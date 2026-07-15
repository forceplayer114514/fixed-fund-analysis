"""lib/candidates.py 单元测试。

不测 PDF,直接对文本样例断言候选池。真实 PDF 回归留在 Phase 3 test_solver_regression。
"""
from __future__ import annotations

import pytest

from lib.candidates import (
    CANDIDATE_PATTERNS,
    ReturnCandidate,
    extract_pdf_one_candidates,
    extract_rolling_any,
    generate_candidates,
    pattern_bentham,
    pattern_gci_label,
    pattern_kkc_net,
    pattern_kkc_nta,
    pattern_perf_table_1mo,
    pattern_returned,
)


# --- pattern_returned (generic finditer) ---
def test_pattern_returned_single_match():
    text = "The fund returned 0.53% in April."
    cands = pattern_returned(text)
    assert len(cands) == 1
    assert cands[0].value == pytest.approx(0.0053)
    assert cands[0].pattern_tag == "returned_pct"
    assert cands[0].priority == 1
    assert cands[0].ambiguous_context is False


def test_pattern_returned_negative_value():
    text = "The fund returned -0.26% in a difficult month."
    cands = pattern_returned(text)
    assert len(cands) == 1
    assert cands[0].value == pytest.approx(-0.0026)


def test_pattern_returned_multiple_matches():
    """finditer 全部匹配,不再首匹配。"""
    text = "The fund returned 0.72%, while the benchmark returned 0.35%."
    cands = pattern_returned(text)
    assert len(cands) == 2
    vals = sorted(c.value for c in cands)
    assert vals[0] == pytest.approx(0.0035)
    assert vals[1] == pytest.approx(0.0072)


def test_pattern_returned_benchmark_context_flags_ambiguous():
    """benchmark 上下文 -> ambiguous_context=True(双匹配都标)。"""
    text = "The fund returned 0.72%, outperforming the benchmark which returned 0.35%."
    cands = pattern_returned(text)
    assert len(cands) == 2
    for c in cands:
        assert c.ambiguous_context is True


def test_pattern_returned_no_match():
    assert pattern_returned("No returns mentioned here.") == []
    assert pattern_returned("") == []


# --- pattern_bentham ---
def test_pattern_bentham_modern_format():
    text = "The Fund had a total return (after fees) of 0.53% in April 2025."
    cands = pattern_bentham(text)
    assert len(cands) == 1
    assert cands[0].value == pytest.approx(0.0053)
    assert cands[0].pattern_tag == "total_return_after_fees"
    assert cands[0].priority == 0


def test_pattern_bentham_legacy_percent_word():
    text = "The Fund had a total return (after fees*) of 0.30 percent."
    cands = pattern_bentham(text)
    assert len(cands) == 1
    assert cands[0].value == pytest.approx(0.003)


# --- pattern_kkc_net / pattern_kkc_nta ---
def test_pattern_kkc_net():
    text = "Total Return (Net) 0.85% 2.55% 5.10%"
    cands = pattern_kkc_net(text)
    assert len(cands) == 1
    assert cands[0].value == pytest.approx(0.0085)
    assert cands[0].pattern_tag == "total_returns_net"


def test_pattern_kkc_nta():
    text = "Net Return Based on NTA (%) 0.72% details"
    cands = pattern_kkc_nta(text)
    assert len(cands) == 1
    assert cands[0].value == pytest.approx(0.0072)
    assert cands[0].pattern_tag == "net_return_nta_row"


# --- pattern_gci_label (行定位) ---
def test_pattern_gci_label():
    text = "NTA Net Return (%)\n0.53\n1.20\n2.30\nDistribution\n"
    cands = pattern_gci_label(text)
    assert len(cands) == 1
    assert cands[0].value == pytest.approx(0.0053)
    assert cands[0].pattern_tag == "nta_net_return_label"
    assert "NTA Net Return" in cands[0].source_quote


def test_pattern_gci_label_no_valid_next_line():
    text = "NTA Net Return (%)\nDistribution Report\n"
    assert pattern_gci_label(text) == []


def test_pattern_gci_label_old_format_no_nta_prefix():
    """2023-02 前旧格式用 'Net Return (%)'(无 NTA 前缀),同样命中 1mo 值。"""
    text = "Net Return (%)\n0.42\n1.37\n2.68\n–\n4.57\n"
    cands = pattern_gci_label(text)
    assert len(cands) == 1
    assert cands[0].value == pytest.approx(0.0042)
    assert cands[0].pattern_tag == "nta_net_return_label"
    assert "Net Return" in cands[0].source_quote


def test_pattern_gci_label_excludes_kkc_and_excess_labels():
    """精确正则不误匹配 KKC 'Net Return Based on NTA' 或 'Net Excess Return'。
    两者含 'Net Return' 子串但非 GCI 表标签格式。"""
    # KKC 标签(下一行是纯数字,确保是正则而非下一行守卫排除它)
    assert pattern_gci_label("Net Return Based on NTA (%)\n0.72\n") == []
    # Net Excess Return 行(GCI 表内同行,与 Net Return 形似)
    assert pattern_gci_label("Net Excess Return (%)\n0.30\n1.00\n") == []


def test_pattern_gci_label_footnote_marker():
    """2022-08~12 月报标签带脚注 'Net Return2 (%)',正则允许 Return 后脚注数字。"""
    text = "Net Return2 (%)\n0.60\n1.78\n3.16\n"
    cands = pattern_gci_label(text)
    assert len(cands) == 1
    assert cands[0].value == pytest.approx(0.0060)
    assert "Net Return2" in cands[0].source_quote


