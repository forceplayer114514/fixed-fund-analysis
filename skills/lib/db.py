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


def _require_write_token() -> None:
    """写操作凭证检查：FUND_DB_WRITE_TOKEN 未设 -> PermissionError。

    软隔离：提高越权门槛（agent bash 内联无 token 失败）。绝对隔离须 harness
    sandbox（agent bash 与主对话 DB 写权限完全隔离），超出 skills 代码层。
    """
    if not os.environ.get("FUND_DB_WRITE_TOKEN"):
        raise PermissionError(
            "DB 写操作需要 FUND_DB_WRITE_TOKEN 环境变量（越权防护软隔离）。"
            "主对话跑 ingest.py 时由 _cli 注入；探测 agent bash 不继承。"
        )

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
    """CREATE TABLE IF NOT EXISTS(幂等)。建立 funds / monthly_returns /
    confirmed_gaps / pending_review。

    对已存在的旧库做幂等迁移：funds 表补 shareclass_prefix / inception_date /
    inception_assumed 列（SQLite 无 ADD COLUMN IF NOT EXISTS，先 PRAGMA
    table_info 查列是否存在）。confirmed_gaps / pending_review 对旧库由
    CREATE IF NOT EXISTS 自动补建。
    """
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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            shareclass_prefix TEXT,
            inception_date TEXT,
            inception_assumed INTEGER DEFAULT 0
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

        CREATE TABLE IF NOT EXISTS confirmed_gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_id TEXT NOT NULL,
            missing_month TEXT NOT NULL,
            exhausted_levels TEXT,
            checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds(fund_id) ON DELETE CASCADE,
            UNIQUE(fund_id, missing_month)
        );

        CREATE TABLE IF NOT EXISTS pending_review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_id TEXT NOT NULL,
            date TEXT NOT NULL,
            net_return REAL NOT NULL,
            source_quote TEXT,
            extract_method TEXT NOT NULL,
            gate_result TEXT,
            review_state TEXT NOT NULL DEFAULT 'pending',
            review_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds(fund_id) ON DELETE CASCADE
        );
        """
    )
    # 幂等迁移：旧库 funds 表缺列时补加。新库 CREATE 已含，PRAGMA 查不到才 ALTER，
    # 避免重复加列报错。
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(funds)").fetchall()]
    if "shareclass_prefix" not in cols:
        conn.execute("ALTER TABLE funds ADD COLUMN shareclass_prefix TEXT")
    if "inception_date" not in cols:
        conn.execute("ALTER TABLE funds ADD COLUMN inception_date TEXT")
    if "inception_assumed" not in cols:
        conn.execute("ALTER TABLE funds ADD COLUMN inception_assumed INTEGER DEFAULT 0")
    if "extractor_verified" not in cols:
        conn.execute("ALTER TABLE funds ADD COLUMN extractor_verified INTEGER DEFAULT 0")
        # 已有 monthly_returns 的老基金信任,标 1(未走 A4 准入,数据已入库)。
        # 新基金 discover 时无 monthly_returns(A4 首批全进 pending),不会被标。
        conn.execute(
            "UPDATE funds SET extractor_verified=1 WHERE fund_id IN "
            "(SELECT DISTINCT fund_id FROM monthly_returns)"
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
    shareclass_prefix: Optional[str] = None,
    inception_date: Optional[str] = None,
    inception_assumed: int = 0,
    extractor_verified: int = 0,
) -> None:
    """插入一只基金(纯 INSERT,不 upsert)。

    fund_name / apir_code 的 UNIQUE 冲突抛 sqlite3.IntegrityError;
    重复注册应报错而非静默覆盖。shareclass_prefix 存入 funds 表供 audit 读取
    （B 组跨份额类校验的前缀，None 表示单份额类/不参与跨份额类校验）。
    inception_date 为基金成立日(YYYY-MM-DD);无精确日时由调用方标
    inception_assumed=1 并以全链最早可得月作下界(见 strategies expected_range)。
    extractor_verified:A4 准入标记。新基金默认 0(generic 首批全进 pending,
    人工 verify_extractor 后标 1,update 侧增量才直通)。
    """
    _require_write_token()
    conn.execute(
        """
        INSERT INTO funds
            (fund_id, fund_name, apir_code, confirmed_url, fetch_method,
             url_type, max_pdf_pages, verified_at, shareclass_prefix,
             inception_date, inception_assumed, extractor_verified)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fund_id, fund_name, apir_code, confirmed_url, fetch_method,
            url_type, max_pdf_pages, verified_at, shareclass_prefix,
            inception_date, inception_assumed, extractor_verified,
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
    _require_write_token()
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
    _require_write_token()
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


