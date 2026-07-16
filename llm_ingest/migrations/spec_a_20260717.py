"""Spec A 迁移: funds 表补 fundmonitors 列 + 4 支基金种子 FundID +
Yarra/KKR 从 source_quote 抠 FundID + JCB 3 行月末日期修正 + GCI inception.

幂等: 反复跑 N 次, 结果与跑 1 次相同. 二次运行 applied 应为空.

调用: `from llm_ingest.migrations.spec_a_20260717 import apply`
或 CLI: `python3 -m llm_ingest.cli migrate`.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

MIGRATION_ID = "20260717_spec_a"

# fund_id -> (FundID, AccCode 可选)
_SEEDS: List[Tuple[str, int, Optional[str]]] = [
    ("bentham_global_income", 3312, None),
    ("jcb_active_bond", 1003, None),
    ("macquarie_australian_fixed_interest", 2107, None),
    ("smarter_money_lscf", 2332, None),
]

# 从 monthly_returns.source_quote 抠 FundID / AccCode 的基金
_EXTRACT_TARGETS: List[str] = [
    "yarra_enhanced_income_fund",
    "kkr_credit_income_fund",
]

# JCB 3 行月初 → 月末修正
_JCB_FUND_ID = "jcb_active_bond"
_JCB_DATE_FIXES: List[Tuple[str, str]] = [
    ("2026-04-01", "2026-04-30"),
    ("2026-05-01", "2026-05-31"),
    ("2026-06-01", "2026-06-30"),
]

# GCI 基金 inception
_GCI_FUND_ID = "gryphon_capital_income"
_GCI_INCEPTION = "2019-02-28"

_FUNDID_RE = re.compile(
    r"FundID=(\d+)(?:&AccCode=([A-Za-z0-9]+))?", re.IGNORECASE,
)


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    """PRAGMA table_info 探列."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    for r in rows:
        # sqlite3.Row 或 tuple, 兼容
        name = r[1] if not isinstance(r, sqlite3.Row) else r["name"]
        if name == col:
            return True
    return False


def _fund_exists(conn: sqlite3.Connection, fund_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM funds WHERE fund_id=?", (fund_id,)
    ).fetchone()
    return row is not None


def _extract_fundid_from_source_quote(
    conn: sqlite3.Connection, fund_id: str,
) -> Tuple[Optional[int], Optional[str]]:
    """从 monthly_returns.source_quote 抠 FundID/AccCode.

    只取 pattern_tag='fundmonitors_table' 的行, 第一行匹配即返回.
    抠不到返回 (None, None), 上层作 skipped 处理.
    """
    rows = conn.execute(
        """
        SELECT source_quote FROM monthly_returns
        WHERE fund_id=? AND pattern_tag='fundmonitors_table'
          AND source_quote IS NOT NULL
        ORDER BY date DESC
        """,
        (fund_id,),
    ).fetchall()
    for r in rows:
        sq = r[0] if not isinstance(r, sqlite3.Row) else r["source_quote"]
        if not sq:
            continue
        m = _FUNDID_RE.search(sq)
        if m:
            fid = int(m.group(1))
            acc = m.group(2) if m.group(2) else None
            return fid, acc
    return None, None


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, col: str, col_type: str,
    applied: List[str], skipped: List[str],
) -> None:
    if _has_column(conn, table, col):
        skipped.append(f"{table}.{col} already exists")
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    applied.append(f"{table}.{col}")


def _seed_fundmonitors_id(
    conn: sqlite3.Connection, fund_id: str, fmid: int, acc: Optional[str],
    applied: List[str], skipped: List[str],
) -> None:
    if not _fund_exists(conn, fund_id):
        skipped.append(f"seed {fund_id}={fmid} funds row missing")
        return
    row = conn.execute(
        "SELECT fundmonitors_fund_id, fundmonitors_acc_code FROM funds WHERE fund_id=?",
        (fund_id,),
    ).fetchone()
    existing_fid = row[0] if row else None
    existing_acc = row[1] if row else None
    if existing_fid is not None:
        skipped.append(
            f"seed {fund_id}={fmid} already set to {existing_fid}"
        )
        return
    conn.execute(
        """
        UPDATE funds SET fundmonitors_fund_id=?, fundmonitors_acc_code=?
        WHERE fund_id=? AND fundmonitors_fund_id IS NULL
        """,
        (fmid, acc if acc is not None else existing_acc, fund_id),
    )
    applied.append(f"seed {fund_id}={fmid}")


