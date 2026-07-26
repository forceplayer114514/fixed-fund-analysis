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
    # discovered_source_name 落库 (查 fund_id 不写死 test_fund -- page_fund_name
    # 与输入名不同会触发自动纠名 rename, fund_id 换了; 见
    # test_l1_rename_on_page_fund_name 专测 rename 本身, 这里只关心
    # discovered_source_name 有没有记下来, 与 fund_id 是否变化无关)
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT discovered_source_name FROM funds").fetchone()
    conn.close()
    assert row[0] == "Test Fund From Page"


def test_l1_fail_falls_back_to_pdf(tmp_db):
    """L1 status!=ok -> 走 L2 PDF 通路 (既有 discovery).

    Spec G: discover.py 的 find_archive_via_search 已删 sub2api web_search
    兜底, 只走 Tavily。若不 mock discover2.find_archive_v2, Tavily 账号配额
    用尽 (HTTP 432, 2026-07-26 起持续) 时 archive_url/latest_pdf_url 全空,
    _fetch 压根不会被调, 这条测试会变成依赖 Tavily 账号活期状态的假单测。
    直接 mock v2 定位结果, 保证测试与外部网络/账号状态解耦。
    """
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm
    from llm_ingest import discover2 as d2_mod
    from llm_ingest.discover import ArchivePointer

    fake_pointer = ArchivePointer(
        archive_url="https://issuer.example.com/reports/archive",
        pagination_param=None,
        no_archive=False,
        latest_pdf_url=None,
        issuer_domain_confirmed="issuer.example.com",
        evidence="stub for test_l1_fail_falls_back_to_pdf",
    )
    with patch.object(fm, "probe", return_value={
        "status": "fetch_fail", "records": [], "ytd_map": {},
        "url": None, "page_fund_name": None, "errors": [],
    }), patch.object(d2_mod, "find_archive_v2", return_value=fake_pointer), \
         patch("llm_ingest.discover._fetch", return_value=None) as mock_fetch:
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


def test_l1_rename_on_page_fund_name(tmp_db):
    """L1 page_fund_name 与用户输入不同 -> 触发自动纠名: funds 表 fund_id/
    fund_name 换成基于官方名的新值, job dict 的 fund_id 同步更新 (否则表格级
    /jobs/active 轮询按 fund_id 关联不到这一行)."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm

    fake_records = [("2024-01-31", 0.005)]
    with patch.object(fm, "probe", return_value={
        "status": "ok", "records": fake_records, "ytd_map": {},
        "url": "https://fundmonitors.com/x",
        "page_fund_name": "Test Fund Official",
        "errors": [],
    }), patch("llm_ingest.discover._fetch", return_value=None):
        jid = ing._job_new("test_fund")
        ing._run_ingest_job(jid, _make_req())

    assert ing._JOBS[jid]["fund_id"] == "test_fund_official"

    conn = sqlite3.connect(tmp_db)
    old_row = conn.execute("SELECT 1 FROM funds WHERE fund_id='test_fund'").fetchone()
    new_row = conn.execute(
        "SELECT fund_name, discovered_source_name FROM funds WHERE fund_id='test_fund_official'"
    ).fetchone()
    mr = conn.execute("SELECT fund_id FROM monthly_returns").fetchall()
    conn.close()
    assert old_row is None
    assert new_row is not None
    assert new_row[0] == "Test Fund Official"
    assert new_row[1] == "Test Fund Official"
    assert [r[0] for r in mr] == ["test_fund_official"]


def test_l1_rename_skipped_on_collision_continues(tmp_db):
    """新 fund_id 已被别的基金占用 -> rename 放弃, 继续用旧 fund_id 跑完, 不崩."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm
    from llm_ingest import store as store_mod

    conn = sqlite3.connect(tmp_db)
    store_mod.upsert_fund(
        conn, fund_id="test_fund_official", fund_name="Other Fund",
        confirmed_url="https://other.com",
    )
    conn.close()

    fake_records = [("2024-01-31", 0.005)]
    with patch.object(fm, "probe", return_value={
        "status": "ok", "records": fake_records, "ytd_map": {},
        "url": "https://fundmonitors.com/x",
        "page_fund_name": "Test Fund Official",
        "errors": [],
    }), patch("llm_ingest.discover._fetch", return_value=None):
        jid = ing._job_new("test_fund")
        ing._run_ingest_job(jid, _make_req())

    assert ing._JOBS[jid]["fund_id"] == "test_fund"
    assert ing._JOBS[jid]["state"] == "succeeded"
    log = "\n".join(ing._JOBS[jid]["log_tail"])
    assert "rename_skipped" in log
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT 1 FROM funds WHERE fund_id='test_fund'").fetchone()
    conn.close()
    assert row is not None


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


