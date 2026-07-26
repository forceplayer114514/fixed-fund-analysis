"""Phase 3 store.py 单测. 用临时 sqlite 文件 (不碰生产库)."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest import store, verify
from llm_ingest.extract import Extraction
from llm_ingest.verify import QuoteCheck, RollingCheck


@pytest.fixture()
def conn():
    """临时 sqlite. tmp file 而非 :memory: (让 open_conn 走同一路径)."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    c = sqlite3.connect(tmp.name)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    store.ensure_tables_if_missing(c)
    # 先建 fund, monthly_returns 才能 FK 通过
    store.upsert_fund(
        c, fund_id="fund_x", fund_name="Fund X",
        confirmed_url="https://example.com/archive",
    )
    yield c
    c.close()
    Path(tmp.name).unlink(missing_ok=True)


# ---------- upsert_fund ----------

def test_upsert_fund_insert_then_update(conn):
    store.upsert_fund(
        conn, fund_id="fund_y", fund_name="Fund Y",
        confirmed_url="https://a.com", apir_code="ABC1234AU",
    )
    row = conn.execute("SELECT * FROM funds WHERE fund_id='fund_y'").fetchone()
    assert row["fund_name"] == "Fund Y"
    assert row["apir_code"] == "ABC1234AU"

    # 更新 url, apir 保留 (COALESCE)
    store.upsert_fund(
        conn, fund_id="fund_y", fund_name="Fund Y",
        confirmed_url="https://b.com", apir_code=None,
    )
    row = conn.execute("SELECT * FROM funds WHERE fund_id='fund_y'").fetchone()
    assert row["confirmed_url"] == "https://b.com"
    assert row["apir_code"] == "ABC1234AU"  # 保留


# ---------- 写库决策: monthly ----------

def test_write_extraction_all_pass_goes_monthly(conn):
    ex = Extraction(
        ym="2025-03",
        net_return=0.0065,
        source_quote="Fund returned 0.65% (net of fees).",
        measure="net_monthly",
        measure_label_in_pdf="Net Return",
        rolling={"1mo": 0.65, "3mo": None, "6mo": None, "12mo": None},
        not_found=False,
        raw={"net_return_pct": 0.65},
    )
    q = QuoteCheck(passed=True, reason="ok")
    r = RollingCheck(passed=True, reason="ok", windows_verified=1)
    dec = store.write_extraction(
        conn, fund_id="fund_x", ex=ex,
        quote_check=q, rolling_check=r, identity_check=QuoteCheck(passed=True, reason="ok"),
        monthly_history={},
    )
    assert dec.action == "monthly"
    assert dec.gate_summary == "q1r1f1a1i1"
    row = conn.execute(
        "SELECT * FROM monthly_returns WHERE fund_id='fund_x'"
    ).fetchone()
    assert row["date"] == "2025-03-31"
    assert abs(row["net_return"] - 0.0065) < 1e-9
    assert abs(row["nav"] - 1.0065) < 1e-9  # NAV 重算
    assert row["verify_windows"] == 1
    assert row["source_quote"].startswith("Fund returned")
    assert row["pattern_tag"] == "llm"


def test_nav_recompute_across_multiple_months(conn):
    # 依次写 3 月, NAV 应链式复利
    exs = [
        (0.01, "2025-01"),
        (0.02, "2025-02"),
        (0.005, "2025-03"),
    ]
    for ret, ym in exs:
        ex = Extraction(
            ym=ym, net_return=ret, source_quote=f"return {ret*100}%",
            measure="net_monthly", measure_label_in_pdf="Net",
            rolling={"1mo": None, "3mo": None, "6mo": None, "12mo": None},
            not_found=False, raw={},
        )
        store.write_extraction(
            conn, fund_id="fund_x", ex=ex,
            quote_check=QuoteCheck(True, "ok"),
            rolling_check=RollingCheck(True, "ok", 0),
            identity_check=QuoteCheck(True, "ok"),
            monthly_history=store.load_monthly_history(conn, "fund_x"),
        )
    rows = conn.execute(
        "SELECT date, nav FROM monthly_returns WHERE fund_id='fund_x' ORDER BY date"
    ).fetchall()
    assert len(rows) == 3
    # 1.01 * 1.02 * 1.005
    expected_final = 1.01 * 1.02 * 1.005
    assert abs(rows[-1]["nav"] - expected_final) < 1e-9


