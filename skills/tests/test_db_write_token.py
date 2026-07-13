"""lib.db 写操作 token 软隔离测试。"""
from __future__ import annotations

import pytest

from lib.db import create_fund, ensure_tables, get_connection, upsert_monthly_return


def test_write_without_token_raises(db_conn, monkeypatch):
    monkeypatch.delenv("FUND_DB_WRITE_TOKEN", raising=False)
    with pytest.raises(PermissionError, match="FUND_DB_WRITE_TOKEN"):
        create_fund(
            db_conn, fund_id="x", fund_name="X",
            confirmed_url="http://x", fetch_method="pdf", url_type="t",
        )


def test_write_with_token_succeeds(db_conn, monkeypatch):
    monkeypatch.setenv("FUND_DB_WRITE_TOKEN", "test")
    create_fund(
        db_conn, fund_id="x", fund_name="X",
        confirmed_url="http://x", fetch_method="pdf", url_type="t",
    )
    row = db_conn.execute("SELECT fund_id FROM funds WHERE fund_id=?", ("x",)).fetchone()
    assert row["fund_id"] == "x"


def test_upsert_without_token_raises(db_conn, monkeypatch):
    monkeypatch.setenv("FUND_DB_WRITE_TOKEN", "test")
    create_fund(
        db_conn, fund_id="x", fund_name="X",
        confirmed_url="http://x", fetch_method="pdf", url_type="t",
    )
    monkeypatch.delenv("FUND_DB_WRITE_TOKEN", raising=False)
    with pytest.raises(PermissionError):
        upsert_monthly_return(
            db_conn, fund_id="x", date="2024-01-31", net_return=0.01,
        )


def test_read_ops_not_gated(db_conn, monkeypatch):
    """get_connection / ensure_tables 不需 token。"""
    monkeypatch.delenv("FUND_DB_WRITE_TOKEN", raising=False)
    conn = get_connection()
    ensure_tables(conn)  # CREATE TABLE IF NOT EXISTS，幂等读性质，不 gate
    conn.close()