def test_l2_discovery_gaps_recorded_as_confirmed_gaps(tmp_db):
    """回归 (2026-07 Stake 2025-09 静默漏记事故): run_discovery 算出的 rep.gaps
    (预期月份里压根没找到 PDF 链接的月份, 如官方本月未发月报) 此前从未被写进
    confirmed_gaps -- per-link 循环里的 record_confirmed_gap 只覆盖"有链接但
    下载/提取失败"的月份, 两类缺口互斥, rep.gaps 那批一直静默丢失。"""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import discover as disc_mod
    from llm_ingest import extract as ex_mod
    from llm_ingest import fundmonitors as fm
    from llm_ingest import cli as llm_cli
    from llm_ingest import pdf as pdf_mod

    fake_report = disc_mod.DiscoveryReport(
        fund_id="test_fund",
        links=[("2025-10", "https://issuer.example.com/2025-10.pdf")],
        gaps=["2025-09"],  # 官方本月未发月报, discovery 阶段就确定无链接
    )
    fake_ex = ex_mod.Extraction(
        ym="2025-10", net_return=0.005, source_quote="Net Return 0.5%",
        measure="table_value", measure_label_in_pdf="Net Return (%)",
        rolling={}, not_found=False, raw={},
    )
    with patch.object(fm, "probe", return_value={
        "status": "no_fundid", "records": [], "ytd_map": {},
        "url": None, "page_fund_name": None, "errors": [],
    }), patch.object(disc_mod, "run_discovery", return_value=fake_report), \
         patch.object(llm_cli, "_download_pdf", return_value=True), \
         patch.object(ex_mod, "extract_from_pdf", return_value=fake_ex), \
         patch.object(pdf_mod, "full_text", return_value="Net Return 0.5%"):
        jid = ing._job_new("test_fund")
        ing._run_ingest_job(jid, _make_req())

    assert ing._JOBS[jid]["state"] == "succeeded"
    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT exhausted_levels FROM confirmed_gaps "
        "WHERE fund_id='test_fund' AND missing_month='2025-09'"
    ).fetchone()
    conn.close()
    assert row is not None, "rep.gaps 里的 2025-09 应被记入 confirmed_gaps"
    assert row[0] == "no_link_found"


def test_l2_discovery_no_links_at_all_does_not_record_gaps(tmp_db):
    """discovery 完全空手 (links 也是空) -> 整个 job 该失败, 不该顺带乱记 gaps
    (job 都没跑起来, 没资格断言"这几个月确实没有链接")."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import discover as disc_mod
    from llm_ingest import fundmonitors as fm

    fake_report = disc_mod.DiscoveryReport(
        fund_id="test_fund", links=[], gaps=["2025-09", "2025-10"],
    )
    with patch.object(fm, "probe", return_value={
        "status": "no_fundid", "records": [], "ytd_map": {},
        "url": None, "page_fund_name": None, "errors": [],
    }), patch.object(disc_mod, "run_discovery", return_value=fake_report):
        jid = ing._job_new("test_fund")
        ing._run_ingest_job(jid, _make_req())

    assert ing._JOBS[jid]["state"] == "failed"
    conn = sqlite3.connect(tmp_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM confirmed_gaps WHERE fund_id='test_fund'"
    ).fetchone()[0]
    conn.close()
    assert count == 0
