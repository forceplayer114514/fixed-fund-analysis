"""skills DB 写入层:用 sqlite3 标准库直接操作共享 SQLite 数据库。

只写 funds + monthly_returns 两张表(原始数据 + NAV 复利重算)。
不 import webapp 任何代码,不装 SQLAlchemy。数据完整性原则:本模块只负责
忠实写入与 NAV 复利重算,缺口检查 / 异常检测在 extract.py / webapp 端。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

# 默认 DB 路径:<仓库根>/data/fund_analysis.db
# skills/lib/db.py -> parent(lib) -> parent(skills) -> parent(仓库根)
_DEFAULT_DB_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "fund_analysis.db"
)


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """返回一个配置好的 sqlite3 连接。

    db_path 优先级:显式参数 > 环境变量 FUND_DB_PATH > 默认路径。
    启用 PRAGMA foreign_keys(ON DELETE CASCADE 依赖它,且必须在事务开启前
    设置),row_factory 设为 sqlite3.Row 以支持按列名访问。
    """
    if db_path is None:
        db_path = os.environ.get("FUND_DB_PATH")
    if db_path is None:
        db_path = str(_DEFAULT_DB_PATH)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # 新连接无挂起事务,PRAGMA 在事务外执行,foreign_keys 生效。
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS(幂等)。建立 funds 与 monthly_returns。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS funds (
            fund_id TEXT PRIMARY KEY,
            fund_name TEXT NOT NULL UNIQUE,
            apir_code TEXT UNIQUE,
            confirmed_url TEXT NOT NULL,
            fetch_method TEXT NOT NULL,
            url_type TEXT NOT NULL,
            max_pdf_pages INTEGER,
            verified_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS monthly_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_id TEXT NOT NULL,
            date TEXT NOT NULL,
            net_return REAL NOT NULL,
            nav REAL NOT NULL,
            commentary_truth REAL,
            FOREIGN KEY (fund_id) REFERENCES funds(fund_id) ON DELETE CASCADE,
            UNIQUE(fund_id, date)
        );
        """
    )
    conn.commit()


def create_fund(
    conn: sqlite3.Connection,
    *,
    fund_id: str,
    fund_name: str,
    confirmed_url: str,
    fetch_method: str,
    url_type: str,
    apir_code: Optional[str] = None,
    max_pdf_pages: Optional[int] = None,
    verified_at: Optional[str] = None,
) -> None:
    """插入一只基金(纯 INSERT,不 upsert)。

    fund_name / apir_code 的 UNIQUE 冲突抛 sqlite3.IntegrityError;
    重复注册应报错而非静默覆盖。
    """
    conn.execute(
        """
        INSERT INTO funds
            (fund_id, fund_name, apir_code, confirmed_url, fetch_method,
             url_type, max_pdf_pages, verified_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fund_id, fund_name, apir_code, confirmed_url, fetch_method,
            url_type, max_pdf_pages, verified_at,
        ),
    )
    conn.commit()


def upsert_monthly_return(
    conn: sqlite3.Connection,
    *,
    fund_id: str,
    date: str,
    net_return: float,
    commentary_truth: Optional[float] = None,
) -> None:
    """插入或更新某月收益,随后重算该基金全部 NAV。

    ON CONFLICT(fund_id, date) 时更新 net_return;commentary_truth 用
    COALESCE(excluded, 旧值) 处理 -- 仅当传入非 None 时覆盖,传 None 则保留
    旧值。新插入行 nav 暂占位 1.0,由随后的 recompute_nav 重算为正确复利值。
    """
    conn.execute(
        """
        INSERT INTO monthly_returns (fund_id, date, net_return, nav, commentary_truth)
        VALUES (?, ?, ?, 1.0, ?)
        ON CONFLICT(fund_id, date) DO UPDATE SET
            net_return = excluded.net_return,
            commentary_truth = COALESCE(excluded.commentary_truth,
                                       monthly_returns.commentary_truth)
        """,
        (fund_id, date, net_return, commentary_truth),
    )
    conn.commit()
    recompute_nav(conn, fund_id)


def recompute_nav(conn: sqlite3.Connection, fund_id: str) -> None:
    """按 date 升序重算该基金全部 NAV:nav=1.0 起点,nav = nav*(1+net_return)。

    逐行 UPDATE(按主键 id 定位,避免依赖行顺序)。与 webapp crud.recompute_nav
    逻辑一致。
    """
    rows = conn.execute(
        "SELECT id, net_return FROM monthly_returns "
        "WHERE fund_id = ? ORDER BY date",
        (fund_id,),
    ).fetchall()
    nav = 1.0
    for row in rows:
        nav = nav * (1.0 + row["net_return"])
        conn.execute(
            "UPDATE monthly_returns SET nav = ? WHERE id = ?",
            (nav, row["id"]),
        )
    conn.commit()


def list_funds(conn: sqlite3.Connection) -> list[dict]:
    """返回所有基金(dict 列表,含全部字段),按 fund_name 升序。"""
    rows = conn.execute("SELECT * FROM funds ORDER BY fund_name").fetchall()
    return [dict(r) for r in rows]


def get_fund(conn: sqlite3.Connection, fund_id: str) -> Optional[dict]:
    """返回单只基金 dict,不存在返回 None。"""
    row = conn.execute(
        "SELECT * FROM funds WHERE fund_id = ?", (fund_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def get_monthly_returns(conn: sqlite3.Connection, fund_id: str) -> list[dict]:
    """返回该基金全部月度数据(date, net_return, nav, commentary_truth),
    按 date 升序。"""
    rows = conn.execute(
        "SELECT date, net_return, nav, commentary_truth "
        "FROM monthly_returns WHERE fund_id = ? ORDER BY date",
        (fund_id,),
    ).fetchall()
    return [dict(r) for r in rows]
