"""Phase 1c: 两道闸单元测试. 真 PDF 文本片段作 fixture, 不 mock."""
import sys
from pathlib import Path

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest.verify import (
    check_anti_fabrication,
    check_field_type,
    check_quote,
    check_rolling,
    _prev_yms,
)

# GCI 2026-05 首页 PyMuPDF 抽取的真实文本片段
GCI_2026_05_TEXT = """FUND PERFORMANCE
1 Mth  3 Mth  6 Mth  1 Yr  3 Yr Ann  5 Yr Ann  Incep Ann
NTA Net Return (%)
0.65
1.85
3.65
7.87
5.14
4.09
3.98
"""

# GCI 2019-02 首页 (旧格式, 12mo dash)
GCI_2019_02_TEXT = """FUND PERFORMANCE
1 Mth  3 Mth  6 Mth  1 Yr  Incep Ann
Net Return (%)
0.42
1.37
2.68
–
4.57
"""


# ---------- check_quote ----------

def test_quote_ok_new_format():
    q = "NTA Net Return (%) 0.65 1.85 3.65 7.87"
    r = check_quote(q, GCI_2026_05_TEXT, 0.0065)
    assert r.passed, r.reason


def test_quote_ok_old_format_with_dash():
    q = "Net Return (%) 0.42 1.37 2.68 – 4.57"
    r = check_quote(q, GCI_2019_02_TEXT, 0.0042)
    assert r.passed, r.reason


def test_quote_empty_fails():
    r = check_quote("", GCI_2026_05_TEXT, 0.0065)
    assert not r.passed
    assert r.reason == "empty_quote"


def test_quote_not_in_pdf_fails():
    """模型编了一个 PDF 里不存在的引用."""
    q = "NTA Net Return (%) 99.99 88.88"
    r = check_quote(q, GCI_2026_05_TEXT, 0.9999)
    assert not r.passed
    assert r.reason == "quote_not_in_pdf"


def test_quote_present_but_value_not_in_quote_fails():
    """quote 是 PDF 真实子串, 但 net_return 说 5.14 (那是 3yr ann 那行)."""
    q = "NTA Net Return (%) 0.65 1.85 3.65 7.87"
    r = check_quote(q, GCI_2026_05_TEXT, 0.0514)
    assert not r.passed
    assert "value_" in r.reason


def test_quote_flattens_whitespace():
    """PyMuPDF 每行独立, quote 是横向拼接. 归一化后应能匹配."""
    pdf_text = "NTA Net Return (%)\n0.65\n1.85\n3.65"
    quote = "NTA Net Return (%) 0.65 1.85 3.65"
    r = check_quote(quote, pdf_text, 0.0065)
    assert r.passed


def test_quote_negative_value():
    """括号负数或直接 -0.5 都要能通过 (前提是 quote 里字面有)."""
    pdf_text = "Net Return (%) -0.50 -1.20 -2.10"
    quote = "Net Return (%) -0.50 -1.20 -2.10"
    r = check_quote(quote, pdf_text, -0.005)
    assert r.passed


# ---------- check_rolling ----------

def test_rolling_no_rolling_reported_passes_with_zero_windows():
    """无 rolling -> pass, verify_windows=0."""
    r = check_rolling(0.0065, "2026-05", {}, {"1mo": None, "3mo": None, "6mo": None, "12mo": None})
    assert r.passed
    assert r.windows_verified == 0
    assert r.reason == "no_rolling_reported"


def test_rolling_insufficient_history_passes_with_zero():
    """有 rolling 但无前月历史 -> pass, windows=0."""
    r = check_rolling(0.0065, "2026-05", {}, {"3mo": 1.85, "6mo": 3.65, "12mo": 7.87})
    assert r.passed
    assert r.windows_verified == 0
    assert r.reason == "insufficient_history"


def test_rolling_3mo_matches():
    """3mo 复利对上 -> pass. GCI 2026-05: 0.65 + 前2月 -> 3mo=1.85."""
    history = {"2026-04": 0.0060, "2026-03": 0.0059}
    # (1.0065)*(1.006)*(1.0059) - 1 = 0.018461 -> 1.8461%. 3mo pdf=1.85. tol 0.5% 内.
    r = check_rolling(0.0065, "2026-05", history, {"3mo": 1.85})
    assert r.passed
    assert r.windows_verified == 1


def test_rolling_mismatch_fails():
    """3mo 明显对不上 (模型摘的是季度滚动误当月度) -> 挡."""
    history = {"2026-04": 0.0060, "2026-03": 0.0059}
    # 若 net_return=0.018 (季度值误当月度), 3mo 复利变成 3%+, 与 pdf 1.85 差 >0.5%.
    r = check_rolling(0.018, "2026-05", history, {"3mo": 1.85})
    assert not r.passed
    assert "mismatch_3mo" in r.reason


def test_rolling_not_found_passes():
    r = check_rolling(None, "2026-05", {}, {"3mo": 1.85})
    assert r.passed


# ---------- _prev_yms ----------

def test_prev_yms_wraps_year():
    assert _prev_yms("2020-02", 3) == ["2020-01", "2019-12", "2019-11"]


def test_prev_yms_zero():
    assert _prev_yms("2026-05", 0) == []


# ---------- check_field_type ----------

def test_field_type_ok():
    assert check_field_type(0.0065)[0]


def test_field_type_rejects_annualized_error():
    """|net_return| >= 0.5 十进制 = 50% -> 多半年化误当月度."""
    ok, reason = check_field_type(0.65)  # 65% 月度 = 不可能
    assert not ok
    assert "out_of_range" in reason


def test_field_type_negative_ok():
    assert check_field_type(-0.05)[0]


# ---------- check_anti_fabrication ----------

def test_antifab_normal_passes():
    ok, _ = check_anti_fabrication(0.005, "2026-05", [("2026-04", 0.004), ("2026-03", 0.006)])
    assert ok


def test_antifab_rejects_run_of_3():
    """连续 3 个相同非零值 = 幻觉 backfill 特征."""
    hist = [("2026-04", 0.005), ("2026-03", 0.005)]
    ok, reason = check_anti_fabrication(0.005, "2026-05", hist)
    assert not ok
    assert "identical_run_3" in reason


def test_antifab_zero_run_allowed():
    """零值可以连续 (基金停跌成立), 不挡."""
    hist = [("2026-04", 0.0), ("2026-03", 0.0)]
    ok, _ = check_anti_fabrication(0.0, "2026-05", hist)
    assert ok


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