def _extract_and_seed(
    conn: sqlite3.Connection, fund_id: str,
    applied: List[str], skipped: List[str],
) -> None:
    if not _fund_exists(conn, fund_id):
        skipped.append(f"extract {fund_id} funds row missing")
        return
    row = conn.execute(
        "SELECT fundmonitors_fund_id FROM funds WHERE fund_id=?",
        (fund_id,),
    ).fetchone()
    existing_fid = row[0] if row else None
    if existing_fid is not None:
        skipped.append(
            f"extract {fund_id} already set to {existing_fid}"
        )
        return
    fid, acc = _extract_fundid_from_source_quote(conn, fund_id)
    if fid is None:
        skipped.append(
            f"extract {fund_id} no fundmonitors_table source_quote match"
        )
        return
    conn.execute(
        """
        UPDATE funds SET fundmonitors_fund_id=?, fundmonitors_acc_code=?
        WHERE fund_id=? AND fundmonitors_fund_id IS NULL
        """,
        (fid, acc, fund_id),
    )
    applied.append(
        f"extract {fund_id}={fid}"
        + (f"&AccCode={acc}" if acc else "")
    )


def _fix_jcb_dates(
    conn: sqlite3.Connection,
    applied: List[str], skipped: List[str],
) -> None:
    if not _fund_exists(conn, _JCB_FUND_ID):
        skipped.append(f"jcb dates: {_JCB_FUND_ID} funds row missing")
        return
    for old, new in _JCB_DATE_FIXES:
        # 已修 (new 存在, old 不存在) → skipped
        row_old = conn.execute(
            "SELECT 1 FROM monthly_returns WHERE fund_id=? AND date=?",
            (_JCB_FUND_ID, old),
        ).fetchone()
        row_new = conn.execute(
            "SELECT 1 FROM monthly_returns WHERE fund_id=? AND date=?",
            (_JCB_FUND_ID, new),
        ).fetchone()
        if row_old is None and row_new is not None:
            skipped.append(f"jcb {old}->{new} already fixed")
            continue
        if row_old is None and row_new is None:
            skipped.append(f"jcb {old}->{new} neither row exists")
            continue
        # old 存在: 若 new 也已存在, 唯一约束会炸, 我们提示 skipped
        if row_old is not None and row_new is not None:
            skipped.append(
                f"jcb {old}->{new} both rows exist (manual fix required)"
            )
            continue
        conn.execute(
            "UPDATE monthly_returns SET date=? WHERE fund_id=? AND date=?",
            (new, _JCB_FUND_ID, old),
        )
        applied.append(f"jcb {old}->{new}")


def _set_gci_inception(
    conn: sqlite3.Connection,
    applied: List[str], skipped: List[str],
) -> None:
    if not _fund_exists(conn, _GCI_FUND_ID):
        skipped.append(f"gci inception: {_GCI_FUND_ID} funds row missing")
        return
    row = conn.execute(
        "SELECT inception_date FROM funds WHERE fund_id=?",
        (_GCI_FUND_ID,),
    ).fetchone()
    cur = row[0] if row else None
    if cur == _GCI_INCEPTION:
        skipped.append(f"gci inception={_GCI_INCEPTION} already set")
        return
    if cur is not None:
        skipped.append(
            f"gci inception already set to {cur} (not overwriting)"
        )
        return
    conn.execute(
        "UPDATE funds SET inception_date=? WHERE fund_id=? AND inception_date IS NULL",
        (_GCI_INCEPTION, _GCI_FUND_ID),
    )
    applied.append(f"gci inception={_GCI_INCEPTION}")


def apply(conn: sqlite3.Connection) -> Dict[str, Any]:
    """幂等应用 Spec A 迁移.

    返回:
      {
        "migration_id": "20260717_spec_a",
        "applied": [str, ...],   # 本次实际改动
        "skipped": [str, ...],   # 已存在/条件不满足
      }
    """
    applied: List[str] = []
    skipped: List[str] = []

    # 1. 加两列
    _add_column_if_missing(
        conn, "funds", "fundmonitors_fund_id", "INTEGER",
        applied, skipped,
    )
    _add_column_if_missing(
        conn, "funds", "fundmonitors_acc_code", "TEXT",
        applied, skipped,
    )

    # 2. 4 支基金种子 FundID
    for fund_id, fmid, acc in _SEEDS:
        _seed_fundmonitors_id(conn, fund_id, fmid, acc, applied, skipped)

    # 2b. Yarra/KKR 从 source_quote 抠
    for fund_id in _EXTRACT_TARGETS:
        _extract_and_seed(conn, fund_id, applied, skipped)

    # 3. JCB 3 行月末日期
    _fix_jcb_dates(conn, applied, skipped)

    # 4. GCI inception
    _set_gci_inception(conn, applied, skipped)

    conn.commit()
    return {
        "migration_id": MIGRATION_ID,
        "applied": applied,
        "skipped": skipped,
    }