def test_pattern_gci_label_parens_negative():
    """会计括号负数 '(0.45)' = -0.45%(如 COVID 2020-03 砸盘月)。"""
    text = "Net Return (%)\n(0.45)\n0.27\n1.46\n"
    cands = pattern_gci_label(text)
    assert len(cands) == 1
    assert cands[0].value == pytest.approx(-0.0045)
    assert "(0.45)" in cands[0].source_quote


# --- pattern_perf_table_1mo ---
def test_pattern_perf_table_1mo_hits_when_table_parsed():
    """extract_perf_rolling 能解出 1mo 时,perf_table_1mo 产一个候选。"""
    # 构造一个 Stake 风格 performance 表(header + Class A 行)
    text = (
        "Some commentary above.\n"
        "Performance 1 month 3 months 6 months 12 months since inception\n"
        "Class A 0.53% 1.60% 3.10% 6.20% 15.40%\n"
        "More text.\n"
    )
    cands = pattern_perf_table_1mo(text)
    assert len(cands) == 1
    assert cands[0].value == pytest.approx(0.0053)
    assert cands[0].pattern_tag == "perf_table_1mo"
    assert cands[0].priority == 2


def test_pattern_perf_table_1mo_none_when_table_missing():
    """无 performance 表 -> 空。"""
    assert pattern_perf_table_1mo("just commentary text") == []


# --- generate_candidates:多模式聚合 ---
def test_generate_candidates_combines_multiple_patterns():
    """同文本触发 returned_pct + total_return_after_fees + perf_table_1mo。"""
    text = (
        "The Fund had a total return (after fees) of 0.53%. "
        "The Fund returned 0.72% while the benchmark returned 0.35%.\n"
        "Performance 1 month 3 months 6 months 12 months since inception\n"
        "Class A 0.53% 1.60% 3.10% 6.20% 15.40%\n"
    )
    cands = generate_candidates(text)
    tags = {c.pattern_tag for c in cands}
    # 至少含这三个模式来源
    assert "total_return_after_fees" in tags
    assert "returned_pct" in tags
    assert "perf_table_1mo" in tags


def test_generate_candidates_empty_text():
    assert generate_candidates("") == []


def test_generate_candidates_preserves_multiple_sources_same_value():
    """同值不同来源保留全部候选(展示时能列全部原文)。"""
    text = (
        "The Fund returned 0.53%.\n"
        "Performance 1 month 3 months 6 months 12 months since inception\n"
        "Class A 0.53% 1.60% 3.10% 6.20% 15.40%\n"
    )
    cands = generate_candidates(text)
    same_val = [c for c in cands if abs(c.value - 0.0053) < 1e-9]
    tags = {c.pattern_tag for c in same_val}
    # returned_pct + perf_table_1mo 都在池中(不去重掉)
    assert "returned_pct" in tags
    assert "perf_table_1mo" in tags


# --- CANDIDATE_PATTERNS 注册表结构 ---
def test_candidate_patterns_registry_shape():
    """6 条模式,priority 各就位。"""
    tags = [t for t, _, _ in CANDIDATE_PATTERNS]
    assert tags == [
        "total_return_after_fees", "total_returns_net", "net_return_nta_row",
        "nta_net_return_label", "returned_pct", "perf_table_1mo",
    ]
    # 专属模式 priority=0,generic=1,表格 1mo=2
    prios = {t: p for t, p, _ in CANDIDATE_PATTERNS}
    assert prios["returned_pct"] == 1
    assert prios["perf_table_1mo"] == 2
    assert prios["total_return_after_fees"] == 0


# --- extract_rolling_any:三提取器串行 ---
def test_extract_rolling_any_perf_hits_first():
    """Stake 风格 perf 表可用 -> extractor_tag='perf'。"""
    text = (
        "Performance 1 month 3 months 6 months 12 months since inception\n"
        "Class A 0.53% 1.60% 3.10% 6.20% 15.40%\n"
    )
    r = extract_rolling_any(text)
    assert r["parse_error"] is False
    assert r["extractor_tag"] == "perf"
    assert r["1mo"] == pytest.approx(0.0053)
    assert r["3mo"] == pytest.approx(0.016)
    assert "precision" in r


def test_extract_rolling_any_falls_back_to_bentham():
    """perf 表不可用 + Bentham 'Total return (after fees)' 行 -> extractor_tag='bentham'。"""
    text = (
        "Total return (after fees) 0.53 1.60 3.10 6.20 Benchmark 0.20 0.60"
    )
    r = extract_rolling_any(text)
    assert r["parse_error"] is False
    assert r["extractor_tag"] == "bentham"
    assert r["1mo"] == pytest.approx(0.0053)


def test_extract_rolling_any_all_fail():
    """无任何 rolling 表 -> parse_error=True,extractor_tag='none'。"""
    r = extract_rolling_any("Just commentary text, no table.")
    assert r["parse_error"] is True
    assert r["extractor_tag"] == "none"
    assert "precision" in r


def test_extract_rolling_any_precision_detection():
    """precision = text 中数值最小小数位数。"""
    # 只有 1 位小数
    r = extract_rolling_any("Values: 1.5 2.7 3.1")
    assert r["precision"] == 1
    # 有 2 位小数
    r2 = extract_rolling_any("Values: 1.50 2.75")
    assert r2["precision"] == 2


# --- extract_pdf_one_candidates ---
def test_extract_pdf_one_candidates_missing_pdf_returns_empty():
    cands, rolling = extract_pdf_one_candidates("/no/such/file.pdf")
    assert cands == []
    assert rolling["parse_error"] is True
    assert rolling["extractor_tag"] == "none"
