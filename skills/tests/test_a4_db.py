"""A4 db 层:extractor_verified 迁移 + verify_extractor 批量 promote。

测 ensure_tables 幂等加列(老基金有 monthly_returns -> 标 1)、create_fund 默认 0、
verify_extractor 仅 promote generic_first_use(不碰 ambiguous)。
"""
from __future__ import annotations

import sqlite3

import pytest

from lib.db import (
    add_pending_review,
    create_fund,
    ensure_tables,
    get_extractor_verified,
    get_monthly_returns,
    list_pending_review,
    upsert_monthly_return,
    verify_extractor,
)


def _make_fund(conn, fund_id="F001", fund_name="Test Fund", **kwargs):
    defaults = dict(
        confirmed_url="https://example.com/fund.pdf",
        fetch_method="pdf",
        url_type="pdf",
    )
    defaults.update(kwargs)
    create_fund(conn, fund_id=fund_id, fund_name=fund_name, **defaults)


def test_create_fund_default_extractor_verified_0(db_conn):
    _make_fund(db_conn, fund_id="NEW1", fund_name="New Fund")
    assert get_extractor_verified(db_conn, "NEW1") == 0


def test_get_extractor_verified_missing_fund_0(db_conn):
    assert get_extractor_verified(db_conn, "NOPE") == 0


def test_old_fund_with_returns_migrated_to_verified_1(tmp_path, monkeypatch):
    """旧库(无 extractor_verified 列)+ fund + monthly_returns -> ensure_tables
    迁移加列 + 标 1(信任已入库老基金)。新基金无 returns 不被标。"""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    # 旧 schema(无 extractor_verified)
    conn.executescript(
        """
        CREATE TABLE funds (
            fund_id TEXT PRIMARY KEY, fund_name TEXT NOT NULL UNIQUE,
            confirmed_url TEXT NOT NULL, fetch_method TEXT NOT NULL,
            url_type TEXT NOT NULL, inception_date TEXT, inception_assumed INTEGER DEFAULT 0
        );
        CREATE TABLE monthly_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fund_id TEXT NOT NULL,
            date TEXT NOT NULL, net_return REAL NOT NULL, nav REAL NOT NULL,
            UNIQUE(fund_id, date)
        );
        INSERT INTO funds VALUES ('OLD1','Old Fund','u','pdf','pdf','2020-01-01',0);
        INSERT INTO funds VALUES ('NEW1','New Fund','u2','pdf','pdf','2021-01-01',0);
        INSERT INTO monthly_returns (fund_id,date,net_return,nav) VALUES ('OLD1','2020-01-31',0.001,1.001);
        """
    )
    conn.commit()
    monkeypatch.setenv("FUND_DB_WRITE_TOKEN", "test")
    ensure_tables(conn)
    # OLD1 有 monthly_returns -> 标 1;NEW1 无 -> 留 0
    assert get_extractor_verified(conn, "OLD1") == 1
    assert get_extractor_verified(conn, "NEW1") == 0
    conn.close()


def test_verify_extractor_promotes_generic_first_use(db_conn):
    _make_fund(db_conn, fund_id="G1", fund_name="G Fund")
    for ym, r in [("2024-01-31", 0.005), ("2024-02-29", 0.003), ("2024-03-31", 0.004)]:
        add_pending_review(
            db_conn, fund_id="G1", date=ym, net_return=r,
            extract_method="code", review_reason="generic_first_use",
        )
    result = verify_extractor(db_conn, "G1")
    assert result["promoted_count"] == 3
    assert get_extractor_verified(db_conn, "G1") == 1
    # 3 月入 monthly_returns
    assert len(get_monthly_returns(db_conn, "G1")) == 3
    # pending 全 approved
    pending = list_pending_review(db_conn, "G1", state="pending")
    assert pending == []


def test_verify_extractor_skips_ambiguous_and_overlimit(db_conn):
    """verify_extractor 仅 promote generic_first_use,不碰 ambiguous_subject/超限值。"""
    _make_fund(db_conn, fund_id="G2", fund_name="G2 Fund")
    add_pending_review(
        db_conn, fund_id="G2", date="2024-01-31", net_return=0.005,
        extract_method="code", review_reason="generic_first_use",
    )
    add_pending_review(
        db_conn, fund_id="G2", date="2024-02-29", net_return=0.004,
        extract_method="code", review_reason="ambiguous_subject",
    )
    add_pending_review(
        db_conn, fund_id="G2", date="2024-03-31", net_return=0.6,
        extract_method="code", review_reason="abs_return_exceeds_threshold",
    )
    result = verify_extractor(db_conn, "G2")
    assert result["promoted_count"] == 1
    # 仅 1 月入 monthly_returns
    assert len(get_monthly_returns(db_conn, "G2")) == 1
    # ambiguous + 超限仍 pending
    pending = list_pending_review(db_conn, "G2", state="pending")
    reasons = {p["review_reason"] for p in pending}
    assert reasons == {"ambiguous_subject", "abs_return_exceeds_threshold"}


def test_verify_extractor_missing_fund_raises(db_conn):
    with pytest.raises(KeyError):
        verify_extractor(db_conn, "NOPE")
