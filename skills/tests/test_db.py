"""skills/lib/db.py 的单元测试 (TDD)。

覆盖建表、基金插入与唯一约束、月度收益 upsert、NAV 复利重算、
commentary_truth 保留语义、级联删除等关键路径。
"""
from __future__ import annotations

import sqlite3

import pytest

from lib.db import (
    create_fund,
    ensure_tables,
    get_fund,
    get_monthly_returns,
    list_funds,
    recompute_nav,
    upsert_monthly_return,
)


def _make_fund(conn, fund_id="F001", fund_name="Test Fund", **kwargs):
    """辅助:插入一只基金,补齐必填默认参数。"""
    defaults = dict(
        confirmed_url="https://example.com/fund.pdf",
        fetch_method="pdf",
        url_type="pdf",
    )
    defaults.update(kwargs)
    create_fund(conn, fund_id=fund_id, fund_name=fund_name, **defaults)


def test_ensure_tables_creates_tables(db_conn):
    # conftest 已调过一次,这里再调验证幂等
    ensure_tables(db_conn)
    rows = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert "funds" in names
    assert "monthly_returns" in names


def test_create_fund_and_get(db_conn):
    create_fund(
        db_conn,
        fund_id="F001",
        fund_name="Test Fund A",
        confirmed_url="https://example.com/a.pdf",
        fetch_method="pdf",
        url_type="pdf",
        apir_code="ETL5010AU",
        max_pdf_pages=10,
        verified_at="2026-07-11T00:00:00",
    )
    f = get_fund(db_conn, "F001")
    assert f is not None
    assert f["fund_id"] == "F001"
    assert f["fund_name"] == "Test Fund A"
    assert f["confirmed_url"] == "https://example.com/a.pdf"
    assert f["fetch_method"] == "pdf"
    assert f["url_type"] == "pdf"
    assert f["apir_code"] == "ETL5010AU"
    assert f["max_pdf_pages"] == 10
    assert f["verified_at"] == "2026-07-11T00:00:00"
    assert f["created_at"] is not None


def test_create_fund_duplicate_name_raises(db_conn):
    create_fund(db_conn, fund_id="F001", fund_name="Dup Name",
                confirmed_url="u1", fetch_method="pdf", url_type="pdf")
    with pytest.raises(sqlite3.IntegrityError):
        create_fund(db_conn, fund_id="F002", fund_name="Dup Name",
                    confirmed_url="u2", fetch_method="pdf", url_type="pdf")


def test_create_fund_duplicate_apir_raises(db_conn):
    create_fund(db_conn, fund_id="F001", fund_name="Fund A",
                confirmed_url="u1", fetch_method="pdf", url_type="pdf",
                apir_code="ETL5010AU")
    with pytest.raises(sqlite3.IntegrityError):
        create_fund(db_conn, fund_id="F002", fund_name="Fund B",
                    confirmed_url="u2", fetch_method="pdf", url_type="pdf",
                    apir_code="ETL5010AU")


def test_upsert_monthly_return_inserts_and_computes_nav(db_conn):
    _make_fund(db_conn)
    upsert_monthly_return(db_conn, fund_id="F001", date="2024-01-31", net_return=0.01)
    upsert_monthly_return(db_conn, fund_id="F001", date="2024-02-29", net_return=0.02)
    upsert_monthly_return(db_conn, fund_id="F001", date="2024-03-31", net_return=-0.005)
    rows = get_monthly_returns(db_conn, "F001")
    assert len(rows) == 3
    assert rows[0]["net_return"] == pytest.approx(0.01)
    assert rows[0]["nav"] == pytest.approx(1.01)
    assert rows[1]["nav"] == pytest.approx(1.01 * 1.02)
    assert rows[2]["nav"] == pytest.approx(1.01 * 1.02 * 0.995)


def test_upsert_monthly_return_updates_existing(db_conn):
    _make_fund(db_conn)
    upsert_monthly_return(db_conn, fund_id="F001", date="2024-01-31", net_return=0.01)
    upsert_monthly_return(db_conn, fund_id="F001", date="2024-02-29", net_return=0.02)
    # 对已有月份 upsert 新 net_return
    upsert_monthly_return(db_conn, fund_id="F001", date="2024-01-31", net_return=0.03)
    rows = get_monthly_returns(db_conn, "F001")
    assert len(rows) == 2
    assert rows[0]["net_return"] == pytest.approx(0.03)
    assert rows[0]["nav"] == pytest.approx(1.03)
    assert rows[1]["nav"] == pytest.approx(1.03 * 1.02)