# ---------- 写库决策: pending ----------

def test_quote_fail_goes_pending(conn):
    ex = Extraction(
        ym="2025-04", net_return=0.008,
        source_quote="the fund gained 0.8% for the month",
        measure="net_monthly", measure_label_in_pdf="Return",
        rolling={"1mo": 0.8, "3mo": None, "6mo": None, "12mo": None},
        not_found=False, raw={"note": "some raw"},
    )
    q = QuoteCheck(passed=False, reason="value_0.8_not_in_quote")
    r = RollingCheck(passed=True, reason="ok", windows_verified=0)
    dec = store.write_extraction(
        conn, fund_id="fund_x", ex=ex,
        quote_check=q, rolling_check=r, identity_check=QuoteCheck(passed=True, reason="ok"),
        monthly_history={},
    )
    assert dec.action == "pending"
    assert dec.gate_summary == "q0r1f1a1i1"
    assert "quote:value_0.8_not_in_quote" in dec.reason

    row = conn.execute(
        "SELECT * FROM pending_review WHERE fund_id='fund_x'"
    ).fetchone()
    assert row["extract_method"] == "llm"
    assert row["review_state"] == "pending"
    assert row["gate_result"] == "q0r1f1a1i1"
    payload = json.loads(row["candidates_json"])
    assert payload["raw"] == {"note": "some raw"}
    assert payload["gate_reasons"]["quote"] == "value_0.8_not_in_quote"

    # 未入 monthly_returns
    n = conn.execute("SELECT COUNT(*) FROM monthly_returns").fetchone()[0]
    assert n == 0


def test_rolling_fail_and_field_type_fail_both_recorded(conn):
    ex = Extraction(
        ym="2025-05", net_return=0.75,  # field_type 挂 (>=0.5)
        source_quote="return 75%",
        measure="net_monthly", measure_label_in_pdf="",
        rolling={"1mo": None, "3mo": None, "6mo": None, "12mo": None},
        not_found=False, raw={},
    )
    q = QuoteCheck(True, "ok")
    r = RollingCheck(False, "mismatch_3mo", 0)
    dec = store.write_extraction(
        conn, fund_id="fund_x", ex=ex,
        quote_check=q, rolling_check=r, identity_check=QuoteCheck(passed=True, reason="ok"),
        monthly_history={},
    )
    assert dec.action == "pending"
    assert dec.gate_summary == "q1r0f0a1i1"
    assert "rolling:mismatch_3mo" in dec.reason
    assert "field:" in dec.reason


def test_antifab_fail_after_history_run(conn):
    """连续 3 个相同值 -> antifab 挡. 第 4 个进 pending."""
    hist = {"2025-01": 0.005, "2025-02": 0.005, "2025-03": 0.005}
    ex = Extraction(
        ym="2025-04", net_return=0.005,
        source_quote="return 0.5%",
        measure="net_monthly", measure_label_in_pdf="",
        rolling={"1mo": None, "3mo": None, "6mo": None, "12mo": None},
        not_found=False, raw={},
    )
    dec = store.write_extraction(
        conn, fund_id="fund_x", ex=ex,
        quote_check=QuoteCheck(True, "ok"),
        rolling_check=RollingCheck(True, "ok", 0),
        identity_check=QuoteCheck(True, "ok"),
        monthly_history=hist,
    )
    assert dec.action == "pending"
    assert "antifab:identical_run" in dec.reason


# ---------- 写库决策: gap ----------

def test_not_found_goes_to_confirmed_gap(conn):
    ex = Extraction(
        ym="2025-06", net_return=None, source_quote="",
        measure="unknown", measure_label_in_pdf="",
        rolling={}, not_found=True, raw={},
    )
    dec = store.write_extraction(
        conn, fund_id="fund_x", ex=ex,
        quote_check=QuoteCheck(False, "empty_quote"),
        rolling_check=RollingCheck(True, "no_net_return", 0),
        identity_check=QuoteCheck(True, "ok"),
        monthly_history={},
    )
    assert dec.action == "gap"
    row = conn.execute(
        "SELECT * FROM confirmed_gaps WHERE fund_id='fund_x'"
    ).fetchone()
    assert row["missing_month"] == "2025-06"
    assert row["exhausted_levels"] == "L1,L2,L3"


