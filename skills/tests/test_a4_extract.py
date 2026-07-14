"""A4 提取器准入验证:反 benchmark 守卫 + 注册表 + generic/专属区分。

测 extract_commentary_return_full(generic) 的 ambiguous 守卫、EXTRACTORS
注册表、is_generic_extractor。不测网络。
"""
from __future__ import annotations

from lib.extract import (
    EXTRACTORS,
    ExtractedReturn,
    extract_bentham_net_return_full,
    extract_commentary_return_full,
    get_extractor,
    is_generic_extractor,
)


# --- A4c 反 benchmark 守卫 ---


def test_extracted_return_ambiguous_default_false():
    er = ExtractedReturn(value=0.005, source_quote="returned 0.50%")
    assert er.ambiguous is False


def test_commentary_full_pure_return_not_ambiguous():
    text = "The Fund returned 0.53% for the month."
    er = extract_commentary_return_full(text)
    assert er is not None
    assert er.value == 0.0053
    assert er.source_quote == "returned 0.53%"
    assert er.ambiguous is False


def test_commentary_full_benchmark_in_window_ambiguous():
    """基金自身 returned 0.72%,窗口内提 benchmark -> ambiguous=True(认错对象风险)。"""
    text = (
        "The Fund returned 0.72% for the month, outperforming the benchmark "
        "which returned 0.35% over the same period."
    )
    er = extract_commentary_return_full(text)
    assert er is not None
    assert er.value == 0.0072
    assert er.ambiguous is True


def test_commentary_full_index_in_window_ambiguous():
    text = "returned 0.41% for the month, ahead of the index return."
    er = extract_commentary_return_full(text)
    assert er is not None
    assert er.ambiguous is True


def test_commentary_full_outside_window_not_ambiguous():
    """benchmark 词在 200 字符窗口外 -> 不触发(避免误伤远距离提及)。"""
    text = "The Fund returned 0.53% for the month." + ("x" * 250) + " benchmark"
    er = extract_commentary_return_full(text)
    assert er is not None
    assert er.ambiguous is False


def test_commentary_full_no_match_returns_none():
    assert extract_commentary_return_full("no return figure here") is None
    assert extract_commentary_return_full("") is None


def test_bentham_full_never_ambiguous():
    """专属提取器人写时人眼看过样本,不带守卫,ambiguous 永远 False。"""
    text = "had a total return (after fees) of 0.42%, outperforming the benchmark."
    er = extract_bentham_net_return_full(text)
    assert er is not None
    assert er.value == 0.0042
    assert er.ambiguous is False


# --- A1 注册表 ---


def test_extractors_registry_contains_generic_stake_bentham():
    assert "generic" in EXTRACTORS
    assert "stake" in EXTRACTORS
    assert "bentham" in EXTRACTORS


def test_get_extractor_default_generic():
    assert get_extractor(None) is EXTRACTORS["generic"]
    assert get_extractor("unknown") is EXTRACTORS["generic"]
    assert get_extractor("bentham") is EXTRACTORS["bentham"]


def test_is_generic_extractor():
    assert is_generic_extractor("generic") is True
    assert is_generic_extractor("stake") is True
    assert is_generic_extractor(None) is True
    assert is_generic_extractor("bentham") is False
