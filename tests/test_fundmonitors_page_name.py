"""_extract_page_fund_name: 从 fundmonitors markdown 抽页面基金名 (Spec B 透明展示)."""
from __future__ import annotations

from llm_ingest.fundmonitors import _extract_page_fund_name


def test_h1_extracted():
    md = "# Yarra Enhanced Income Fund\n\nSome body text..."
    assert _extract_page_fund_name(md) == "Yarra Enhanced Income Fund"


def test_h2_extracted():
    md = "## KKR Credit Income Fund\n\nBody..."
    assert _extract_page_fund_name(md) == "KKR Credit Income Fund"


def test_h3_extracted():
    md = "### Bentham Global Income Fund\n\nRest..."
    assert _extract_page_fund_name(md) == "Bentham Global Income Fund"


def test_bold_fallback_when_no_heading():
    md = "Some prefix text **Macquarie Fixed Interest Fund** with more content"
    assert _extract_page_fund_name(md) == "Macquarie Fixed Interest Fund"


def test_empty_markdown_returns_none():
    assert _extract_page_fund_name("") is None
    assert _extract_page_fund_name(None) is None


def test_no_heading_no_bold_returns_none():
    md = "just plain text with no markdown formatting anywhere"
    assert _extract_page_fund_name(md) is None


def test_heading_takes_priority_over_bold():
    md = "# Real Title\n\nBody with **bold text** later"
    assert _extract_page_fund_name(md) == "Real Title"


def test_share_class_variant_preserved():
    """Wholesale/Assisted 等 share class 后缀原样保留 (前端标红核对)。"""
    md = "# Coolabah Short Term Income Fund (Wholesale)"
    assert _extract_page_fund_name(md) == "Coolabah Short Term Income Fund (Wholesale)"


def test_fund_phrase_fallback_for_fundmonitors_ajax_md():
    """fundmonitors AJAX 返 HTML 转 md 无 heading, 抓首个 'Xxx Fund/Trust' 短语."""
    md = (
        "function DoCompare() { obj = document.comparefrm; ... }\n"
        "Yarra Enhanced Income Fund Fund & Manager Details Investment Details\n"
        "Yarra Capital Management | Total FUM for all funds:"
    )
    assert _extract_page_fund_name(md) == "Yarra Enhanced Income Fund"


def test_fund_phrase_fallback_trust_variant():
    md = "some prefix Gryphon Capital Income Trust more content"
    assert _extract_page_fund_name(md) == "Gryphon Capital Income Trust"