def test_retry_success_after_gap_clears_confirmed_gap(conn):
    """先记一次缺口 (下载失败/穷尽), 重跑成功写入 monthly_returns 后,
    该月的 confirmed_gaps 残留必须被清除 -- 否则 gap_count 永久误报该基金缺数据。
    """
    store.record_confirmed_gap(
        conn, fund_id="fund_x", missing_month="2025-06",
        exhausted_levels="download_fail",
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM confirmed_gaps WHERE fund_id='fund_x' AND missing_month='2025-06'"
    ).fetchone()[0] == 1

    ex = Extraction(
        ym="2025-06", net_return=0.0065,
        source_quote="Fund returned 0.65% (net of fees).",
        measure="net_monthly", measure_label_in_pdf="Net Return",
        rolling={"1mo": 0.65, "3mo": None, "6mo": None, "12mo": None},
        not_found=False, raw={"net_return_pct": 0.65},
    )
    dec = store.write_extraction(
        conn, fund_id="fund_x", ex=ex,
        quote_check=QuoteCheck(True, "ok"),
        rolling_check=RollingCheck(True, "ok", 1),
        identity_check=QuoteCheck(True, "ok"),
        monthly_history={},
    )
    assert dec.action == "monthly"
    assert conn.execute(
        "SELECT COUNT(*) FROM confirmed_gaps WHERE fund_id='fund_x' AND missing_month='2025-06'"
    ).fetchone()[0] == 0


def test_parse_error_goes_to_gap(conn):
    ex = Extraction(
        ym="2025-07", net_return=None, source_quote="",
        measure="unknown", measure_label_in_pdf="",
        rolling={}, not_found=True, raw={"error": "parse_failed"},
        parse_error="no_json_object_in_text",
    )
    dec = store.write_extraction(
        conn, fund_id="fund_x", ex=ex,
        quote_check=QuoteCheck(False, "empty_quote"),
        rolling_check=RollingCheck(True, "no_net_return", 0),
        identity_check=QuoteCheck(True, "ok"),
        monthly_history={},
    )
    assert dec.action == "gap"
    assert dec.reason == "no_json_object_in_text"


# ---------- pending 促进 / 拒绝 ----------

def test_promote_pending_writes_monthly(conn):
    # 先造一条 pending
    ex = Extraction(
        ym="2025-08", net_return=0.007,
        source_quote="0.7%",
        measure="net_monthly", measure_label_in_pdf="",
        rolling={"1mo": None, "3mo": None, "6mo": None, "12mo": None},
        not_found=False, raw={},
    )
    dec = store.write_extraction(
        conn, fund_id="fund_x", ex=ex,
        quote_check=QuoteCheck(False, "orphan_numbers"),
        rolling_check=RollingCheck(True, "ok", 0),
        identity_check=QuoteCheck(True, "ok"),
        monthly_history={},
    )
    assert dec.review_id is not None
    result = store.promote_pending(conn, dec.review_id)
    assert result == {"fund_id": "fund_x", "date": "2025-08-31", "action": "approved"}
    row = conn.execute(
        "SELECT * FROM monthly_returns WHERE fund_id='fund_x'"
    ).fetchone()
    assert abs(row["net_return"] - 0.007) < 1e-9

    # pending 状态改
    pending_row = conn.execute(
        "SELECT review_state FROM pending_review WHERE id=?", (dec.review_id,)
    ).fetchone()
    assert pending_row["review_state"] == "approved"


