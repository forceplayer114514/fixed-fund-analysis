"""Spec A 迁移 (20260717) 单元测试. 用 tmp_path 临时 sqlite 文件."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest import store
from llm_ingest.migrations.spec_a_20260717 import (
    MIGRATION_ID,
    apply,
    _has_column,
)


@pytest.fixture()
def conn(tmp_path):
    """临时 sqlite 文件. ensure_tables_if_missing 建 4 张 llm_ingest 直写表."""
    db = tmp_path / "fund_analysis.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    store.ensure_tables_if_missing(c)
    yield c
    c.close()


def _insert_fund(conn, fund_id: str, fund_name: str, **kw):
    """直接 INSERT 避开 upsert_fund 的多字段, 便于测边界."""
    apir = kw.get("apir_code")
    inception = kw.get("inception_date")
    conn.execute(
        """
        INSERT INTO funds
            (fund_id, fund_name, apir_code, confirmed_url, fetch_method,
             url_type, max_pdf_pages, inception_date, inception_assumed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fund_id, fund_name, apir,
            kw.get("confirmed_url", "https://example.com"),
            kw.get("fetch_method", "llm_ingest"),
            kw.get("url_type", "archive"),
            kw.get("max_pdf_pages"),
            inception,
            kw.get("inception_assumed", 0),
        ),
    )
    conn.commit()


