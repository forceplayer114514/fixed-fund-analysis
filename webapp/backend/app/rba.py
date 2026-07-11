"""RBA 官方现金利率抓取与入库。移植自 scripts/metrics.py 的 fetch_rba_cash_rate / fetch_historical_cash_rates。"""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RbaCashRate


def fetch_current_rba_rate() -> float:
    """从 RBA 首页抓取当前官方现金利率（年化小数，如 0.0435）。"""
    resp = requests.get(settings.RBA_BASE_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # 在页面文本中查找 "Cash rate target" 字样
    h = soup.find(string=lambda x: x and "Cash rate target" in x)
    if not h:
        raise ValueError("RBA 页面未找到 'Cash rate target' 文本")
    parent = h.find_parent("article") or h.find_parent("div")
    if not parent:
        raise ValueError("未找到 'Cash rate target' 的父容器")
    val_el = parent.find(class_="statistic-value")
    if not val_el:
        raise ValueError("未找到 class='statistic-value' 元素")

    match = re.search(r"[0-9.]+", val_el.text.strip())
    if not match:
        raise ValueError(f"无法从文本解析利率数值: '{val_el.text}'")
    return float(match.group(0)) / 100.0


def fetch_historical_rba_rates() -> dict[str, float]:
    """从 DBnomics API 抓取历史逐月现金利率，返回 {YYYY-MM: 年化小数}。"""
    resp = requests.get(settings.RBA_HISTORY_API, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    doc = data["series"]["docs"][0]

    rates = {}
    for period, val in zip(doc["period"], doc["value"]):
        if val == "NA" or val is None:
            continue
        try:
            rates[period[:7]] = float(val) / 100.0  # YYYY-MM -> 年化小数
        except ValueError:
            continue
    return rates


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
