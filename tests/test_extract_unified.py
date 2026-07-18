"""Spec E extract_unified prompt + parse_response 单一路径 单测.

覆盖:
  1. table_value PDF/HTML/CSV 三通道走同 schema
  2. nav_pair 场景 (Coolabah Plotly)
  3. cum_ex_dist 场景 (Macquarie CSV)
  4. commentary_pct 场景
  5. 月份闸: LLM 返错月 → parse_error=wrong_month
  6. 单位模糊 bare 无 label → unit_ambiguous
  7. LLM 返非 JSON → parse_error
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest.extract import parse_response


def _mock_llm(payload: dict) -> MagicMock:
    """LLM 返 JSON payload 的 fake resp."""
    r = MagicMock()
    r.text = json.dumps(payload)
    return r


# ---------- table_value ----------

def test_table_value_percent_symbol_correct_month():
    """PDF 表格 'Net Return (%) 0.65' → 0.65% → 0.0065 十进制."""
    ex = parse_response(json.dumps({
        "ym": "2026-05", "kind": "table_value",
        "value_text": "0.65%",
        "measure_label": "NTA Net Return (%)",
        "source_quote": "NTA Net Return (%) 0.65 1.85",
        "rolling_text": {"3mo": "1.85%"},
        "not_found": False,
    }), "2026-05")
    assert ex.net_return == pytest.approx(0.0065)
    assert ex.rolling["3mo"] == pytest.approx(0.0185)  # 十进制
    assert not ex.not_found


def test_table_value_bare_with_label_bucks_up():
    """bare 0.65 靠 measure_label='(%)' 兜单位."""
    ex = parse_response(json.dumps({
        "ym": "2026-05", "kind": "table_value",
        "value_text": "0.65",
        "measure_label": "Net Return (%)",
        "source_quote": "Net Return (%) 0.65",
        "not_found": False,
    }), "2026-05")
    assert ex.net_return == pytest.approx(0.0065)


def test_table_value_bare_no_hint_no_label_becomes_pending():
    """bare 无 label 无 hint → unit_ambiguous, parse_error 落."""
    ex = parse_response(json.dumps({
        "ym": "2026-05", "kind": "table_value",
        "value_text": "0.65",
        "not_found": False,
    }), "2026-05")
    assert ex.net_return is None
    assert ex.parse_error is not None
    assert "unit_ambiguous" in ex.parse_error


# ---------- nav_pair ----------

def test_nav_pair_coolabah_hovertext():
    """Coolabah Plotly hovertext 2 NAV → Python 算月度收益."""
    ex = parse_response(json.dumps({
        "ym": "2026-06", "kind": "nav_pair",
        "prev_date": "2026-05-31", "prev_text": "$134.24",
        "curr_date": "2026-06-30", "curr_text": "$135.16",
        "source_quote": "FRHY<br />2026-05-31: $134.24, FRHY<br />2026-06-30: $135.16",
        "measure_label": "Plotly hovertext NAV",
        "not_found": False,
    }), "2026-06")
    expected = 135.16 / 134.24 - 1  # 0.006853...
    assert ex.net_return == pytest.approx(expected, abs=1e-6)


def test_nav_pair_not_adjacent_rejected():
    """3 月跨度 prev/curr → prev_not_adjacent 判废."""
    ex = parse_response(json.dumps({
        "ym": "2026-06", "kind": "nav_pair",
        "prev_date": "2026-03-31", "prev_text": "1.0000",
        "curr_date": "2026-06-30", "curr_text": "1.0300",
        "source_quote": "1.0000 ... 1.0300",
        "not_found": False,
    }), "2026-06")
    assert ex.net_return is None
    assert "prev_not_adjacent" in ex.parse_error


def test_nav_pair_wrong_month_rejected():
    """LLM 抓错月 (返 5 月 NAV 但目标是 6 月) → wrong_month."""
    ex = parse_response(json.dumps({
        "ym": "2026-06", "kind": "nav_pair",
        "prev_date": "2026-04-30", "prev_text": "1.0000",
        "curr_date": "2026-05-31", "curr_text": "1.0100",
        "source_quote": "1.0000 ... 1.0100",
        "not_found": False,
    }), "2026-06")
    assert ex.net_return is None
    assert "wrong_month" in ex.parse_error


# ---------- cum_ex_dist ----------

def test_cum_ex_dist_macquarie_csv():
    """(1.0100 + 0.0050) / 1.0000 - 1 = 0.0150."""
    ex = parse_response(json.dumps({
        "ym": "2026-06", "kind": "cum_ex_dist",
        "prev_date": "2026-05-31", "prev_text": "$1.0000",
        "curr_date": "2026-06-30", "curr_text": "$1.0100",
        "dist_text": "$0.0050",
        "source_quote": "2026-05-31,1.0000\n2026-06-30,1.0100,0.0050",
        "not_found": False,
    }), "2026-06")
    assert ex.net_return == pytest.approx(0.0150, abs=1e-6)


# ---------- commentary_pct ----------

def test_commentary_pct_extract():
    """Commentary 段 'returned 0.65% net of fees'."""
    ex = parse_response(json.dumps({
        "ym": "2026-05", "kind": "commentary_pct",
        "value_text": "0.65%",
        "measure_label": "net of fees",
        "source_quote": "In May 2026 the fund returned 0.65% net of fees",
        "not_found": False,
    }), "2026-05")
    assert ex.net_return == pytest.approx(0.0065)


# ---------- not_found ----------

def test_not_found_returns_ex_no_value():
    ex = parse_response(json.dumps({
        "ym": "2026-05", "kind": "not_found", "not_found": True,
        "source_quote": "",
    }), "2026-05")
    assert ex.not_found
    assert ex.net_return is None
    assert ex.parse_error is None


# ---------- 月份闸 (ym 层) ----------

def test_wrong_ym_rejected():
    """LLM 返 ym=2026-04 但 expected=2026-06 → wrong_month."""
    ex = parse_response(json.dumps({
        "ym": "2026-04", "kind": "table_value",
        "value_text": "0.65%",
        "not_found": False,
    }), "2026-06")
    assert ex.net_return is None
    assert "wrong_month" in ex.parse_error


# ---------- parse_error 兜底 ----------

def test_llm_returns_garbage_marks_parse_error():
    """LLM 返非 JSON → parse_error."""
    ex = parse_response("sorry, I cannot answer", "2026-05")
    assert ex.not_found
    assert ex.net_return is None
    assert ex.parse_error is not None


def test_llm_returns_markdown_fenced_json():
    """LLM 返 ```json ... ``` 包裹 → 剥掉 fence 后解析."""
    text = """```json
{"ym": "2026-05", "kind": "table_value", "value_text": "0.65%", "measure_label": "Net Return (%)", "source_quote": "0.65%", "not_found": false}
```"""
    ex = parse_response(text, "2026-05")
    assert ex.net_return == pytest.approx(0.0065)


# ---------- rolling 十进制单位 ----------

def test_rolling_text_converts_to_decimal():
    """rolling_text '1.85%' → rolling['3mo'] = 0.0185 (与 net_return 同单位)."""
    ex = parse_response(json.dumps({
        "ym": "2026-05", "kind": "table_value",
        "value_text": "0.65%",
        "source_quote": "0.65% 3mo:1.85% 6mo:3.65%",
        "rolling_text": {"3mo": "1.85%", "6mo": "3.65%", "12mo": "7.87%"},
        "not_found": False,
    }), "2026-05")
    assert ex.rolling["3mo"] == pytest.approx(0.0185)
    assert ex.rolling["6mo"] == pytest.approx(0.0365)
    assert ex.rolling["12mo"] == pytest.approx(0.0787)


# ---------- fund_name_text 透传 (纠名用, 与数值四闸无关) ----------

def test_fund_name_text_transplanted_on_success():
    ex = parse_response(json.dumps({
        "ym": "2026-05", "kind": "table_value",
        "value_text": "0.65%",
        "measure_label": "Net Return (%)",
        "source_quote": "Net Return (%) 0.65",
        "not_found": False,
        "fund_name_text": "Stake Accumulate Fund",
    }), "2026-05")
    assert ex.fund_name_text == "Stake Accumulate Fund"


def test_fund_name_text_transplanted_on_not_found():
    """not_found=True 场景 (数值没抓到) 仍可能转写出抬头基金名, 供上游纠名用."""
    ex = parse_response(json.dumps({
        "ym": "2026-05", "kind": "not_found", "not_found": True,
        "source_quote": "",
        "fund_name_text": "Stake Accumulate Fund",
    }), "2026-05")
    assert ex.not_found
    assert ex.fund_name_text == "Stake Accumulate Fund"


def test_fund_name_text_transplanted_on_parse_error():
    """月份闸/单位模糊等 parse_error 场景 fund_name_text 也不丢."""
    ex = parse_response(json.dumps({
        "ym": "2026-04", "kind": "table_value",
        "value_text": "0.65%",
        "not_found": False,
        "fund_name_text": "Stake Accumulate Fund",
    }), "2026-06")
    assert ex.parse_error is not None
    assert ex.fund_name_text == "Stake Accumulate Fund"


def test_fund_name_text_missing_defaults_none():
    ex = parse_response(json.dumps({
        "ym": "2026-05", "kind": "table_value",
        "value_text": "0.65%",
        "measure_label": "Net Return (%)",
        "source_quote": "Net Return (%) 0.65",
        "not_found": False,
    }), "2026-05")
    assert ex.fund_name_text is None


def test_fund_name_text_null_string_normalizes_to_none():
    """LLM 按 prompt 约定填字符串 'null' (而非 JSON null) 时也归一化成 None."""
    ex = parse_response(json.dumps({
        "ym": "2026-05", "kind": "table_value",
        "value_text": "0.65%",
        "measure_label": "Net Return (%)",
        "source_quote": "Net Return (%) 0.65",
        "not_found": False,
        "fund_name_text": "   ",
    }), "2026-05")
    assert ex.fund_name_text is None


def test_fund_name_text_not_in_collect_text_tokens():
    """回归锁: fund_name_text 不该混进 parsers.collect_text_tokens (那个列表
    过 check_quote_tokens, 要求同时在 source_quote 里 -- 基金名在抬头, 数值行
    的 source_quote 里通常没有, 混进去会让几乎所有提取的闸1误判 fail)."""
    from llm_ingest.parsers import collect_text_tokens
    obj = {
        "value_text": "0.65%",
        "fund_name_text": "Stake Accumulate Fund",
    }
    tokens = collect_text_tokens(obj)
    assert "Stake Accumulate Fund" not in tokens


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