def test_reject_pending_no_monthly_write(conn):
    ex = Extraction(
        ym="2025-09", net_return=0.005, source_quote="",
        measure="unknown", measure_label_in_pdf="",
        rolling={"1mo": None, "3mo": None, "6mo": None, "12mo": None},
        not_found=False, raw={},
    )
    dec = store.write_extraction(
        conn, fund_id="fund_x", ex=ex,
        quote_check=QuoteCheck(False, "empty_quote"),
        rolling_check=RollingCheck(True, "ok", 0),
        identity_check=QuoteCheck(True, "ok"),
        monthly_history={},
    )
    store.reject_pending(conn, dec.review_id, reason="hallucinated")
    row = conn.execute(
        "SELECT review_state, review_reason FROM pending_review WHERE id=?",
        (dec.review_id,),
    ).fetchone()
    assert row["review_state"] == "rejected"
    assert row["review_reason"] == "hallucinated"
    # monthly_returns 不该有
    n = conn.execute("SELECT COUNT(*) FROM monthly_returns").fetchone()[0]
    assert n == 0


def test_promote_nonexistent_raises(conn):
    with pytest.raises(KeyError):
        store.promote_pending(conn, 99999)


# ---------- 读接口 ----------

def test_load_monthly_history(conn):
    # 写两月
    for ret, ym in [(0.01, "2025-01"), (0.02, "2025-02")]:
        ex = Extraction(
            ym=ym, net_return=ret, source_quote=f"return {ret*100}%",
            measure="net_monthly", measure_label_in_pdf="",
            rolling={"1mo": None, "3mo": None, "6mo": None, "12mo": None},
            not_found=False, raw={},
        )
        store.write_extraction(
            conn, fund_id="fund_x", ex=ex,
            quote_check=QuoteCheck(True, "ok"),
            rolling_check=RollingCheck(True, "ok", 0),
            identity_check=QuoteCheck(True, "ok"),
            monthly_history=store.load_monthly_history(conn, "fund_x"),
        )
    hist = store.load_monthly_history(conn, "fund_x")
    assert hist == {"2025-01": 0.01, "2025-02": 0.02}


def test_list_pending_and_gaps(conn):
    # 一条 pending + 一条 gap
    store.write_extraction(
        conn, fund_id="fund_x",
        ex=Extraction(
            ym="2025-10", net_return=0.005, source_quote="",
            measure="unknown", measure_label_in_pdf="",
            rolling={"1mo": None, "3mo": None, "6mo": None, "12mo": None},
            not_found=False, raw={},
        ),
        quote_check=QuoteCheck(False, "empty_quote"),
        rolling_check=RollingCheck(True, "ok", 0),
        identity_check=QuoteCheck(True, "ok"),
        monthly_history={},
    )
    store.write_extraction(
        conn, fund_id="fund_x",
        ex=Extraction(
            ym="2025-11", net_return=None, source_quote="",
            measure="unknown", measure_label_in_pdf="",
            rolling={}, not_found=True, raw={},
        ),
        quote_check=QuoteCheck(False, "empty_quote"),
        rolling_check=RollingCheck(True, "no_net_return", 0),
        identity_check=QuoteCheck(True, "ok"),
        monthly_history={},
    )
    pending = store.list_pending(conn, fund_id="fund_x")
    assert len(pending) == 1
    assert pending[0]["date"] == "2025-10-31"

    gaps = store.list_gaps(conn, "fund_x")
    assert len(gaps) == 1
    assert gaps[0]["missing_month"] == "2025-11"


def test_write_extraction_identity_fail_goes_pending(conn):
    """Spec G 10.5: 身份闸未过的文档不得入 monthly_returns, 转 pending_review。"""
    ex = Extraction(
        ym="2025-03",
        net_return=0.0065,
        source_quote="Fund returned 0.65% (net of fees).",
        measure="net_monthly",
        measure_label_in_pdf="Net Return",
        rolling={"1mo": 0.65, "3mo": None, "6mo": None, "12mo": None},
        not_found=False,
        raw={"net_return_pct": 0.65},
        fund_name_text="Yarra Australian Income Fund",
    )
    q = QuoteCheck(passed=True, reason="ok")
    r = RollingCheck(passed=True, reason="ok", windows_verified=1)
    ident = verify.check_fund_identity(
        ex.fund_name_text, "Yarra Enhanced Income Fund")
    dec = store.write_extraction(
        conn, fund_id="fund_x", ex=ex,
        quote_check=q, rolling_check=r, identity_check=ident,
        monthly_history={},
    )
    assert dec.action == "pending"
    assert "identity" in dec.reason
    assert dec.gate_summary == "q1r1f1a1i0"
    n = conn.execute(
        "SELECT COUNT(*) FROM monthly_returns WHERE fund_id='fund_x'"
    ).fetchone()[0]
    assert n == 0, "身份存疑的数据不得进 monthly_returns"


