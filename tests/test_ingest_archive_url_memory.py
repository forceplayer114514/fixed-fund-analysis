"""更新基金通路: 归档页地址存回 + 失效自愈.

背景: 前端"更新数据"按钮打的是同一个 POST /api/ingest/funds, 只多带一个
funds.confirmed_url。改造前该字段被 `req.confirmed_url or req.issuer_domain or ""`
填成域名根或空串, 于是"更新数据"要么去解析发行商首页 (首页没有月报 PDF, 0 links
硬失败), 要么每次都从零重跑一轮搜索引擎 (费钱, 且下次搜索引擎未必给到同一页面,
缺口记录会来回变)。
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_db(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    monkeypatch.setenv("FUND_DB_PATH", tmp.name)
    conn = sqlite3.connect(tmp.name)
    from llm_ingest import store
    store.ensure_tables_if_missing(conn)
    conn.execute("ALTER TABLE funds ADD COLUMN discovered_source_name TEXT")
    conn.commit()
    conn.close()
    yield tmp.name
    Path(tmp.name).unlink(missing_ok=True)


def _req(**over):
    from webapp.backend.app.schemas import IngestRequest
    base = dict(fund_id="f1", fund_name="Stake Accumulate Fund", issuer=None,
                confirmed_url=None, issuer_domain=None, asx_code=None,
                apir_code=None, max_pdf_pages=None, limit=None)
    base.update(over)
    return IngestRequest(**base)


def _cu(db_path, fund_id="f1"):
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT confirmed_url FROM funds WHERE fund_id=?",
                       (fund_id,)).fetchone()
    conn.close()
    return row[0] if row else None


class TestUpsertPreservesConfirmedUrl:
    def test_issuer_domain_no_longer_masquerades_as_archive_url(self, tmp_db):
        """域名根塞进 confirmed_url 会让"更新数据"去解析发行商首页 -> 0 links."""
        from webapp.backend.app.routers import ingest as ing
        from llm_ingest import store
        conn = sqlite3.connect(tmp_db)
        ing._upsert_fund_preserving_existing(
            conn, store, "f1", "Stake Accumulate Fund",
            _req(issuer_domain="https://hellostake.com"))
        conn.commit(); conn.close()
        assert _cu(tmp_db) == ""

    def test_stored_archive_url_survives_a_request_that_omits_it(self, tmp_db):
        """upsert_fund 的 ON CONFLICT 是无条件 `confirmed_url = excluded.*`,
        不先读回来就会被这次请求空着的同名字段清掉, 存回也就白存了。"""
        from webapp.backend.app.routers import ingest as ing
        from llm_ingest import store
        arch = "https://hellostake.com/au/legal/monthly-performance-report"
        conn = sqlite3.connect(tmp_db)
        ing._upsert_fund_preserving_existing(conn, store, "f1", "F", _req())
        conn.execute("UPDATE funds SET confirmed_url=? WHERE fund_id=?", (arch, "f1"))
        conn.commit()
        ing._upsert_fund_preserving_existing(conn, store, "f1", "F", _req())
        conn.commit(); conn.close()
        assert _cu(tmp_db) == arch

    def test_explicit_request_url_wins(self, tmp_db):
        from webapp.backend.app.routers import ingest as ing
        from llm_ingest import store
        conn = sqlite3.connect(tmp_db)
        ing._upsert_fund_preserving_existing(conn, store, "f1", "F", _req())
        conn.execute("UPDATE funds SET confirmed_url='https://old/page' "
                     "WHERE fund_id=?", ("f1",))
        conn.commit()
        ing._upsert_fund_preserving_existing(
            conn, store, "f1", "F", _req(confirmed_url="https://new/page"))
        conn.commit(); conn.close()
        assert _cu(tmp_db) == "https://new/page"


class TestRememberArchiveUrl:
    def test_writes_when_empty(self, tmp_db):
        from webapp.backend.app.routers import ingest as ing
        from llm_ingest import store
        conn = sqlite3.connect(tmp_db)
        ing._upsert_fund_preserving_existing(conn, store, "f1", "F", _req())
        conn.commit()
        assert ing._remember_archive_url(conn, "f1", "https://x/archive") is True
        conn.close()
        assert _cu(tmp_db) == "https://x/archive"

    def test_does_not_overwrite_user_supplied_url(self, tmp_db):
        from webapp.backend.app.routers import ingest as ing
        from llm_ingest import store
        conn = sqlite3.connect(tmp_db)
        ing._upsert_fund_preserving_existing(
            conn, store, "f1", "F", _req(confirmed_url="https://user/page"))
        conn.commit()
        assert ing._remember_archive_url(conn, "f1", "https://other/page") is False
        conn.close()
        assert _cu(tmp_db) == "https://user/page"

    def test_none_archive_url_is_noop(self, tmp_db):
        from webapp.backend.app.routers import ingest as ing
        from llm_ingest import store
        conn = sqlite3.connect(tmp_db)
        ing._upsert_fund_preserving_existing(conn, store, "f1", "F", _req())
        conn.commit()
        assert ing._remember_archive_url(conn, "f1", None) is False
        conn.close()


class TestUpdateFundPathUsesTheNewClassifier:
    """更新通路必须与新增通路走同一处判定 (parse_archive_page ->
    extract_all_pdf_links + classify_pdf_links)。"""

    def test_parse_archive_gets_base_url_and_fund_name(self, tmp_db):
        """漏传 base_url 时页内相对 href 还原不出绝对地址, 链接清单必然为空;
        漏传 fund_name 则无从判断归属 (classify_pdf_links 直接 ValueError)。"""
        from webapp.backend.app.routers import ingest as ing
        from llm_ingest import discover as disc
        from llm_ingest import fundmonitors as fm

        arch = "https://hellostake.com/au/legal/monthly-performance-report"
        seen = {}

        def _fake_parse(html, *, fund_name, client=None, base_url=""):
            seen["fund_name"] = fund_name
            seen["base_url"] = base_url
            return ([("2026-06", "https://cdn/AccumulateReport_Jun26.pdf")],
                    False, "", 0)

        with patch.object(fm, "probe", return_value={"status": "no_fundid",
                                                     "records": [], "url": None,
                                                     "page_fund_name": None,
                                                     "errors": []}), \
             patch.object(disc, "_fetch", return_value="<html/>"), \
             patch.object(disc, "parse_archive_page", _fake_parse), \
             patch("llm_ingest.cli._download_pdf", return_value=False):
            jid = ing._job_new("f1")
            ing._run_ingest_job(jid, _req(confirmed_url=arch))

        assert seen["base_url"] == arch
        assert seen["fund_name"] == "Stake Accumulate Fund"

    def test_stale_archive_url_self_heals_into_rediscovery(self, tmp_db):
        """存的归档页失效 (发行商改版) -> 清掉该值并本轮重新搜索, 而不是从此
        每次更新都硬失败在 "discovery 未产出任何 PDF 链接"。"""
        from webapp.backend.app.routers import ingest as ing
        from llm_ingest import discover as disc
        from llm_ingest import fundmonitors as fm

        stale = "https://hellostake.com/au/old-archive-page"
        found = "https://hellostake.com/au/legal/monthly-performance-report"
        rep = disc.DiscoveryReport(
            fund_id="f1",
            links=[("2026-06", "https://cdn/AccumulateReport_Jun26.pdf")],
            archive_pointer=disc.ArchivePointer(
                archive_url=found, pagination_param=None, no_archive=False,
                latest_pdf_url=None, issuer_domain_confirmed=None, evidence=""),
        )

        with patch.object(fm, "probe", return_value={"status": "no_fundid",
                                                     "records": [], "url": None,
                                                     "page_fund_name": None,
                                                     "errors": []}), \
             patch.object(disc, "_fetch", return_value="<html/>"), \
             patch.object(disc, "parse_archive_page",
                          return_value=([], False, "", 0)), \
             patch.object(disc, "run_discovery", return_value=rep) as mock_disc, \
             patch("llm_ingest.cli._download_pdf", return_value=False):
            jid = ing._job_new("f1")
            ing._run_ingest_job(jid, _req(confirmed_url=stale))

        assert mock_disc.call_count == 1, "失效归档页应回退重新搜索"
        # 新找到的归档页顶替失效的那个存回去
        assert _cu(tmp_db) == found

    def test_discovered_archive_url_is_remembered(self, tmp_db):
        """新增基金那轮就该把归档页存下来, 下次更新才不用再搜一遍。"""
        from webapp.backend.app.routers import ingest as ing
        from llm_ingest import discover as disc
        from llm_ingest import fundmonitors as fm

        found = "https://gcapinvest.com/our-lit"
        rep = disc.DiscoveryReport(
            fund_id="f1", links=[("2026-06", "https://cdn/gci-jun-2026.pdf")],
            archive_pointer=disc.ArchivePointer(
                archive_url=found, pagination_param=None, no_archive=False,
                latest_pdf_url=None, issuer_domain_confirmed=None, evidence=""),
        )
        with patch.object(fm, "probe", return_value={"status": "no_fundid",
                                                     "records": [], "url": None,
                                                     "page_fund_name": None,
                                                     "errors": []}), \
             patch.object(disc, "run_discovery", return_value=rep), \
             patch("llm_ingest.cli._download_pdf", return_value=False):
            jid = ing._job_new("f1")
            ing._run_ingest_job(jid, _req())

        assert _cu(tmp_db) == found
