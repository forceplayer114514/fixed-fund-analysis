"""RBA 官方现金利率抓取与入库。

单一权威源: RBA 官方 "Cash Rate Target" 页面 (`/statistics/cash-rate/`)，
逐行记录每次议息会议决议后的 Effective Date（生效日期，已是官方定义的
"决议次日生效"口径，不是公告当天）+ 当时的目标利率，覆盖 1990 年至今。

2026-07-19 替换掉原来的 DBnomics 第三方 API：该 API 序列只从 2011-01
开始，早于此的基金数据（如 yarra/bentham，2003/2004 年起）永远查不到
RBA 基准，被 resolve_rf_rates 判 rba_missing 剔除出超额收益计算——是
当时发现的真实覆盖缺口。改用 RBA 官网自身这张表，一次抓取同时覆盖
"当前利率"和"历史序列"，不再依赖第三方聚合站，也不会再有这个缺口
（只要基金起始月份不早于 1990 年 1 月）。
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.models import RbaCashRate

CASH_RATE_TARGET_URL = "https://www.rba.gov.au/statistics/cash-rate/"


def fetch_rba_rate_history(timeout: int = 15) -> list[tuple[str, float]]:
    """抓官方 Cash Rate Target 表，返回按生效日期升序的 [(YYYY-MM-DD, 年化小数), ...]。

    早于 1990 年代中期的极少数行用区间值记录利率（如 "15.00 to 15.50"），
    早于本项目所有基金的数据起点，直接跳过，不猜测取哪一侧。
    """
    resp = requests.get(CASH_RATE_TARGET_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if not table:
        raise ValueError("RBA Cash Rate Target 页面未找到数据表")

    out: list[tuple[str, float]] = []
    for row in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) < 3:
            continue
        try:
            eff_date = datetime.strptime(cells[0], "%d %b %Y").date()
        except ValueError:
            continue  # 表头 / Legend 行
        if not re.fullmatch(r"[0-9]+\.[0-9]+", cells[2]):
            continue  # 区间值行，跳过
        out.append((eff_date.isoformat(), float(cells[2]) / 100.0))
    out.sort(key=lambda x: x[0])
    return out


def expand_to_monthly(
    history: list[tuple[str, float]], through_month: Optional[str] = None,
) -> dict[str, float]:
    """把生效日期变更点序列展开成逐月按天加权平均利率字典 {YYYY-MM: 年化小数}。

    口径：利率决议次日生效即时生效（不是等到月底），某月内若发生一次或多次
    变动，该月的月基准利率 = 按实际生效天数加权平均，而不是简单取月末口径
    （那样会把决议日之前、当月仍适用旧利率的天数也算成新利率，掩盖决议当月
    的真实变动，2026-07-19 由用户发现并核实修正，见 memory）。
    无变动的月份（多数月份）两种口径结果恒等——加权平均退化为常数。
    through_month 默认到当前自然月；history 为空返回空字典。
    """
    if not history:
        return {}
    history = sorted(history, key=lambda x: x[0])
    end = through_month or datetime.now().strftime("%Y-%m")
    ey, em = int(end[:4]), int(end[5:7])
    end_date = date(ey, em, monthrange(ey, em)[1])

    out: dict[str, float] = {}
    idx = 0
    current_rate = history[0][1]
    month_key: Optional[str] = None
    day_sum = 0.0
    day_count = 0
    d = date.fromisoformat(history[0][0])
    while d <= end_date:
        while idx < len(history) and date.fromisoformat(history[idx][0]) <= d:
            current_rate = history[idx][1]
            idx += 1
        key = f"{d.year:04d}-{d.month:02d}"
        if key != month_key:
            if month_key is not None:
                out[month_key] = day_sum / day_count
            month_key, day_sum, day_count = key, 0.0, 0
        day_sum += current_rate
        day_count += 1
        d += timedelta(days=1)
    if month_key is not None:
        out[month_key] = day_sum / day_count
    return out


def _next_month(month_key: str) -> str:
    y, m = int(month_key[:4]), int(month_key[5:7])
    m += 1
    if m > 12:
        m = 1
        y += 1
    return f"{y:04d}-{m:02d}"


def group_rate_periods(rates: dict[str, float]) -> list[dict]:
    """把逐月利率字典按连续相同值合并成区间，供"历史利率"页展示用
    （如 2026-01~2026-06 4.50%），不需要精确到每个月。"""
    periods: list[dict] = []
    for month_key in sorted(rates.keys()):
        rate = rates[month_key]
        if (
            periods
            and periods[-1]["rate"] == rate
            and _next_month(periods[-1]["end_month"]) == month_key
        ):
            periods[-1]["end_month"] = month_key
        else:
            periods.append({"start_month": month_key, "end_month": month_key, "rate": rate})
    return periods


def upsert_rba_rates(session: Session, rates: dict[str, float]) -> int:
    """将利率字典写入 rba_cash_rates 表（重复主键覆盖），返回新增条数。"""
    count = 0
    for month_key, rate in rates.items():
        existing = session.get(RbaCashRate, month_key)
        if existing:
            existing.rate = rate
        else:
            session.add(RbaCashRate(date_period=month_key, rate=rate))
            count += 1
    session.commit()
    return count