# ---------- slugify_fund_id / rename_fund_id ----------

def test_slugify_fund_id_basic():
    assert store.slugify_fund_id("Stake Accumulate Fund") == "stake_accumulate_fund"
    assert store.slugify_fund_id("  ***  ") == "fund"


def test_rename_fund_id_noop_when_new_equals_old(conn):
    assert store.rename_fund_id(conn, "fund_x", "fund_x", "Fund X") is False
    row = conn.execute("SELECT * FROM funds WHERE fund_id='fund_x'").fetchone()
    assert row is not None


def test_rename_fund_id_moves_child_rows(conn):
    """monthly_returns/confirmed_gaps/pending_review 都随 rename 转到新 fund_id,
    行数不变; anomalies/fund_metrics 在这套 fixture schema 里不存在 (只由
    webapp SQLAlchemy Base.metadata.create_all 建), rename 应静默跳过不报错。
    """
    conn.execute(
        "INSERT INTO monthly_returns (fund_id, date, net_return, nav) "
        "VALUES ('fund_x', '2026-01-31', 0.005, 1.005)"
    )
    conn.execute(
        "INSERT INTO confirmed_gaps (fund_id, missing_month, exhausted_levels) "
        "VALUES ('fund_x', '2026-02', 'L1,L2,L3')"
    )
    conn.execute(
        "INSERT INTO pending_review (fund_id, date, net_return, extract_method) "
        "VALUES ('fund_x', '2026-03-31', 0.006, 'llm')"
    )
    conn.commit()
    assert not store._table_exists(conn, "anomalies")
    assert not store._table_exists(conn, "fund_metrics")

    ok = store.rename_fund_id(conn, "fund_x", "stake_accumulate_fund", "Stake Accumulate Fund")
    assert ok is True

    assert conn.execute("SELECT 1 FROM funds WHERE fund_id='fund_x'").fetchone() is None
    new = conn.execute("SELECT * FROM funds WHERE fund_id='stake_accumulate_fund'").fetchone()
    assert new is not None
    assert new["fund_name"] == "Stake Accumulate Fund"
    assert new["confirmed_url"] == "https://example.com/archive"  # 其余列原样复制

    mr = conn.execute("SELECT fund_id FROM monthly_returns").fetchall()
    assert [r["fund_id"] for r in mr] == ["stake_accumulate_fund"]
    gaps = conn.execute("SELECT fund_id FROM confirmed_gaps").fetchall()
    assert [r["fund_id"] for r in gaps] == ["stake_accumulate_fund"]
    pend = conn.execute("SELECT fund_id FROM pending_review").fetchall()
    assert [r["fund_id"] for r in pend] == ["stake_accumulate_fund"]


def test_rename_fund_id_skips_on_id_collision(conn):
    store.upsert_fund(conn, fund_id="fund_y", fund_name="Fund Y", confirmed_url="https://b.com")
    ok = store.rename_fund_id(conn, "fund_x", "fund_y", "Whatever Name")
    assert ok is False
    assert conn.execute("SELECT 1 FROM funds WHERE fund_id='fund_x'").fetchone()
    row_y = conn.execute("SELECT fund_name FROM funds WHERE fund_id='fund_y'").fetchone()
    assert row_y["fund_name"] == "Fund Y"  # 未被覆盖


def test_rename_fund_id_skips_on_name_collision(conn):
    """funds.fund_name 有 UNIQUE 约束: 新名字被别的 fund_id 占用也要跳过."""
    store.upsert_fund(conn, fund_id="fund_y", fund_name="Fund Y", confirmed_url="https://b.com")
    ok = store.rename_fund_id(conn, "fund_x", "fund_x_renamed", "Fund Y")
    assert ok is False
    assert conn.execute("SELECT 1 FROM funds WHERE fund_id='fund_x'").fetchone()
    assert conn.execute("SELECT 1 FROM funds WHERE fund_id='fund_x_renamed'").fetchone() is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
