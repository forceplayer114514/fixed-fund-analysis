"""L1/L2 优先级反转 (Spec B): fundmonitors 先跑, 覆盖成功即跳 PDF 循环."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def tmp_db(monkeypatch):
    """临时 DB, 走既有 schema + 加 discovered_source_name."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    monkeypatch.setenv("FUND_DB_PATH", tmp.name)
    conn = sqlite3.connect(tmp.name)
    from llm_ingest import store
    store.ensure_tables_if_missing(conn)
    conn.execute("ALTER TABLE funds ADD COLUMN discovered_source_name TEXT")
    conn.execute("ALTER TABLE funds ADD COLUMN fundmonitors_fund_id INTEGER")
    conn.execute("ALTER TABLE funds ADD COLUMN fundmonitors_acc_code TEXT")
    conn.execute(
        "INSERT INTO funds (fund_id, fund_name, confirmed_url, fetch_method, "
        "url_type, fundmonitors_fund_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("test_fund", "Test Fund", "https://a.com", "code", "archive", 1234),
    )
    conn.commit()
    conn.close()
    yield tmp.name
    Path(tmp.name).unlink(missing_ok=True)


def _make_req():
    from webapp.backend.app.schemas import IngestRequest
    return IngestRequest(
        fund_id="test_fund", fund_name="Test Fund",
        issuer=None, confirmed_url=None, issuer_domain=None,
        asx_code=None, apir_code=None, max_pdf_pages=None, limit=None,
    )


def test_l1_ok_skips_pdf_and_records_discovered_source_name(tmp_db):
    """L1 fundmonitors 成功 -> PDF 循环整段跳过, discovered_source_name 落库."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm

    fake_records = [("2024-01-31", 0.005), ("2024-02-29", 0.006)]
    with patch.object(fm, "probe", return_value={
        "status": "ok",
        "records": fake_records,
        "ytd_map": {},
        "url": "https://fundmonitors.com/x",
        "page_fund_name": "Test Fund From Page",
        "errors": [],
    }), patch("llm_ingest.discover._fetch", return_value=None) as mock_fetch:
        jid = ing._job_new("test_fund")
        ing._run_ingest_job(jid, _make_req())

    # PDF discovery 不该被调 (L1 已覆盖)
    assert mock_fetch.call_count == 0
    # discovered_source_name 落库
    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT discovered_source_name FROM funds WHERE fund_id='test_fund'"
    ).fetchone()
    conn.close()
    assert row[0] == "Test Fund From Page"


def test_l1_fail_falls_back_to_pdf(tmp_db):
    """L1 status!=ok -> 走 L2 PDF 通路 (既有 discovery)."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm

    with patch.object(fm, "probe", return_value={
        "status": "fetch_fail", "records": [], "ytd_map": {},
        "url": None, "page_fund_name": None, "errors": [],
    }), patch("llm_ingest.discover._fetch", return_value=None) as mock_fetch:
        # discovery 会尝试跑 (返 None 让 discover fallback 到 raise)
        jid = ing._job_new("test_fund")
        try:
            ing._run_ingest_job(jid, _make_req())
        except Exception:
            pass  # discovery 失败可容忍, 本测试关心 fetch 被调
    assert mock_fetch.call_count >= 1  # L2 PDF 通路启动


def test_l1_paywall_no_discovered_source_name(tmp_db):
    """L1 paywall -> discovered_source_name 保持 NULL."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm

    with patch.object(fm, "probe", return_value={
        "status": "paywall", "records": [], "ytd_map": {},
        "url": "https://fundmonitors.com/x",
        "page_fund_name": None, "errors": [],
    }), patch("llm_ingest.discover._fetch", return_value=None):
        jid = ing._job_new("test_fund")
        try:
            ing._run_ingest_job(jid, _make_req())
        except Exception:
            pass
    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT discovered_source_name FROM funds WHERE fund_id='test_fund'"
    ).fetchone()
    conn.close()
    assert row[0] is None


def test_l1_exception_records_status(tmp_db):
    """probe 抛异常 -> 状态 exception:ExceptionType, 走 L2, 不崩 job."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm

    with patch.object(fm, "probe", side_effect=RuntimeError("boom")), \
         patch("llm_ingest.discover._fetch", return_value=None):
        jid = ing._job_new("test_fund")
        try:
            ing._run_ingest_job(jid, _make_req())
        except Exception:
            pass
    log = "\n".join(ing._JOBS[jid]["log_tail"])
    assert "exception" in log.lower() or "boom" in log.lower()


def test_no_l1_threshold_gate():
    """Spec B: 不再有 len(links)<24 门槛, L1 无条件先跑."""
    import inspect
    from webapp.backend.app.routers import ingest as ing
    src = inspect.getsource(ing._run_ingest_job)
    # 老 gate 已删
    assert "len(links) < 24" not in src


def test_l1_ok_stats_monthly_count(tmp_db):
    """L1 覆盖 N 月 -> stats["monthly"] == N."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm

    fake_records = [
        ("2024-01-31", 0.005), ("2024-02-29", 0.006),
        ("2024-03-31", 0.007),
    ]
    with patch.object(fm, "probe", return_value={
        "status": "ok", "records": fake_records, "ytd_map": {},
        "url": "https://fundmonitors.com/x",
        "page_fund_name": "Test Fund", "errors": [],
    }), patch("llm_ingest.discover._fetch", return_value=None):
        jid = ing._job_new("test_fund")
        ing._run_ingest_job(jid, _make_req())
    assert ing._JOBS[jid]["stats"]["monthly"] == 3
