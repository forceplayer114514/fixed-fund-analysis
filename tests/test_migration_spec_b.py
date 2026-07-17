"""Spec B 迁移: ALTER TABLE funds ADD COLUMN discovered_source_name (幂等)."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from llm_ingest.migrations import spec_b_20260717 as mig


@pytest.fixture
def empty_db():
    """建一个仅有 funds 表的空 DB (无 discovered_source_name 列)。"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute("""
        CREATE TABLE funds (
            fund_id TEXT PRIMARY KEY,
            fund_name TEXT NOT NULL,
            confirmed_url TEXT NOT NULL,
            fetch_method TEXT NOT NULL,
            url_type TEXT NOT NULL
        )
    """)
    conn.commit()
    yield conn
    conn.close()
    Path(tmp.name).unlink(missing_ok=True)


def test_apply_adds_column(empty_db):
    mig.apply(empty_db)
    cur = empty_db.execute("PRAGMA table_info(funds)")
    cols = {row[1] for row in cur.fetchall()}
    assert "discovered_source_name" in cols


def test_apply_idempotent(empty_db):
    mig.apply(empty_db)
    mig.apply(empty_db)  # 第二次不该抛
    cur = empty_db.execute("PRAGMA table_info(funds)")
    dsn_rows = [r for r in cur.fetchall() if r[1] == "discovered_source_name"]
    assert len(dsn_rows) == 1  # 只加一次


def test_column_type_is_text(empty_db):
    mig.apply(empty_db)
    cur = empty_db.execute("PRAGMA table_info(funds)")
    for row in cur.fetchall():
        if row[1] == "discovered_source_name":
            assert row[2] == "TEXT"
            assert row[3] == 0  # NOT NULL = 0 (nullable)
            return
    pytest.fail("discovered_source_name 列不存在")


def test_existing_data_untouched(empty_db):
    empty_db.execute(
        "INSERT INTO funds (fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES ('x', 'Fund X', 'https://a.com', 'code', 'archive')"
    )
    empty_db.commit()
    mig.apply(empty_db)
    row = empty_db.execute(
        "SELECT fund_name, discovered_source_name FROM funds WHERE fund_id='x'"
    ).fetchone()
    assert row[0] == "Fund X"
    assert row[1] is None  # 新列默认 NULL
