"""probe() 返回结构 (Spec B): 新加 page_fund_name, 移除 name_mismatch 状态."""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from llm_ingest import fundmonitors as fm


@pytest.fixture
def db_with_whitelist():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE funds (
            fund_id TEXT PRIMARY KEY,
            fund_name TEXT,
            fundmonitors_fund_id INTEGER,
            fundmonitors_acc_code TEXT
        )
    """)
    conn.execute(
        "INSERT INTO funds (fund_id, fund_name, fundmonitors_fund_id, fundmonitors_acc_code) "
        "VALUES ('yarra_x', 'Yarra Fund X', 1512, 'fresnjxju')"
    )
    conn.commit()
    yield conn
    conn.close()


def test_probe_ok_returns_page_fund_name(db_with_whitelist):
    """白名单短路 + fetch ok -> 返回 page_fund_name."""
    fake_md = "# Yarra Enhanced Income Fund\n\n| Year | Jan % | Feb % |\n|---|---|---|"
    fake_records = [("2024-01-31", 0.005), ("2024-02-29", 0.006)]
    with patch.object(fm, "fetch_profile_markdown",
                      return_value=(fake_md, "ok")), \
         patch.object(fm, "parse_html_monthly_table",
                      return_value=(fake_records, {})), \
         patch.object(fm, "gate_check_table",
                      return_value=(True, [])):
        result = fm.probe("Yarra Fund X", fund_id="yarra_x", db_conn=db_with_whitelist)
    assert result["status"] == "ok"
    assert result["page_fund_name"] == "Yarra Enhanced Income Fund"
    assert result["records"] == fake_records


def test_probe_fetch_fail_returns_none_page_name(db_with_whitelist):
    with patch.object(fm, "fetch_profile_markdown",
                      return_value=(None, "fetch_fail")):
        result = fm.probe("Yarra Fund X", fund_id="yarra_x", db_conn=db_with_whitelist)
    assert result["status"] == "fetch_fail"
    assert result.get("page_fund_name") is None


def test_probe_paywall_returns_none_page_name(db_with_whitelist):
    with patch.object(fm, "fetch_profile_markdown",
                      return_value=(None, "paywall")):
        result = fm.probe("Yarra Fund X", fund_id="yarra_x", db_conn=db_with_whitelist)
    assert result["status"] == "paywall"
    assert result.get("page_fund_name") is None


def test_probe_name_mismatch_via_tavily_rejected():
    """2026-07-18 事故防线: Tavily 通路 (无白名单) 页面名与输入名无字面重叠 -> name_mismatch, 不入库.

    Coolabah Assisted 走 Tavily 命中 Smarter Money Fund 页, 173 月错源入库.
    加 L1 name-fuzzy 闸后应 skip.
    """
    fake_md = "# Smarter Money Long Short Credit Fund"
    fake_records = [("2024-01-31", 0.005)]
    with patch.object(fm, "find_fundid_via_tavily",
                      return_value=(2332, "smarterX")), \
         patch.object(fm, "fetch_profile_markdown",
                      return_value=(fake_md, "ok")), \
         patch.object(fm, "parse_html_monthly_table",
                      return_value=(fake_records, {})), \
         patch.object(fm, "gate_check_table",
                      return_value=(True, [])):
        result = fm.probe("Coolabah Floating-Rate High Yield Fund (Assisted)")
    assert result["status"] == "name_mismatch"
    assert result["records"] == []
    # errors[0] 应写清 mismatch 原因 (inter=∅ 或 name_tokens_empty)
    assert any(kw in result["errors"][0] for kw in ("inter=∅", "tokens_empty"))


def test_probe_name_match_via_tavily_ok():
    """Tavily 通路 + 页面名与输入名 token 有交集 -> 正常入库."""
    fake_md = "# Yarra Enhanced Income Fund"
    fake_records = [("2024-01-31", 0.005)]
    with patch.object(fm, "find_fundid_via_tavily",
                      return_value=(1512, "yarraX")), \
         patch.object(fm, "fetch_profile_markdown",
                      return_value=(fake_md, "ok")), \
         patch.object(fm, "parse_html_monthly_table",
                      return_value=(fake_records, {})), \
         patch.object(fm, "gate_check_table",
                      return_value=(True, [])):
        result = fm.probe("Yarra Enhanced Income Fund")
    assert result["status"] == "ok"


def test_probe_whitelist_bypasses_name_check(db_with_whitelist):
    """白名单短路 (funds.fundmonitors_fund_id 已绑) 时豁免 name 闸 (人工背书)."""
    fake_md = "# Some Weird Name Not Matching"
    fake_records = [("2024-01-31", 0.005)]
    with patch.object(fm, "fetch_profile_markdown",
                      return_value=(fake_md, "ok")), \
         patch.object(fm, "parse_html_monthly_table",
                      return_value=(fake_records, {})), \
         patch.object(fm, "gate_check_table",
                      return_value=(True, [])):
        result = fm.probe("Yarra Fund X", fund_id="yarra_x", db_conn=db_with_whitelist)
    # 白名单命中 -> 豁免 name 闸, 状态 ok
    assert result["status"] == "ok"


def test_name_tokens_stopwords_dropped():
    """_name_tokens: 常见停用词 (fund/trust/income/credit/...) 不参与交集."""
    tokens = fm._name_tokens("Bentham Global Income Fund")
    assert "bentham" in tokens
    assert "global" in tokens
    assert "income" not in tokens  # stopword
    assert "fund" not in tokens    # stopword


def test_name_matches_shared_specific_token():
    """特定 token (bentham/yarra/coolabah) 有交集 -> ok."""
    ok, _msg = fm._name_matches(
        "Bentham Global Income Fund",
        "Bentham Global Income Fund",
    )
    assert ok is True


def test_name_matches_only_stopwords_shared_fails():
    """仅共享停用词 (fund/income) -> 无交集, 判 mismatch."""
    ok, msg = fm._name_matches(
        "Smarter Money Long Short Credit Fund",  # 特定 token: smarter/money/long/short/credit → 都是 stopword
        "Coolabah Floating-Rate High Yield Fund (Assisted)",  # 特定 token: coolabah/assisted
    )
    # 关键: 两侧都被停用词过滤后, coolabah/assisted 与 (无) 无交集
    assert ok is False
    assert any(kw in msg for kw in ("mismatch", "tokens_empty"))
