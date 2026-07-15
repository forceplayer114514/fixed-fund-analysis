"""solver 端到端真实回归测试(integration mark)。

目标:solver dry-run 对 stake/bentham/kkr 已入库基金,resolved 月的值应与 DB
中入库值逐月一致(在 SELECT_TOL 容差内);不一致月须能解释(如旧版无 rolling
表落 unverifiable)。

运行条件:
- 环境变量 FUND_DB_PATH 或默认 <repo>/data/fund_analysis.db 存在
- data/pdf_cache/<fund_id>/YYYY-MM.pdf 已有缓存(先用 legacy discover 抓过一遍
  才有;缓存缺失自动 skip)

用法:
  RUN_SOLVER_REGRESSION=1 python3 -c "import pytest; pytest.main(['tests/test_solver_regression.py','-v'])"

不设 RUN_SOLVER_REGRESSION -> 默认 skip(CI/日常单测不触发)。
"""
from __future__ import annotations

import os
import sqlite3

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.environ.get("FUND_DB_PATH",
                         os.path.join(REPO_ROOT, "data", "fund_analysis.db"))
CACHE_ROOT = os.environ.get("FUND_PDF_CACHE",
                            os.path.join(REPO_ROOT, "data", "pdf_cache"))


def _cache_ready(fund_id: str, min_months: int = 6) -> bool:
    d = os.path.join(CACHE_ROOT, fund_id)
    if not os.path.isdir(d):
        return False
    pdfs = [f for f in os.listdir(d) if f.endswith(".pdf")]
    return len(pdfs) >= min_months


@pytest.mark.skipif(
    not os.environ.get("RUN_SOLVER_REGRESSION"),
    reason="需要 RUN_SOLVER_REGRESSION=1 + 已缓存 PDF; 手动触发",
)
@pytest.mark.parametrize("fund_id", ["stake_accumulate", "bentham_global_income",
                                     "kkr_credit_income_fund"])
def test_solver_matches_existing_db_values(fund_id):
    """对每只 PDF 基金,solver 从 pdf_cache 提取候选 + solve,resolved 月的值
    与 monthly_returns 表中入库值逐月一致(<0.001 相对偏差)。"""
    if not os.path.exists(DB_PATH):
        pytest.skip(f"DB 不存在: {DB_PATH}")
    if not _cache_ready(fund_id):
        pytest.skip(f"PDF 缓存缺失: {os.path.join(CACHE_ROOT, fund_id)}")

    from lib.candidates import extract_pdf_one_candidates
    from lib.solver import MonthInput, solve_series

    # 读现有入库月
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    existing = {
        r["date"][:7]: r["net_return"]
        for r in conn.execute(
            "SELECT date, net_return FROM monthly_returns "
            "WHERE fund_id=? ORDER BY date", (fund_id,)
        ).fetchall()
    }
    conn.close()
    assert len(existing) >= 6, f"{fund_id} 已入库月太少,无法回归"

    # 用缓存的 PDF 跑 solver
    cache_dir = os.path.join(CACHE_ROOT, fund_id)
    months: list = []
    for fn in sorted(os.listdir(cache_dir)):
        if not fn.endswith(".pdf"):
            continue
        ym = fn[:7]
        cands, rolling = extract_pdf_one_candidates(os.path.join(cache_dir, fn))
        months.append(MonthInput(ym=ym, candidates=cands, rolling=rolling))
    resolutions = solve_series(months)

    # 对比:每 resolved 月的 chosen 值 vs existing 值
    mismatched: list[tuple[str, float, float]] = []
    for ym, r in resolutions.items():
        if r.status != "resolved":
            continue
        if ym not in existing:
            continue
        exp = existing[ym]
        got = r.chosen.value
        if abs(got - exp) > 0.001:  # 允许 <0.1% 绝对差
            mismatched.append((ym, exp, got))
    assert not mismatched, (
        f"{fund_id} solver 与 DB 不一致月: {mismatched[:10]}"
        f"(共 {len(mismatched)} 月;<0.1% 内视为一致)"
    )
