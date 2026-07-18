"""Spec E verify.py 单测.

覆盖:
  - check_quote_tokens: 空 quote / 空 doc / token 不在 quote / 不在 doc / 全过
  - _flatten: PyMuPDF 空格怪癖归一化
  - check_rolling: 十进制单位, 缺失即放行, 有值+历史充足 → 对上/对不上
  - check_field_type: |v| < 0.5 十进制
  - check_anti_fabrication: 连续 3 个非零同值判 fail
  - _prev_yms: 跨年
"""
import sys
from pathlib import Path

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest.verify import (
    _flatten,
    _prev_yms,
    check_anti_fabrication,
    check_field_type,
    check_quote_tokens,
    check_rolling,
)


# ---------- _flatten (PyMuPDF 空格归一化) ----------

def test_flatten_percent_space():
    assert _flatten("0.65 %") == "0.65%"


def test_flatten_dollar_space():
    assert _flatten("$ 1023") == "$1023"


def test_flatten_paren_space():
    assert _flatten("( 0.45 )") == "(0.45)"


def test_flatten_dot_space():
    assert _flatten("0. 65") == "0.65"


def test_flatten_comma_space():
    assert _flatten("1, 023") == "1,023"


def test_flatten_minus_space():
    assert _flatten("- 0.45") == "-0.45"


def test_flatten_multi_whitespace():
    assert _flatten("A   B\n\tC") == "A B C"


def test_flatten_empty():
    assert _flatten("") == ""


# ---------- check_quote_tokens: 空 case ----------

def test_no_tokens_passes_not_found():
    """not_found 场景 tokens 为空, 视 pass."""
    r = check_quote_tokens([], "", "")
    assert r.passed
    assert r.reason == "no_tokens_to_check"


def test_empty_source_quote_fails():
    r = check_quote_tokens(["0.65%"], "", "doc has 0.65%")
    assert not r.passed
    assert "empty_source_quote" in r.reason


def test_empty_doc_text_fails():
    r = check_quote_tokens(["0.65%"], "quote 0.65%", "")
    assert not r.passed
    assert "empty_doc_text" in r.reason


# ---------- check_quote_tokens: 正常 ----------

def test_all_tokens_match_passes():
    tokens = ["0.65%", "1.85%"]
    quote = "Net Return (%) 0.65 1.85"  # 归一化后含 0.65% 1.85% 吗? 否 — 数字与 % 之间要相邻
    # 更好: 用真实 quote 格式
    quote = "Net Return (%): 0.65%, 3M: 1.85%"
    doc = "Fund Report ... Net Return (%): 0.65%, 3M: 1.85% ..."
    r = check_quote_tokens(tokens, quote, doc)
    assert r.passed, r.reason


def test_token_missing_from_quote_fails():
    """LLM 编 quote (缺 token)."""
    tokens = ["0.65%"]
    r = check_quote_tokens(tokens, "quote without value", "doc has 0.65%")
    assert not r.passed
    assert "token_not_in_quote" in r.reason


def test_token_missing_from_doc_fails():
    """LLM 编数据 (quote 里有 token 但 doc 没有 — 幻觉)."""
    tokens = ["0.65%"]
    r = check_quote_tokens(tokens, "quote 0.65%", "doc has 9.99%")
    assert not r.passed
    assert "token_not_in_doc" in r.reason


def test_pymupdf_space_tolerated():
    """PDF 抽的 doc_text 常带空格, 归一化后应匹配."""
    tokens = ["0.65%"]
    quote = "Net Return: 0.65%"
    doc = "Net Return :\n0. 65 %"  # PyMuPDF 常见空格
    r = check_quote_tokens(tokens, quote, doc)
    assert r.passed, r.reason


def test_nav_pair_tokens_both_in_quote():
    """nav_pair 场景 2 NAV 都在 quote 里."""
    tokens = ["$134.24", "$135.16"]
    quote = "FRHY<br />2026-05-31: $134.24, FRHY<br />2026-06-30: $135.16"
    doc = quote  # HTML 直接就是 doc
    r = check_quote_tokens(tokens, quote, doc)
    assert r.passed


