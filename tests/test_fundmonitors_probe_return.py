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


def test_probe_no_name_mismatch_status():
    """Spec B: name_mismatch 状态彻底移除, 就算 fund_name 与 page 不符也不再挡。"""
    fake_md = "# Some Completely Different Fund Name"
    fake_records = [("2024-01-31", 0.005), ("2024-02-29", 0.006)]
    with patch.object(fm, "find_fundid_via_tavily",
                      return_value=(9999, "abc")), \
         patch.object(fm, "fetch_profile_markdown",
                      return_value=(fake_md, "ok")), \
         patch.object(fm, "parse_html_monthly_table",
                      return_value=(fake_records, {})), \
         patch.object(fm, "gate_check_table",
                      return_value=(True, [])):
        # 无 fund_id + 无 db_conn -> 走 Tavily 通路, 无白名单短路
        result = fm.probe("Yarra Fund X")
    # 关键: 状态不是 name_mismatch, 数据入库
    assert result["status"] == "ok"
    assert result["page_fund_name"] == "Some Completely Different Fund Name"