def _insert_monthly(conn, fund_id: str, date: str, net_return: float = 0.005,
                    source_quote=None, pattern_tag=None):
    conn.execute(
        """
        INSERT INTO monthly_returns
            (fund_id, date, net_return, nav, source_quote, pattern_tag)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (fund_id, date, net_return, 1.0, source_quote, pattern_tag),
    )
    conn.commit()


# ------------------- 1. 加列 -------------------

def test_migration_adds_columns(conn):
    assert not _has_column(conn, "funds", "fundmonitors_fund_id")
    assert not _has_column(conn, "funds", "fundmonitors_acc_code")
    r = apply(conn)
    assert r["migration_id"] == MIGRATION_ID
    assert _has_column(conn, "funds", "fundmonitors_fund_id")
    assert _has_column(conn, "funds", "fundmonitors_acc_code")
    assert "funds.fundmonitors_fund_id" in r["applied"]
    assert "funds.fundmonitors_acc_code" in r["applied"]


def test_migration_idempotent(conn):
    # 插一个假 fund, 让第一遍能实际改动
    _insert_fund(conn, "bentham_global_income", "Bentham GIF")
    r1 = apply(conn)
    assert len(r1["applied"]) > 0
    r2 = apply(conn)
    # 第二次 applied 应为空 (全部 skipped)
    assert r2["applied"] == []
    assert len(r2["skipped"]) >= len(r1["applied"])
    # 加两列 skipped 里明确出现 "already exists"
    joined = " | ".join(r2["skipped"])
    assert "funds.fundmonitors_fund_id already exists" in joined
    assert "funds.fundmonitors_acc_code already exists" in joined


# ------------------- 2. JCB 日期 -------------------

def test_migration_jcb_dates_fixed(conn):
    _insert_fund(conn, "jcb_active_bond", "JCB Active Bond")
    _insert_monthly(conn, "jcb_active_bond", "2026-04-01", 0.005)
    _insert_monthly(conn, "jcb_active_bond", "2026-05-01", 0.006)
    _insert_monthly(conn, "jcb_active_bond", "2026-06-01", 0.007)
    r = apply(conn)
    dates = [
        row[0] for row in conn.execute(
            "SELECT date FROM monthly_returns WHERE fund_id='jcb_active_bond' ORDER BY date"
        ).fetchall()
    ]
    assert dates == ["2026-04-30", "2026-05-31", "2026-06-30"]
    for tag in ("jcb 2026-04-01->2026-04-30",
                "jcb 2026-05-01->2026-05-31",
                "jcb 2026-06-01->2026-06-30"):
        assert tag in r["applied"]


def test_migration_jcb_already_fixed_skipped(conn):
    _insert_fund(conn, "jcb_active_bond", "JCB Active Bond")
    _insert_monthly(conn, "jcb_active_bond", "2026-04-30", 0.005)
    _insert_monthly(conn, "jcb_active_bond", "2026-05-31", 0.006)
    _insert_monthly(conn, "jcb_active_bond", "2026-06-30", 0.007)
    r = apply(conn)
    joined = " | ".join(r["skipped"])
    assert "jcb 2026-04-01->2026-04-30 already fixed" in joined
    assert "jcb 2026-05-01->2026-05-31 already fixed" in joined
    assert "jcb 2026-06-01->2026-06-30 already fixed" in joined
    # 不炸: 已 fixed 分支不 raise
    for prefix in ("jcb 2026-",):
        matches = [a for a in r["applied"] if a.startswith(prefix)]
        assert matches == []


# ------------------- 3. GCI inception -------------------

def test_migration_gci_inception_set(conn):
    _insert_fund(conn, "gryphon_capital_income", "GCI",
                 inception_date=None)
    r = apply(conn)
    row = conn.execute(
        "SELECT inception_date FROM funds WHERE fund_id='gryphon_capital_income'"
    ).fetchone()
    assert row["inception_date"] == "2019-02-28"
    assert "gci inception=2019-02-28" in r["applied"]


def test_migration_gci_inception_already_set_skipped(conn):
    _insert_fund(conn, "gryphon_capital_income", "GCI",
                 inception_date="2019-01-01")
    r = apply(conn)
    row = conn.execute(
        "SELECT inception_date FROM funds WHERE fund_id='gryphon_capital_income'"
    ).fetchone()
    # 不覆盖
    assert row["inception_date"] == "2019-01-01"
    joined = " | ".join(r["skipped"])
    assert "gci inception already set to 2019-01-01" in joined


# ------------------- 4. 4 支种子 -------------------

def test_migration_seed_fundmonitors_ids(conn):
    for fid, name in [
        ("bentham_global_income", "Bentham GIF"),
        ("jcb_active_bond", "JCB Active Bond"),
        ("macquarie_australian_fixed_interest", "Macquarie AFI"),
        ("smarter_money_lscf", "Smarter Money LSCF"),
    ]:
        _insert_fund(conn, fid, name)
    r = apply(conn)
    seeded = {
        row["fund_id"]: row["fundmonitors_fund_id"]
        for row in conn.execute(
            "SELECT fund_id, fundmonitors_fund_id FROM funds"
        ).fetchall()
    }
    assert seeded["bentham_global_income"] == 3312
    assert seeded["jcb_active_bond"] == 1003
    assert seeded["macquarie_australian_fixed_interest"] == 2107
    assert seeded["smarter_money_lscf"] == 2332
    for tag in ("seed bentham_global_income=3312",
                "seed jcb_active_bond=1003",
                "seed macquarie_australian_fixed_interest=2107",
                "seed smarter_money_lscf=2332"):
        assert tag in r["applied"]


# ------------------- 5. Yarra / KKR source_quote 抠 -------------------

def test_migration_seed_extracts_from_source_quote(conn):
    _insert_fund(conn, "yarra_enhanced_income_fund", "Yarra EIF")
    _insert_monthly(
        conn, "yarra_enhanced_income_fund", "2026-06-30", 0.004,
        source_quote="L3 fundmonitors table: https://.../FundID=999&AccCode=abcxyz",
        pattern_tag="fundmonitors_table",
    )
    r = apply(conn)
    row = conn.execute(
        "SELECT fundmonitors_fund_id, fundmonitors_acc_code FROM funds "
        "WHERE fund_id='yarra_enhanced_income_fund'"
    ).fetchone()
    assert row["fundmonitors_fund_id"] == 999
    assert row["fundmonitors_acc_code"] == "abcxyz"
    joined = " | ".join(r["applied"])
    assert "extract yarra_enhanced_income_fund=999" in joined


def test_migration_seed_extract_no_match_skipped(conn):
    _insert_fund(conn, "yarra_enhanced_income_fund", "Yarra EIF")
    # 没有 pattern_tag='fundmonitors_table' 的行
    _insert_monthly(
        conn, "yarra_enhanced_income_fund", "2026-06-30", 0.004,
        source_quote="Fund returned 0.4% (Yarra factsheet).",
        pattern_tag="factsheet",
    )
    r = apply(conn)
    row = conn.execute(
        "SELECT fundmonitors_fund_id FROM funds "
        "WHERE fund_id='yarra_enhanced_income_fund'"
    ).fetchone()
    assert row["fundmonitors_fund_id"] is None
    joined = " | ".join(r["skipped"])
    assert (
        "extract yarra_enhanced_income_fund no fundmonitors_table source_quote match"
        in joined
    )


# ------------------- 6. funds 缺失容忍 -------------------

def test_migration_seed_fund_missing_skipped(conn):
    # 完全空 DB, funds 表无任何行
    r = apply(conn)  # 不 raise
    joined = " | ".join(r["skipped"])
    assert "seed bentham_global_income=3312 funds row missing" in joined
    assert "seed jcb_active_bond=1003 funds row missing" in joined
    assert "seed macquarie_australian_fixed_interest=2107 funds row missing" in joined
    assert "seed smarter_money_lscf=2332 funds row missing" in joined
    # Yarra/KKR 也应 skipped
    assert "extract yarra_enhanced_income_fund funds row missing" in joined
    assert "extract kkr_credit_income_fund funds row missing" in joined
    # JCB 日期 fund 缺失 skipped
    assert "jcb dates" in joined
    # GCI inception 缺失 skipped
    assert "gci inception" in joined