# ---------- confirmed_gaps:穷尽后正常产出的缺口记录 ----------


def record_confirmed_gap(
    conn: sqlite3.Connection,
    *,
    fund_id: str,
    missing_month: str,
    exhausted_levels: Optional[str] = None,
) -> None:
    """记录某月为已穷尽缺口(UPSERT)。missing_month 格式 'YYYY-MM'。

    exhausted_levels 为逗号分隔字符串(如 'L0,L1,L2,L3'),记录穷尽到哪一级。
    重复记录同月 -> 更新 exhausted_levels + 刷新 checked_at,不新增行。
    """
    _require_write_token()
    conn.execute(
        """
        INSERT INTO confirmed_gaps (fund_id, missing_month, exhausted_levels)
        VALUES (?, ?, ?)
        ON CONFLICT(fund_id, missing_month) DO UPDATE SET
            exhausted_levels = excluded.exhausted_levels,
            checked_at = CURRENT_TIMESTAMP
        """,
        (fund_id, missing_month, exhausted_levels),
    )
    conn.commit()


def list_confirmed_gaps(conn: sqlite3.Connection, fund_id: str) -> list[dict]:
    """返回该基金已记录的缺口(missing_month/exhausted_levels/checked_at),
    按 missing_month 升序。"""
    rows = conn.execute(
        "SELECT missing_month, exhausted_levels, checked_at "
        "FROM confirmed_gaps WHERE fund_id = ? ORDER BY missing_month",
        (fund_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def remove_confirmed_gap(
    conn: sqlite3.Connection, fund_id: str, missing_month: str
) -> None:
    """补录该月后从 confirmed_gaps 移除。无该行则无操作(幂等)。"""
    _require_write_token()
    conn.execute(
        "DELETE FROM confirmed_gaps WHERE fund_id = ? AND missing_month = ?",
        (fund_id, missing_month),
    )
    conn.commit()


# ---------- pending_review:LLM 兜底提取 / |r|<0.5 超限值,待人工裁决 ----------


def add_pending_review(
    conn: sqlite3.Connection,
    *,
    fund_id: str,
    date: str,
    net_return: float,
    source_quote: Optional[str] = None,
    extract_method: str,
    gate_result: Optional[str] = None,
    review_reason: Optional[str] = None,
) -> int:
    """写一条待人工审核记录,返回自增 id。

    extract_method: 'code'(代码提取超限)或 'llm'(LLM 兜底提取)。
    review_state 默认 'pending',永不直通 monthly_returns -- 过同一 gate_check
    仅获准入审核队列,promote_pending 后才入库(修正3.1.2)。
    """
    _require_write_token()
    cur = conn.execute(
        """
        INSERT INTO pending_review
            (fund_id, date, net_return, source_quote, extract_method,
             gate_result, review_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (fund_id, date, net_return, source_quote, extract_method,
         gate_result, review_reason),
    )
    conn.commit()
    return cur.lastrowid


def list_pending_review(
    conn: sqlite3.Connection,
    fund_id: Optional[str] = None,
    state: Optional[str] = "pending",
) -> list[dict]:
    """列出待审核记录。fund_id=None 列全部;state=None 列全部状态(默认 pending)。"""
    sql = (
        "SELECT id, fund_id, date, net_return, source_quote, extract_method, "
        "gate_result, review_state, review_reason, created_at FROM pending_review"
    )
    clauses: list[str] = []
    params: list = []
    if fund_id is not None:
        clauses.append("fund_id = ?")
        params.append(fund_id)
    if state is not None:
        clauses.append("review_state = ?")
        params.append(state)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def promote_pending(conn: sqlite3.Connection, review_id: int) -> dict:
    """人工审核通过:把 pending_review 行的 net_return 入 monthly_returns,
    并标记 review_state='approved'。返回 {fund_id, date}。

    走与代码提取同一条 upsert_monthly_return(含 NAV 重算),保证人工 promote
    的数据与自动入库走完全相同的复利路径,无旁路。
    """
    _require_write_token()
    row = conn.execute(
        "SELECT fund_id, date, net_return FROM pending_review WHERE id = ?",
        (review_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"pending_review id={review_id} 不存在")
    upsert_monthly_return(
        conn,
        fund_id=row["fund_id"],
        date=row["date"],
        net_return=row["net_return"],
    )
    conn.execute(
        "UPDATE pending_review SET review_state='approved' WHERE id=?",
        (review_id,),
    )
    conn.commit()
    return {"fund_id": row["fund_id"], "date": row["date"]}


def list_stale_pending_reviews(
    conn: sqlite3.Connection, days: int = 14
) -> list[dict]:
    """滞留报告:review_state='pending' 且 created_at 早于 N 天前。

    防 pending_review 变静默坟场:update 每次跑完输出此清单(M6)。
    created_at 为 UTC CURRENT_TIMESTAMP,datetime('now',-Nd) 同基准比较。
    """
    rows = conn.execute(
        "SELECT id, fund_id, date, net_return, source_quote, extract_method, "
        "gate_result, review_reason, created_at FROM pending_review "
        "WHERE review_state='pending' AND created_at < datetime('now', ?)",
        (f"-{days} days",),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- A4:generic 提取器准入验证(extractor_verified) ----------


def get_extractor_verified(conn: sqlite3.Connection, fund_id: str) -> int:
    """读 extractor_verified 标记(0/1)。基金不存在返 0(视为未验证,走 pending)。"""
    row = conn.execute(
        "SELECT extractor_verified FROM funds WHERE fund_id=?", (fund_id,)
    ).fetchone()
    return int(row["extractor_verified"]) if row else 0


def verify_extractor(conn: sqlite3.Connection, fund_id: str) -> dict:
    """人工审核通过 generic 首批后:标 extractor_verified=1 + 批量 promote 该基金
    review_reason='generic_first_use' 的 pending(走同一 upsert_monthly_return)。

    仅 promote generic_first_use(信任已抽审),不碰 ambiguous_subject / 超限值
    pending(那些 review_reason 不同,需逐条人工 promote_pending)。
    返回 {fund_id, promoted_count}。基金不存在抛 KeyError。
    """
    _require_write_token()
    exists = conn.execute(
        "SELECT 1 FROM funds WHERE fund_id=?", (fund_id,)
    ).fetchone()
    if not exists:
        raise KeyError(f"fund_id={fund_id} 不存在")
    rows = conn.execute(
        "SELECT id, date, net_return FROM pending_review "
        "WHERE fund_id=? AND review_state='pending' "
        "AND review_reason='generic_first_use'",
        (fund_id,),
    ).fetchall()
    for row in rows:
        upsert_monthly_return(
            conn, fund_id=fund_id, date=row["date"], net_return=row["net_return"],
        )
        conn.execute(
            "UPDATE pending_review SET review_state='approved' WHERE id=?",
            (row["id"],),
        )
    conn.execute(
        "UPDATE funds SET extractor_verified=1 WHERE fund_id=?", (fund_id,)
    )
    conn.commit()
    return {"fund_id": fund_id, "promoted_count": len(rows)}
