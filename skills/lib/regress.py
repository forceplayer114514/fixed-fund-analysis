"""golden 回归:用 pdf_cache 全量缓存重跑 solver,以库内已入库值为基线做 diff。

设计动机:extend_extractor 改 lib/candidates.py 加候选模式后,唯一可信的
"没有引入回归"验证不是人肉抽查几个基金,而是对全库缓存 PDF 重新求解一遍,
跟 monthly_returns 表里已有的值逐月比对。库内值本身就是历史每次入库时已过
gate_check/solver 数学准入的真值,天然就是基线,不需要额外维护 fixture 文件
(GCI 一役全格式变体天然覆盖,是最强回归样本)。

只读:不需要 FUND_DB_WRITE_TOKEN,不发任何网络请求,只读 pdf_cache 目录下
已下载的 PDF(命中文本缓存则免重新 fitz 解析,见 lib/extract.py parse_pdf_text)。

三类结果:
- value_drift:这次 resolved 值与库内值不同(改 pattern 动了旧月的选择)
- coverage_regression:库内有值,这次却 no_candidates/pending(能力退化)
- new_capability:库内没有该月(此前从未入库/pending),这次 resolved
  (仅提示,不算回归 -- 说明新 pattern 补上了之前提取不到的月)
"""
from __future__ import annotations

import glob
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from lib.candidates import extract_pdf_one_candidates
from lib.db import get_fund, get_monthly_returns, list_funds
from lib.extract import pdf_cache_dir
from lib.solver import MonthInput, solve_series

# value_drift 判定容差:略大于 float 表示误差,严格小于 solver 自身的选择/验证
# 容差(SELECT_TOL/VERIFY_TOL 最小 5e-4 量级),避免浮点噪声误报为漂移。
_DRIFT_TOL = 1e-6


@dataclass
class FundRegressResult:
    fund_id: str
    months_checked: int
    value_drift: list[dict] = field(default_factory=list)
    coverage_regression: list[dict] = field(default_factory=list)
    new_capability: list[dict] = field(default_factory=list)

    @property
    def has_regression(self) -> bool:
        return bool(self.value_drift or self.coverage_regression)


@dataclass
class RegressReport:
    funds: list[FundRegressResult] = field(default_factory=list)

    @property
    def has_regression(self) -> bool:
        return any(f.has_regression for f in self.funds)

    def to_dict(self) -> dict:
        return {
            "has_regression": self.has_regression,
            "funds": [
                {
                    "fund_id": f.fund_id,
                    "months_checked": f.months_checked,
                    "value_drift": f.value_drift,
                    "coverage_regression": f.coverage_regression,
                    "new_capability": f.new_capability,
                }
                for f in self.funds
            ],
        }


def _cached_months(fund_id: str) -> list[str]:
    """列出该基金 pdf_cache 目录下已缓存的 YYYY-MM(按 .pdf 文件名,升序)。"""
    cache_dir = pdf_cache_dir(fund_id)
    yms: list[str] = []
    for path in sorted(glob.glob(os.path.join(cache_dir, "*.pdf"))):
        ym = os.path.splitext(os.path.basename(path))[0]
        if len(ym) == 7 and ym[4] == "-":
            yms.append(ym)
    return yms


def run_fund_regression(
    conn: sqlite3.Connection, fund_id: str,
) -> FundRegressResult:
    """单基金:pdf_cache 全量缓存月份重新求解,与 monthly_returns 已入库值 diff。

    未缓存任何 PDF(如 add-table/add-plotly 非 PDF 源基金)-> months_checked=0,
    三类列表皆空,不算回归(该基金本就不在本机制覆盖范围)。
    """
    yms = _cached_months(fund_id)
    result = FundRegressResult(fund_id=fund_id, months_checked=len(yms))
    if not yms:
        return result

    fund = get_fund(conn, fund_id)
    max_pages = fund.get("max_pdf_pages") if fund else None
    cache_dir = pdf_cache_dir(fund_id)

    month_inputs: list[MonthInput] = []
    for ym in yms:
        pdf_path = os.path.join(cache_dir, f"{ym}.pdf")
        cands, rolling = extract_pdf_one_candidates(pdf_path, max_pages=max_pages)
        month_inputs.append(MonthInput(ym=ym, candidates=cands, rolling=rolling))

    resolutions = solve_series(month_inputs)
    existing_rows = get_monthly_returns(conn, fund_id)
    existing = {r["date"][:7]: r["net_return"] for r in existing_rows}

    for ym in yms:
        r = resolutions[ym]
        in_db = ym in existing
        if r.status == "resolved":
            new_val = r.chosen.value
            if in_db:
                old_val = float(existing[ym])
                if abs(new_val - old_val) > _DRIFT_TOL:
                    result.value_drift.append({
                        "ym": ym, "db_value": old_val, "regress_value": new_val,
                        "source_quote": r.chosen.source_quote,
                    })
            else:
                result.new_capability.append({
                    "ym": ym, "regress_value": new_val,
                })
        else:
            if in_db:
                result.coverage_regression.append({
                    "ym": ym, "db_value": float(existing[ym]), "status": r.status,
                })
    return result


def run_regression(
    conn: sqlite3.Connection, fund_id: Optional[str] = None,
) -> RegressReport:
    """全库(或单基金,fund_id 指定时)回归:遍历 pdf_cache 已缓存月份重新求解。"""
    if fund_id is not None:
        fund_ids = [fund_id]
    else:
        fund_ids = [f["fund_id"] for f in list_funds(conn)]

    report = RegressReport()
    for fid in fund_ids:
        report.funds.append(run_fund_regression(conn, fid))
    return report