# ---------- check_rolling: 十进制 ----------

def test_rolling_no_report_passes_zero_windows():
    r = check_rolling(0.0065, "2026-05", {}, {})
    assert r.passed
    assert r.windows_verified == 0
    assert r.reason == "no_rolling_reported"


def test_rolling_all_none_passes():
    r = check_rolling(0.0065, "2026-05", {}, {"1mo": None, "3mo": None})
    assert r.passed
    assert r.windows_verified == 0


def test_rolling_insufficient_history_passes():
    r = check_rolling(0.0065, "2026-05", {}, {"3mo": 0.0185})
    assert r.passed
    assert r.windows_verified == 0
    assert r.reason == "insufficient_history"


def test_rolling_3mo_matches_decimal():
    """(1.0065) * (1.006) * (1.0059) - 1 ≈ 0.018463; report 0.0185 内 tol."""
    history = {"2026-04": 0.0060, "2026-03": 0.0059}
    r = check_rolling(0.0065, "2026-05", history, {"3mo": 0.0185})
    assert r.passed
    assert r.windows_verified == 1


def test_rolling_mismatch_fails():
    history = {"2026-04": 0.0060, "2026-03": 0.0059}
    # net=0.018 (季度当月度), 3mo 复利 ≈ 3%, 与报 0.0185 差 >0.5%
    r = check_rolling(0.018, "2026-05", history, {"3mo": 0.0185})
    assert not r.passed
    assert "mismatch_3mo" in r.reason


def test_rolling_not_found_passes():
    r = check_rolling(None, "2026-05", {}, {"3mo": 0.0185})
    assert r.passed


# ---------- _prev_yms ----------

def test_prev_yms_wraps_year():
    assert _prev_yms("2020-02", 3) == ["2020-01", "2019-12", "2019-11"]


def test_prev_yms_zero():
    assert _prev_yms("2026-05", 0) == []


# ---------- check_field_type ----------

def test_field_type_ok():
    assert check_field_type(0.0065)[0]


def test_field_type_rejects_annualized():
    ok, reason = check_field_type(0.65)  # 65% 月度 = 不可能
    assert not ok
    assert "out_of_range" in reason


def test_field_type_negative_ok():
    assert check_field_type(-0.05)[0]


def test_field_type_none_ok():
    assert check_field_type(None)[0]


# ---------- check_anti_fabrication ----------

def test_antifab_normal_passes():
    ok, _ = check_anti_fabrication(0.005, "2026-05",
                                   [("2026-04", 0.004), ("2026-03", 0.006)])
    assert ok


def test_antifab_rejects_run_of_3():
    hist = [("2026-04", 0.005), ("2026-03", 0.005)]
    ok, reason = check_anti_fabrication(0.005, "2026-05", hist)
    assert not ok
    assert "identical_run_3" in reason


def test_antifab_zero_run_allowed():
    hist = [("2026-04", 0.0), ("2026-03", 0.0)]
    ok, _ = check_anti_fabrication(0.0, "2026-05", hist)
    assert ok


def test_antifab_ignores_history_not_adjacent_to_ym():
    """回填场景: DB 已有全库最新月, 但当前 ym 是很久以前的历史月.

    hist 里 2026-05/2026-04 是同值 run, 但离 ym=2020-05 十万八千里,
    不应污染 2020-05 的连续性判定 -- 应看 2020-04/2020-03 是否同值。
    """
    hist = [("2026-05", 0.0042), ("2026-04", 0.0042), ("2026-03", 0.0042)]
    ok, _ = check_anti_fabrication(0.0042, "2020-05", hist)
    assert ok


def test_antifab_rejects_run_of_3_adjacent_to_ym_regardless_of_input_order():
    """recent_history 乱序传入 (非 sorted reverse) 也要能挡住紧邻 ym 的同值 run。"""
    hist = [("2019-11", 0.006), ("2020-04", 0.005), ("2020-03", 0.005)]
    ok, reason = check_anti_fabrication(0.005, "2020-05", hist)
    assert not ok
    assert "identical_run_3" in reason


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
