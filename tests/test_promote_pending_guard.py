"""P4: promote_pending guard 白名单单测.

覆盖 llm_ingest/store.py::promote_pending 的权威源保护:
    - 已入库行 pattern_tag ∈ {'fundmonitors_table', 'llm'} 时, pending approve 拒
      覆盖, 返 action='skipped_authoritative_covered' + existing_tag, pending 标
      rejected, monthly_returns 不动。
    - pattern_tag 为 NULL 或非白名单值 (skills-era 遗留 tag, 如 'nta_net_return_label')
      时, pending approve 覆盖 monthly_returns 并触发 NAV 重算, pending 标 approved。
    - 空槽 (无同月记录) 时, pending approve 正常插入。
    - pending id 不存在时 raise KeyError。

用 in-memory sqlite, store.ensure_tables_if_missing 建表。
"""
from __future__ import annotations

import sqlite3
import sys

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest import store


# ---------- 夹具 ----------

@pytest.fixture()
def conn():
    """内存 sqlite + 建表 + 建 fund 让 FK 通过."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    store.ensure_tables_if_missing(c)
    store.upsert_fund(
        c,
        fund_id="fund_x",
        fund_name="Fund X",
        confirmed_url="https://example.com/fund_x",
    )
    yield c
    c.close()


def _insert_monthly(
    conn: sqlite3.Connection,
    *,
    fund_id: str,
    date: str,
    net_return: float,
    pattern_tag,  # 可为 None
    source_quote: str = "seed",
) -> None:
    """直插一行 monthly_returns (bypass upsert helper, 允许 pattern_tag=NULL)."""
    conn.execute(
        "INSERT INTO monthly_returns "
        "(fund_id, date, net_return, nav, source_quote, pattern_tag) "
        "VALUES (?, ?, ?, 1.0, ?, ?)",
        (fund_id, date, net_return, source_quote, pattern_tag),
    )
    conn.commit()


def _add_pending_row(
    conn: sqlite3.Connection,
    *,
    fund_id: str,
    date: str,
    net_return: float,
) -> int:
    """插一行 pending_review, 返回 id."""
    cur = conn.execute(
        "INSERT INTO pending_review "
        "(fund_id, date, net_return, source_quote, extract_method, "
        " gate_result, review_reason, candidates_json) "
        "VALUES (?, ?, ?, 'q', 'llm', 'q0r0f1a1', 'quote_failed', '{}')",
        (fund_id, date, net_return),
    )
    conn.commit()
    return cur.lastrowid


# ---------- 用例 ----------

def test_promote_blocked_by_fundmonitors_table(conn):
    """L3 已入库 -> pending 被挡, 标 rejected, monthly_returns 不动."""
    _insert_monthly(
        conn, fund_id="fund_x", date="2024-03-31",
        net_return=0.0049, pattern_tag="fundmonitors_table",
    )
    pid = _add_pending_row(
        conn, fund_id="fund_x", date="2024-03-31", net_return=0.0100,
    )

    info = store.promote_pending(conn, pid)

    assert info["action"] == "skipped_authoritative_covered"
    assert info["existing_tag"] == "fundmonitors_table"
    assert info["fund_id"] == "fund_x"
    assert info["date"] == "2024-03-31"

    # pending 应标 rejected + reason 含关键字
    row = conn.execute(
        "SELECT review_state, review_reason FROM pending_review WHERE id=?",
        (pid,),
    ).fetchone()
    assert row["review_state"] == "rejected"
    assert "skipped_authoritative_covered" in row["review_reason"]
    assert "fundmonitors_table" in row["review_reason"]

    # monthly_returns 不动 (仍是 L3 值)
    mr = conn.execute(
        "SELECT net_return, pattern_tag FROM monthly_returns "
        "WHERE fund_id=? AND date=?",
        ("fund_x", "2024-03-31"),
    ).fetchone()
    assert mr["net_return"] == pytest.approx(0.0049)
    assert mr["pattern_tag"] == "fundmonitors_table"


def test_promote_blocked_by_llm_tag(conn):
    """L1 LLM 通路已入库 -> pending 被挡, 标 rejected, monthly_returns 不动."""
    _insert_monthly(
        conn, fund_id="fund_x", date="2024-04-30",
        net_return=0.0055, pattern_tag="llm",
    )
    pid = _add_pending_row(
        conn, fund_id="fund_x", date="2024-04-30", net_return=0.0200,
    )

    info = store.promote_pending(conn, pid)

    assert info["action"] == "skipped_authoritative_covered"
    assert info["existing_tag"] == "llm"

    row = conn.execute(
        "SELECT review_state, review_reason FROM pending_review WHERE id=?",
        (pid,),
    ).fetchone()
    assert row["review_state"] == "rejected"
    assert "llm" in row["review_reason"]

    mr = conn.execute(
        "SELECT net_return, pattern_tag FROM monthly_returns "
        "WHERE fund_id=? AND date=?",
        ("fund_x", "2024-04-30"),
    ).fetchone()
    assert mr["net_return"] == pytest.approx(0.0055)
    assert mr["pattern_tag"] == "llm"


def test_promote_allowed_over_null_tag(conn):
    """已有行 pattern_tag=NULL (skills-era) -> pending approve 覆盖."""
    _insert_monthly(
        conn, fund_id="fund_x", date="2024-05-31",
        net_return=0.0030, pattern_tag=None,
    )
    pid = _add_pending_row(
        conn, fund_id="fund_x", date="2024-05-31", net_return=0.0088,
    )

    info = store.promote_pending(conn, pid)

    assert info["action"] == "approved"
    assert info["fund_id"] == "fund_x"
    assert info["date"] == "2024-05-31"
    assert "existing_tag" not in info  # 未被挡, 不带 existing_tag

    # pending 标 approved
    row = conn.execute(
        "SELECT review_state FROM pending_review WHERE id=?",
        (pid,),
    ).fetchone()
    assert row["review_state"] == "approved"

    # monthly_returns 被覆盖为 pending 值
    mr = conn.execute(
        "SELECT net_return FROM monthly_returns WHERE fund_id=? AND date=?",
        ("fund_x", "2024-05-31"),
    ).fetchone()
    assert mr["net_return"] == pytest.approx(0.0088)


def test_promote_allowed_over_nta_label(conn):
    """已有行 pattern_tag='nta_net_return_label' (skills GCI 特例) -> 允许覆盖."""
    _insert_monthly(
        conn, fund_id="fund_x", date="2024-06-30",
        net_return=0.0020, pattern_tag="nta_net_return_label",
    )
    pid = _add_pending_row(
        conn, fund_id="fund_x", date="2024-06-30", net_return=0.0077,
    )

    info = store.promote_pending(conn, pid)

    assert info["action"] == "approved"

    mr = conn.execute(
        "SELECT net_return FROM monthly_returns WHERE fund_id=? AND date=?",
        ("fund_x", "2024-06-30"),
    ).fetchone()
    assert mr["net_return"] == pytest.approx(0.0077)


def test_promote_empty_slot_allowed(conn):
    """monthly_returns 无该 (fund_id, date) -> pending approve 插入新行."""
    pid = _add_pending_row(
        conn, fund_id="fund_x", date="2024-07-31", net_return=0.0042,
    )

    info = store.promote_pending(conn, pid)

    assert info["action"] == "approved"

    mr = conn.execute(
        "SELECT net_return, pattern_tag FROM monthly_returns "
        "WHERE fund_id=? AND date=?",
        ("fund_x", "2024-07-31"),
    ).fetchone()
    assert mr is not None
    assert mr["net_return"] == pytest.approx(0.0042)
    # _upsert_monthly_return 默认 pattern_tag='llm'
    assert mr["pattern_tag"] == "llm"


def test_promote_missing_pending_raises_keyerror(conn):
    """pending id 不存在 -> raise KeyError."""
    with pytest.raises(KeyError):
        store.promote_pending(conn, 99999)
