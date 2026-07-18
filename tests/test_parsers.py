"""Spec E parsers.py 单测.

覆盖:
  - _parse_number_token 单位识别 (%, ‰, bps, $, bare + label 兜, unit_hint 兜)
  - 括号负数 / -号 / 千分位剥除
  - _prev_adjacent / _curr_matches_expected 月份逻辑
  - parse_extraction 四种 kind + not_found
  - 月份闸: wrong_month 一律判废
  - _parse_rolling: 缺失 → None, 有值 → 十进制
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest.parsers import (
    ParseResult,
    _curr_matches_expected,
    _parse_number_token,
    _parse_rolling,
    _prev_adjacent,
    _to_decimal_return,
    collect_text_tokens,
    parse_extraction,
)


# ---------- _parse_number_token: 字面符号 ----------

def test_percent_symbol():
    v, u = _parse_number_token("0.65%")
    assert v == Decimal("0.65") and u == "percent"


def test_permille_symbol():
    v, u = _parse_number_token("0.65‰")
    assert v == Decimal("0.65") and u == "permille"


def test_bps_symbol():
    v, u = _parse_number_token("65 bps")
    assert v == Decimal("65") and u == "bps"


def test_dollar_symbol():
    v, u = _parse_number_token("$1.0234")
    assert v == Decimal("1.0234") and u == "dollar"


# ---------- 括号负 / 负号 / 千分位 ----------

def test_paren_negative():
    v, u = _parse_number_token("(0.45)")
    assert v == Decimal("-0.45") and u == "unknown"


def test_paren_with_percent():
    v, u = _parse_number_token("(0.45%)")
    assert v == Decimal("-0.45") and u == "percent"


def test_dash_negative():
    v, u = _parse_number_token("-0.45%")
    assert v == Decimal("-0.45") and u == "percent"


def test_thousands_separator():
    v, u = _parse_number_token("$1,023.40")
    assert v == Decimal("1023.40") and u == "dollar"


def test_thousands_no_dollar():
    v, u = _parse_number_token("1,000,000")
    assert v == Decimal("1000000")


# ---------- label 兜单位 ----------

def test_bare_number_with_percent_label():
    """bare 0.65 + measure_label='Net Return (%)' → percent."""
    v, u = _parse_number_token("0.65", label="Net Return (%)")
    assert v == Decimal("0.65") and u == "percent"


def test_bare_number_with_bps_label():
    v, u = _parse_number_token("65", label="1M (bps)")
    assert v == Decimal("65") and u == "bps"


def test_bare_number_unit_hint_fallback():
    v, u = _parse_number_token("0.65", unit_hint="percent")
    assert v == Decimal("0.65") and u == "percent"


def test_bare_number_no_hint_no_label():
    v, u = _parse_number_token("0.65")
    assert v == Decimal("0.65") and u == "unknown"


def test_literal_symbol_beats_label():
    """字面 % 优先于 label."""
    v, u = _parse_number_token("0.65%", label="NAV ($)")
    assert u == "percent"


def test_empty_token():
    v, u = _parse_number_token("")
    assert v is None


def test_junk_token():
    v, u = _parse_number_token("N/A")
    assert v is None


# ---------- _to_decimal_return ----------

def test_percent_to_decimal():
    assert _to_decimal_return(Decimal("0.65"), "percent") == pytest.approx(0.0065)


def test_permille_to_decimal():
    assert _to_decimal_return(Decimal("6.5"), "permille") == pytest.approx(0.0065)


def test_bps_to_decimal():
    assert _to_decimal_return(Decimal("65"), "bps") == pytest.approx(0.0065)


def test_decimal_passthrough():
    assert _to_decimal_return(Decimal("0.0065"), "decimal") == pytest.approx(0.0065)


def test_dollar_returns_none():
    """NAV 不是收益率, 不能直接当十进制."""
    assert _to_decimal_return(Decimal("1.0234"), "dollar") is None


def test_unknown_returns_none():
    """禁不确定值入库."""
    assert _to_decimal_return(Decimal("0.65"), "unknown") is None


# ---------- 月份逻辑 ----------

def test_prev_adjacent_normal():
    assert _prev_adjacent("2026-05-31", "2026-06-30")


def test_prev_adjacent_year_wrap():
    assert _prev_adjacent("2019-12-31", "2020-01-31")


def test_prev_not_adjacent_two_months():
    assert not _prev_adjacent("2026-04-30", "2026-06-30")


def test_prev_not_adjacent_reverse():
    assert not _prev_adjacent("2026-06-30", "2026-05-31")


def test_prev_adjacent_bad_input():
    assert not _prev_adjacent("", "2026-06-30")
    assert not _prev_adjacent("2026-05-31", "")


def test_curr_matches_expected():
    assert _curr_matches_expected("2026-06-30", "2026-06")
    assert not _curr_matches_expected("2026-05-31", "2026-06")
    assert not _curr_matches_expected("", "2026-06")


# ---------- parse_extraction: 月份闸 (命根子) ----------

def test_month_gate_wrong_ym_rejects():
    """LLM 返 ym=2026-05 但 expected=2026-06 → wrong_month."""
    obj = {"ym": "2026-05", "kind": "table_value", "value_text": "0.65%"}
    r = parse_extraction(obj, "2026-06")
    assert r.net_return is None
    assert "wrong_month" in r.parse_error


def test_month_gate_wrong_curr_date_rejects():
    """kind=nav_pair, ym 对但 curr_date 错月 → wrong_month."""
    obj = {
        "ym": "2026-06", "kind": "nav_pair",
        "prev_date": "2026-04-30", "prev_text": "1.0000",
        "curr_date": "2026-05-31", "curr_text": "1.0100",
    }
    r = parse_extraction(obj, "2026-06")
    assert r.net_return is None
    assert "wrong_month" in r.parse_error


# ---------- parse_extraction: not_found ----------

def test_not_found_returns_none_no_error():
    obj = {"ym": "2026-06", "kind": "not_found", "not_found": True}
    r = parse_extraction(obj, "2026-06")
    assert r.net_return is None
    assert r.parse_error is None


# ---------- parse_extraction: table_value ----------

def test_table_value_with_percent_symbol():
    obj = {"ym": "2026-06", "kind": "table_value", "value_text": "0.65%"}
    r = parse_extraction(obj, "2026-06")
    assert r.net_return == pytest.approx(0.0065)
    assert r.parse_error is None


def test_table_value_bare_with_label():
    obj = {
        "ym": "2026-06", "kind": "table_value",
        "value_text": "0.65", "measure_label": "Net Return (%)",
    }
    r = parse_extraction(obj, "2026-06")
    assert r.net_return == pytest.approx(0.0065)


def test_table_value_bare_no_label_no_hint_rejected():
    """bare 无 label 无 hint → unit_ambiguous, 判 pending."""
    obj = {"ym": "2026-06", "kind": "table_value", "value_text": "0.65"}
    r = parse_extraction(obj, "2026-06")
    assert r.net_return is None
    assert "unit_ambiguous" in r.parse_error


def test_table_value_paren_negative_with_percent():
    obj = {"ym": "2026-06", "kind": "table_value", "value_text": "(0.45%)"}
    r = parse_extraction(obj, "2026-06")
    assert r.net_return == pytest.approx(-0.0045)


def test_table_value_bps():
    obj = {"ym": "2026-06", "kind": "table_value", "value_text": "65 bps"}
    r = parse_extraction(obj, "2026-06")
    assert r.net_return == pytest.approx(0.0065)


def test_table_value_dollar_rejected():
    """$1.0234 是 NAV, 不能当 return."""
    obj = {"ym": "2026-06", "kind": "table_value", "value_text": "$1.0234"}
    r = parse_extraction(obj, "2026-06")
    assert r.net_return is None
    assert "unit_ambiguous" in r.parse_error


# ---------- parse_extraction: commentary_pct (与 table_value 同处理) ----------

def test_commentary_pct_extract():
    obj = {
        "ym": "2026-06", "kind": "commentary_pct",
        "value_text": "0.65% net of fees",
    }
    r = parse_extraction(obj, "2026-06")
    assert r.net_return == pytest.approx(0.0065)


# ---------- parse_extraction: nav_pair ----------

def test_nav_pair_correct_math():
    """1.0165 / 1.0100 - 1 = 0.006435..."""
    obj = {
        "ym": "2026-06", "kind": "nav_pair",
        "prev_date": "2026-05-31", "prev_text": "1.0100",
        "curr_date": "2026-06-30", "curr_text": "1.0165",
    }
    r = parse_extraction(obj, "2026-06")
    assert r.net_return == pytest.approx(0.006435, abs=1e-5)


def test_nav_pair_with_dollar_signs():
    obj = {
        "ym": "2026-06", "kind": "nav_pair",
        "prev_date": "2026-05-31", "prev_text": "$134.24",
        "curr_date": "2026-06-30", "curr_text": "$135.16",
    }
    r = parse_extraction(obj, "2026-06")
    assert r.net_return == pytest.approx(135.16 / 134.24 - 1, abs=1e-6)


def test_nav_pair_not_adjacent_fails():
    """prev=2026-03 curr=2026-06 → 3 月跨度, prev_not_adjacent."""
    obj = {
        "ym": "2026-06", "kind": "nav_pair",
        "prev_date": "2026-03-31", "prev_text": "1.0000",
        "curr_date": "2026-06-30", "curr_text": "1.0300",
    }
    r = parse_extraction(obj, "2026-06")
    assert r.net_return is None
    assert "prev_not_adjacent" in r.parse_error


def test_nav_pair_zero_prev_fails():
    obj = {
        "ym": "2026-06", "kind": "nav_pair",
        "prev_date": "2026-05-31", "prev_text": "0",
        "curr_date": "2026-06-30", "curr_text": "1.0165",
    }
    r = parse_extraction(obj, "2026-06")
    assert r.net_return is None


# ---------- parse_extraction: cum_ex_dist ----------

def test_cum_ex_dist_normal():
    """(1.0100 + 0.0050) / 1.0000 - 1 = 0.0150."""
    obj = {
        "ym": "2026-06", "kind": "cum_ex_dist",
        "prev_date": "2026-05-31", "prev_text": "$1.0000",
        "curr_date": "2026-06-30", "curr_text": "$1.0100",
        "dist_text": "$0.0050",
    }
    r = parse_extraction(obj, "2026-06")
    assert r.net_return == pytest.approx(0.0150, abs=1e-6)


def test_cum_ex_dist_no_dist():
    """dist_text null → 当 0."""
    obj = {
        "ym": "2026-06", "kind": "cum_ex_dist",
        "prev_date": "2026-05-31", "prev_text": "1.0000",
        "curr_date": "2026-06-30", "curr_text": "1.0100",
        "dist_text": None,
    }
    r = parse_extraction(obj, "2026-06")
    assert r.net_return == pytest.approx(0.0100, abs=1e-6)


# ---------- _parse_rolling ----------

def test_rolling_all_missing_returns_all_none():
    obj = {"rolling_text": {}}
    r = _parse_rolling(obj)
    assert all(v is None for v in r.values())


def test_rolling_text_percent_converts_to_decimal():
    obj = {"rolling_text": {"3mo": "1.85%", "6mo": "3.65%", "12mo": "7.87%"}}
    r = _parse_rolling(obj)
    assert r["3mo"] == pytest.approx(0.0185)
    assert r["6mo"] == pytest.approx(0.0365)
    assert r["12mo"] == pytest.approx(0.0787)
    assert r["1mo"] is None


def test_rolling_bare_number_defaults_percent():
    """rolling 语境 bare '1.85' 默认按 % 处理."""
    obj = {"rolling_text": {"3mo": "1.85"}}
    r = _parse_rolling(obj)
    assert r["3mo"] == pytest.approx(0.0185)


def test_rolling_dash_treated_as_null():
    obj = {"rolling_text": {"12mo": "–"}}
    r = _parse_rolling(obj)
    assert r["12mo"] is None


def test_rolling_backcompat_rolling_pct_numeric():
    """兼容旧 rolling_pct 字段, LLM 若返数字直接当百分数处理."""
    obj = {"rolling_pct": {"3mo": 1.85}}
    r = _parse_rolling(obj)
    assert r["3mo"] == pytest.approx(0.0185)


# ---------- collect_text_tokens ----------

def test_collect_tokens_all_present():
    obj = {
        "value_text": "0.65%",
        "prev_text": "$1.0100",
        "curr_text": "$1.0165",
        "dist_text": "$0.005",
    }
    toks = collect_text_tokens(obj)
    assert "0.65%" in toks
    assert "$1.0100" in toks
    assert "$1.0165" in toks
    assert "$0.005" in toks


def test_collect_tokens_null_dist_skipped():
    obj = {"value_text": "0.65%", "dist_text": None}
    toks = collect_text_tokens(obj)
    assert toks == ["0.65%"]


def test_collect_tokens_string_null_skipped():
    obj = {"value_text": "0.65%", "dist_text": "null"}
    toks = collect_text_tokens(obj)
    assert toks == ["0.65%"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
