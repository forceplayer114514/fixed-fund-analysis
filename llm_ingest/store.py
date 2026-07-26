"""Phase 3 写库层.

stdlib sqlite3 直写 funds / monthly_returns / pending_review / confirmed_gaps.
schema 已由 skills/lib/db.py ensure_tables 建好, 本模块不重复建表 (幂等 CREATE IF NOT EXISTS
放在 ensure_tables_if_missing, 供 webapp 空库初始化时用).

决策:
  两闸 (quote + rolling + field_type + antifab) 全过 -> monthly_returns (含 NAV 重算)
  任一未过 / not_found=True / parse_error -> pending_review, candidates_json 存原始响应 + 四 gate reason
  三级 fallback 穷尽仍 not_found -> confirmed_gaps

date 存 "YYYY-MM-末日" (schema TEXT YYYY-MM-DD; skills 惯例月末, 与 L3 表格一致)。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .extract import Extraction
from .verify import (
    QuoteCheck,
    RollingCheck,
    check_anti_fabrication,
    check_field_type,
)

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO / "data" / "fund_analysis.db"

# 权威源白名单: 走过 gate/两闸的通路, pending 不允许覆盖
#   - fundmonitors_table: L3 表格通路 (缺口/反捏造/字段/YTD 复利 4 gate 已过)
#   - llm: L1 PDF 通路 (quote/rolling/field_type/antifab 四闸已过)
# 未在白名单内 (如 NULL / 'nta_net_return_label' 等 skills-era 遗留 tag) 允许被 pending 覆盖
# (对齐 Spec B 洗数据目标)。
_AUTHORITATIVE_TAGS = frozenset({"fundmonitors_table", "llm"})


# ---------- 连接 ----------

def resolve_db_path() -> Path:
    """解析 DB 路径, 与 webapp/backend/app/config.py 的 DATABASE_URL 共用同一来源.

    优先级: FUND_DB_PATH (llm_ingest 专用, 明确覆盖) > DATABASE_URL (webapp 用,
    sqlite:/// 前缀) > 仓库根默认路径。

    两个 env 变量各自独立时 (只设 DATABASE_URL 不设 FUND_DB_PATH), ingest 写库与
    webapp 读库曾各自解析到不同文件 -- 摄取 job 报 succeeded 但数据进了另一个库,
    API 一行也查不到 (2026-07 教训)。此处统一收口, 只设其一即可两边一致。
    """
    env = os.environ.get("FUND_DB_PATH")
    if env:
        return Path(env)
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("sqlite:///"):
        return Path(db_url[len("sqlite:///"):])
    return DEFAULT_DB_PATH


def open_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """打开 sqlite3 连接, foreign keys 打开, row_factory 设为 dict-like."""
    if db_path is None:
        db_path = resolve_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_tables_if_missing(conn: sqlite3.Connection) -> None:
    """幂等建表. 生产库 schema 已由 skills/lib/db.py 建好, 此函数仅在空库时兜底.

    只建 llm_ingest 直接写的 4 张: funds / monthly_returns / confirmed_gaps / pending_review.
    其他表 (rba_cash_rates / fund_metrics / anomalies / ai_reports) 由 webapp 自行建.
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
            inception_assumed INTEGER DEFAULT 0,
            extractor_verified INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS monthly_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_id TEXT NOT NULL,
            date TEXT NOT NULL,
            net_return REAL NOT NULL,
            nav REAL NOT NULL,
            commentary_truth REAL,
            verify_windows INTEGER,
            source_quote TEXT,
            pattern_tag TEXT,
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
            candidates_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds(fund_id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()


# ---------- funds ----------

def upsert_fund(
    conn: sqlite3.Connection,
    *,
    fund_id: str,
    fund_name: str,
    confirmed_url: str,
    fetch_method: str = "llm_ingest",
    url_type: str = "archive",
    apir_code: Optional[str] = None,
    max_pdf_pages: Optional[int] = None,
    inception_date: Optional[str] = None,
    inception_assumed: int = 0,
) -> None:
    """插入或更新基金元信息 (fund_id 主键 upsert).

    fetch_method 默认 'llm_ingest' (与 skills 侧 'code'/'solver' 区分).
    url_type: 'archive' (归档页) / 'latest_only' (只挂最新) / 'html_table' (JCB 类).
    """
    conn.execute(
        """
        INSERT INTO funds
            (fund_id, fund_name, apir_code, confirmed_url, fetch_method,
             url_type, max_pdf_pages, inception_date, inception_assumed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fund_id) DO UPDATE SET
            fund_name = excluded.fund_name,
            apir_code = COALESCE(excluded.apir_code, funds.apir_code),
            confirmed_url = excluded.confirmed_url,
            fetch_method = excluded.fetch_method,
            url_type = excluded.url_type,
            max_pdf_pages = COALESCE(excluded.max_pdf_pages, funds.max_pdf_pages),
            inception_date = COALESCE(excluded.inception_date, funds.inception_date),
            inception_assumed = excluded.inception_assumed
        """,
        (
            fund_id, fund_name, apir_code, confirmed_url, fetch_method,
            url_type, max_pdf_pages, inception_date, inception_assumed,
        ),
    )
    conn.commit()


def slugify_fund_id(name: str) -> str:
    """基金名 -> fund_id slug. 小写, 非字母数字 -> 下划线, 去首尾, 折叠连续下划线。

    从 webapp/backend/app/routers/ingest.py::_slugify_fund_id 下沉于此, 供
    ingest.py (start_ingest 首次生成) 与 rename_fund_id 的调用方 (拿到官方
    名称后重新生成) 两处共用, 不留两份实现。

    例: 'Bentham Global Income Fund' -> 'bentham_global_income_fund'
    """
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    s = re.sub(r"_+", "_", s)
    return s or "fund"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    """sqlite_master 查表是否存在. anomalies/fund_metrics 由 webapp 的 SQLAlchemy
    建, ensure_tables_if_missing 不建这两张 -- rename 前必须先探测, 否则空库/
    纯 llm_ingest 单测环境 UPDATE 会报 'no such table'。"""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


_RENAME_CHILD_TABLES = (
    "monthly_returns", "anomalies", "fund_metrics",
    "confirmed_gaps", "pending_review",
)


def rename_fund_id(
    conn: sqlite3.Connection,
    old_fund_id: str,
    new_fund_id: str,
    new_fund_name: str,
) -> bool:
    """discovery/L1 拿到官方名称后, 把临时 fund_id/fund_name 换成正式值.

    返回 True = 已 rename, False = 跳过 (new==old / fund_id 撞车 / fund_name
    撞车 -- funds.fund_name 有 UNIQUE 约束)。

    顺序 (PRAGMA foreign_keys=ON, SQLite FK 默认 NO ACTION, 必须先建新父行才能
    把子表指过去, 不能直接 UPDATE funds SET fund_id=):
      1. new_fund_id 已存在 于 funds, 或 new_fund_name 被别的 fund_id 占用 ->
         放弃, 不动数据, 返回 False.
      2. 动态读旧行全部列 (不硬编码列名, 防未来 migration 加列漏字段), 按新
         fund_id/fund_name 插一份新行, 其余列原样复制.
      3. 对存在的子表 UPDATE fund_id=old -> new.
      4. DELETE 旧 funds 行.
      5. 一次性 conn.commit() (前面步骤都不提交, 失败靠调用方 conn.rollback()
         保证整体原子性)。
    不动 pdf_cache 目录 (调用方 I/O 层面自己 rename, 这里只管 DB)。
    """
    if new_fund_id == old_fund_id:
        return False
    if conn.execute(
        "SELECT 1 FROM funds WHERE fund_id=?", (new_fund_id,)
    ).fetchone() is not None:
        return False
    if conn.execute(
        "SELECT 1 FROM funds WHERE fund_name=? AND fund_id<>?",
        (new_fund_name, old_fund_id),
    ).fetchone() is not None:
        return False

    old_row = conn.execute(
        "SELECT * FROM funds WHERE fund_id=?", (old_fund_id,)
    ).fetchone()
    if old_row is None:
        return False

    cols = list(old_row.keys())
    values = [
        new_fund_id if c == "fund_id"
        else new_fund_name if c == "fund_name"
        else old_row[c]
        for c in cols
    ]
    placeholders = ",".join("?" * len(cols))
    conn.execute(
        f"INSERT INTO funds ({','.join(cols)}) VALUES ({placeholders})",
        values,
    )

    for table in _RENAME_CHILD_TABLES:
        if _table_exists(conn, table):
            conn.execute(
                f"UPDATE {table} SET fund_id=? WHERE fund_id=?",
                (new_fund_id, old_fund_id),
            )

    conn.execute("DELETE FROM funds WHERE fund_id=?", (old_fund_id,))
    conn.commit()
    return True


# ---------- monthly_returns + NAV 重算 ----------

def _upsert_monthly_return(
    conn: sqlite3.Connection,
    *,
    fund_id: str,
    date: str,
    net_return: float,
    verify_windows: Optional[int] = None,
    source_quote: Optional[str] = None,
    pattern_tag: Optional[str] = "llm",
) -> None:
    """写一行月度. 走完立即 recompute_nav (与 skills 侧一致的语义).

    同时清除该月的 confirmed_gaps 残留 (若之前该月因下载失败/校验未过记过缺口,
    现在既然写入了 monthly_returns, 就不再是缺口 -- 否则 gap_count 会永久误报,
    与 write_table_records 对同一问题的处理保持一致)。
    """
    conn.execute(
        """
        INSERT INTO monthly_returns
            (fund_id, date, net_return, nav, verify_windows, source_quote, pattern_tag)
        VALUES (?, ?, ?, 1.0, ?, ?, ?)
        ON CONFLICT(fund_id, date) DO UPDATE SET
            net_return = excluded.net_return,
            verify_windows = COALESCE(excluded.verify_windows, monthly_returns.verify_windows),
            source_quote = COALESCE(excluded.source_quote, monthly_returns.source_quote),
            pattern_tag = COALESCE(excluded.pattern_tag, monthly_returns.pattern_tag)
        """,
        (fund_id, date, net_return, verify_windows, source_quote, pattern_tag),
    )
    conn.execute(
        "DELETE FROM confirmed_gaps WHERE fund_id=? AND missing_month=?",
        (fund_id, date[:7]),
    )
    conn.commit()
    _recompute_nav(conn, fund_id)


def _recompute_nav(conn: sqlite3.Connection, fund_id: str) -> None:
    """按 date 升序重算 NAV, nav=1.0 起点."""
    rows = conn.execute(
        "SELECT id, net_return FROM monthly_returns WHERE fund_id=? ORDER BY date",
        (fund_id,),
    ).fetchall()
    nav = 1.0
    for r in rows:
        nav = nav * (1.0 + r["net_return"])
        conn.execute("UPDATE monthly_returns SET nav=? WHERE id=?", (nav, r["id"]))
    conn.commit()


def write_table_records(
    conn: sqlite3.Connection,
    *,
    fund_id: str,
    records: List[Tuple[str, float]],
    source_url: str,
    pattern_tag: str = "fundmonitors_table",
) -> int:
    """L3 通路 (表格源) 直入 monthly_returns。

    records 已过 gate_check_table (缺口/反捏造/字段类型/YTD 复利), 无 per-month
    quote 与 rolling, 因此:
      - source_quote = f"L3 fundmonitors table: {source_url}"  (可溯)
      - verify_windows = 0 (无 rolling 交叉, 但 YTD 已过, 表 gate 已过)
      - pattern_tag = 'fundmonitors_table' 标记非 LLM 提取, 与 PDF 通路区分

    返回入库行数。走完统一 recompute_nav。
    """
    n = 0
    for date, net_ret in records:
        conn.execute(
            """
            INSERT INTO monthly_returns
                (fund_id, date, net_return, nav, verify_windows, source_quote, pattern_tag)
            VALUES (?, ?, ?, 1.0, 0, ?, ?)
            ON CONFLICT(fund_id, date) DO UPDATE SET
                net_return = excluded.net_return,
                verify_windows = COALESCE(excluded.verify_windows, monthly_returns.verify_windows),
                source_quote = COALESCE(excluded.source_quote, monthly_returns.source_quote),
                pattern_tag = COALESCE(excluded.pattern_tag, monthly_returns.pattern_tag)
            """,
            (fund_id, date, net_ret,
             f"L3 fundmonitors table: {source_url}", pattern_tag),
        )
        n += 1
    # 清除同月 confirmed_gaps: 该月既然 L3 补齐, 就不再是缺口, 免前端读 gap 表标红
    if records:
        months = tuple(sorted({d[:7] for d, _ in records}))
        placeholders = ",".join("?" * len(months))
        conn.execute(
            f"DELETE FROM confirmed_gaps WHERE fund_id=? AND missing_month IN ({placeholders})",
            (fund_id, *months),
        )
    conn.commit()
    _recompute_nav(conn, fund_id)
    return n


# ---------- pending_review ----------

def _add_pending(
    conn: sqlite3.Connection,
    *,
    fund_id: str,
    date: str,
    net_return: float,
    source_quote: Optional[str],
    review_reason: str,
    gate_result: str,
    candidates_json: Optional[str],
) -> int:
    """写 pending, extract_method 固定 'llm' (与 skills 'code'/'solver' 区分)."""
    cur = conn.execute(
        """
        INSERT INTO pending_review
            (fund_id, date, net_return, source_quote, extract_method,
             gate_result, review_reason, candidates_json)
        VALUES (?, ?, ?, ?, 'llm', ?, ?, ?)
        """,
        (fund_id, date, net_return, source_quote, gate_result,
         review_reason, candidates_json),
    )
    conn.commit()
    return cur.lastrowid


# ---------- confirmed_gaps ----------

def record_confirmed_gap(
    conn: sqlite3.Connection,
    *,
    fund_id: str,
    missing_month: str,
    exhausted_levels: str = "L1,L2,L3",
) -> None:
    """记穷尽后的缺口 (UPSERT). missing_month 'YYYY-MM'."""
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


# ---------- 决策核心: 写 extraction 结果 ----------

@dataclass(frozen=True)
class WriteDecision:
    """写库决策记录, 供上层 (ingest/cli) 汇总统计."""
    ym: str
    action: str  # 'monthly' | 'pending' | 'gap' | 'skip'
    review_id: Optional[int] = None  # pending 时的 lastrowid
    gate_summary: str = ""            # "q1r1f1a1" 之类的紧凑串
    reason: str = ""


def _ym_to_date(ym: str) -> str:
    """'YYYY-MM' -> 'YYYY-MM-末日' (schema TEXT YYYY-MM-DD; skills 惯例月末)。

    与 L3 fundmonitors 表格通路的 `_get_last_day_of_month` 一致, 避免同一月既
    有 '-01' 又有 '-31' 两行, 前端会误判缺口。既有 skills 数据全部月末格式,
    llm_ingest 早期注释误写月初已修正。
    """
    import datetime as _dt
    y, m = int(ym[:4]), int(ym[5:7])
    if m == 12:
        return f"{y}-12-31"
    return (_dt.date(y, m + 1, 1) - _dt.timedelta(days=1)).strftime("%Y-%m-%d")


def write_extraction(
    conn: sqlite3.Connection,
    *,
    fund_id: str,
    ex: Extraction,
    quote_check: QuoteCheck,
    rolling_check: RollingCheck,
    identity_check: QuoteCheck,
    monthly_history: Dict[str, float],
    exhausted_levels: str = "L1,L2,L3",
) -> WriteDecision:
    """核心写库决策. 传入五个 check 结果, 决定 monthly / pending / gap.

    monthly_history: {ym: net_return} 已入库的历史, 用于 anti-fab 检测.
    exhausted_levels: not_found 时该月记 confirmed_gaps 的 exhausted 字段.

    决策矩阵:
      parse_error / not_found + net_return=None -> confirmed_gaps
      net_return 存在 + 五闸全过 -> monthly_returns
      net_return 存在 + 任一闸未过 -> pending_review

    identity_check (闸 5, Spec G 10.5): 文档抬头基金名是否与目标基金一致。
    设为必填参数而非可选, 是要让"不核对身份就写库"在类型层面不可能发生 --
    这道闸原本缺失, 导致兄弟基金月报可静默入库。

    reason 用 review_reason 落库: 'quote_failed' / 'rolling_failed' /
    'field_type_failed' / 'antifab_failed' / 'identity_failed' /
    'parse_error' / 'multi:xxx'
    """
    date = _ym_to_date(ex.ym)

    # 完全无值 / parse 失败 -> gap (穷尽后)
    if ex.parse_error or ex.not_found or ex.net_return is None:
        record_confirmed_gap(
            conn, fund_id=fund_id, missing_month=ex.ym,
            exhausted_levels=exhausted_levels,
        )
        return WriteDecision(
            ym=ex.ym, action="gap",
            reason=ex.parse_error or ("not_found" if ex.not_found else "no_value"),
        )

    # 五道闸
    f_ok, f_reason = check_field_type(ex.net_return)
    # anti-fab 用历史倒序
    hist_recent = sorted(monthly_history.items(), reverse=True)
    a_ok, a_reason = check_anti_fabrication(ex.net_return, ex.ym, hist_recent)

    summary = (
        f"q{int(quote_check.passed)}"
        f"r{int(rolling_check.passed)}"
        f"f{int(f_ok)}"
        f"a{int(a_ok)}"
        f"i{int(identity_check.passed)}"
    )
    all_ok = (
        quote_check.passed and rolling_check.passed
        and f_ok and a_ok and identity_check.passed
    )

    if all_ok:
        _upsert_monthly_return(
            conn,
            fund_id=fund_id,
            date=date,
            net_return=ex.net_return,
            verify_windows=rolling_check.windows_verified,
            source_quote=ex.source_quote,
        )
        return WriteDecision(ym=ex.ym, action="monthly", gate_summary=summary, reason="ok")

    # pending: 汇总失败原因
    reasons = []
    if not quote_check.passed:
        reasons.append(f"quote:{quote_check.reason}")
    if not rolling_check.passed:
        reasons.append(f"rolling:{rolling_check.reason}")
    if not f_ok:
        reasons.append(f"field:{f_reason}")
    if not a_ok:
        reasons.append(f"antifab:{a_reason}")
    if not identity_check.passed:
        reasons.append(f"identity:{identity_check.reason}")
    reason = "|".join(reasons)

    # candidates_json 存原始响应+五 gate reason, 供人工审核看到原始上下文
    payload: Dict[str, Any] = {
        "raw": ex.raw,
        "measure": ex.measure,
        "measure_label_in_pdf": ex.measure_label_in_pdf,
        "rolling": ex.rolling,
        "gate_reasons": {
            "quote": quote_check.reason,
            "rolling": rolling_check.reason,
            "field_type": f_reason,
            "antifab": a_reason,
            "identity": identity_check.reason,
        },
    }
    review_id = _add_pending(
        conn,
        fund_id=fund_id,
        date=date,
        net_return=ex.net_return,
        source_quote=ex.source_quote,
        review_reason=reason,
        gate_result=summary,
        candidates_json=json.dumps(payload, ensure_ascii=False),
    )
    return WriteDecision(
        ym=ex.ym, action="pending", review_id=review_id,
        gate_summary=summary, reason=reason,
    )


# ---------- 读接口 (供 webapp / CLI 用) ----------

def load_monthly_history(
    conn: sqlite3.Connection, fund_id: str
) -> Dict[str, float]:
    """ym (YYYY-MM) -> net_return, 用于 anti-fab / rolling 校验的历史输入."""
    rows = conn.execute(
        "SELECT date, net_return FROM monthly_returns WHERE fund_id=? ORDER BY date",
        (fund_id,),
    ).fetchall()
    return {r["date"][:7]: float(r["net_return"]) for r in rows}


def list_pending(
    conn: sqlite3.Connection,
    fund_id: Optional[str] = None,
    state: str = "pending",
) -> List[Dict[str, Any]]:
    """列 pending_review 记录. state=None 列全部."""
    sql = (
        "SELECT id, fund_id, date, net_return, source_quote, extract_method, "
        "gate_result, review_state, review_reason, candidates_json, created_at "
        "FROM pending_review"
    )
    clauses: List[str] = []
    params: List[Any] = []
    if fund_id is not None:
        clauses.append("fund_id=?")
        params.append(fund_id)
    if state is not None:
        clauses.append("review_state=?")
        params.append(state)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def promote_pending(conn: sqlite3.Connection, review_id: int) -> Dict[str, Any]:
    """人工审核通过: pending 行 net_return 走 upsert_monthly_return (含 NAV 重算),
    并标 review_state='approved'. 返回 {fund_id, date, action, [existing_tag]}.

    Guard (P4 扩权威源白名单): 若目标 (fund_id, date) 已被 _AUTHORITATIVE_TAGS 覆盖
    (当前包含 L3 表格通路 'fundmonitors_table' 与 L1 PDF LLM 通路 'llm'), 拒绝覆盖 --
    两条通路皆已过 gate/两闸校验, 优于人工挪进来的 pending 单值。此处只把 pending 标
    'rejected' 附因由, monthly_returns 不动, action 返 'skipped_authoritative_covered',
    附 existing_tag 让前端告诉审核人是被哪种权威源挡的。
    未纳入白名单的 pattern_tag (NULL / 'nta_net_return_label' 等 skills-era 遗留) 允许被
    pending 覆盖 -- 对齐 Spec B 洗数据目标, 那些行未经现有 gate。
    KKR 现场教训: skills 旧候选 pending 与权威源值不同 -> approve 会污染 monthly_returns。
    """
    row = conn.execute(
        "SELECT fund_id, date, net_return, source_quote FROM pending_review WHERE id=?",
        (review_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"pending_review id={review_id} 不存在")
    fund_id = row["fund_id"]
    date = row["date"]
    # 检查同月是否已被权威源覆盖
    existing = conn.execute(
        "SELECT pattern_tag, net_return FROM monthly_returns WHERE fund_id=? AND date=?",
        (fund_id, date),
    ).fetchone()
    if existing and existing["pattern_tag"] in _AUTHORITATIVE_TAGS:
        existing_tag = existing["pattern_tag"]
        reason_text = (
            f"skipped_authoritative_covered: {existing_tag} 源值 "
            f"{existing['net_return']} 已入库, pending 值 {row['net_return']} 未采纳"
        )
        conn.execute(
            "UPDATE pending_review SET review_state='rejected', review_reason=? "
            "WHERE id=?",
            (reason_text, review_id),
        )
        conn.commit()
        return {
            "fund_id": fund_id, "date": date,
            "action": "skipped_authoritative_covered",
            "existing_tag": existing_tag,
        }
    _upsert_monthly_return(
        conn, fund_id=fund_id, date=date,
        net_return=float(row["net_return"]),
        source_quote=row["source_quote"],
    )
    conn.execute(
        "UPDATE pending_review SET review_state='approved' WHERE id=?",
        (review_id,),
    )
    conn.commit()
    return {"fund_id": fund_id, "date": date, "action": "approved"}


def reject_pending(
    conn: sqlite3.Connection, review_id: int, reason: str = ""
) -> None:
    """人工审核拒绝: 只标状态, 不写 monthly_returns."""
    conn.execute(
        "UPDATE pending_review SET review_state='rejected', review_reason=? WHERE id=?",
        (reason, review_id),
    )
    conn.commit()


def list_gaps(conn: sqlite3.Connection, fund_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT missing_month, exhausted_levels, checked_at FROM confirmed_gaps "
        "WHERE fund_id=? ORDER BY missing_month",
        (fund_id,),
    ).fetchall()
    return [dict(r) for r in rows]