def test_upsert_preserves_commentary_truth_when_none(db_conn):
    _make_fund(db_conn)
    upsert_monthly_return(db_conn, fund_id="F001", date="2024-01-31",
                          net_return=0.01, commentary_truth=0.015)
    # 再次 upsert,commentary_truth 传 None -> 应保留旧值 0.015
    upsert_monthly_return(db_conn, fund_id="F001", date="2024-01-31",
                          net_return=0.02, commentary_truth=None)
    rows = get_monthly_returns(db_conn, "F001")
    assert len(rows) == 1
    assert rows[0]["net_return"] == pytest.approx(0.02)
    assert rows[0]["commentary_truth"] == pytest.approx(0.015)


def test_recompute_nav_correct_order(db_conn):
    _make_fund(db_conn)
    # 直接插入乱序数据(nav 占位 1.0),再调 recompute_nav 验证按 date 升序重算
    db_conn.execute(
        "INSERT INTO monthly_returns (fund_id, date, net_return, nav) "
        "VALUES ('F001', '2024-03-31', 0.03, 1.0)"
    )
    db_conn.execute(
        "INSERT INTO monthly_returns (fund_id, date, net_return, nav) "
        "VALUES ('F001', '2024-01-31', 0.01, 1.0)"
    )
    db_conn.execute(
        "INSERT INTO monthly_returns (fund_id, date, net_return, nav) "
        "VALUES ('F001', '2024-02-29', 0.02, 1.0)"
    )
    db_conn.commit()
    recompute_nav(db_conn, "F001")
    rows = get_monthly_returns(db_conn, "F001")
    assert [r["date"] for r in rows] == ["2024-01-31", "2024-02-29", "2024-03-31"]
    assert rows[0]["nav"] == pytest.approx(1.01)
    assert rows[1]["nav"] == pytest.approx(1.01 * 1.02)
    assert rows[2]["nav"] == pytest.approx(1.01 * 1.02 * 1.03)


def test_list_funds_ordered_by_name(db_conn):
    create_fund(db_conn, fund_id="F002", fund_name="Zebra Fund",
                confirmed_url="u", fetch_method="pdf", url_type="pdf")
    create_fund(db_conn, fund_id="F001", fund_name="Apple Fund",
                confirmed_url="u", fetch_method="pdf", url_type="pdf")
    create_fund(db_conn, fund_id="F003", fund_name="Mango Fund",
                confirmed_url="u", fetch_method="pdf", url_type="pdf")
    funds = list_funds(db_conn)
    assert [f["fund_name"] for f in funds] == ["Apple Fund", "Mango Fund", "Zebra Fund"]
    assert [f["fund_id"] for f in funds] == ["F001", "F003", "F002"]


def test_get_monthly_returns_ordered_by_date(db_conn):
    _make_fund(db_conn)
    # 乱序 upsert
    upsert_monthly_return(db_conn, fund_id="F001", date="2024-03-31", net_return=0.03)
    upsert_monthly_return(db_conn, fund_id="F001", date="2024-01-31", net_return=0.01)
    upsert_monthly_return(db_conn, fund_id="F001", date="2024-02-29", net_return=0.02)
    rows = get_monthly_returns(db_conn, "F001")
    assert [r["date"] for r in rows] == ["2024-01-31", "2024-02-29", "2024-03-31"]


def test_cascade_delete(db_conn):
    _make_fund(db_conn)
    upsert_monthly_return(db_conn, fund_id="F001", date="2024-01-31", net_return=0.01)
    upsert_monthly_return(db_conn, fund_id="F001", date="2024-02-29", net_return=0.02)
    # get_connection 设置了 PRAGMA foreign_keys=ON,删除 fund 应级联删除月度记录
    db_conn.execute("DELETE FROM funds WHERE fund_id = ?", ("F001",))
    db_conn.commit()
    rows = db_conn.execute(
        "SELECT * FROM monthly_returns WHERE fund_id = ?", ("F001",)
    ).fetchall()
    assert len(rows) == 0
