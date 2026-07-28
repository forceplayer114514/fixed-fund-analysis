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


def _ingest_one_month_ok():
    """让摄取循环真入库 1 个月的最小打桩集合.

    "记住归档页" 与 "记录 discovery 缺口" 现在都以 stats['monthly'] > 0 为前提
    (2026-07-29 事故: 那轮 0 入库却记住了坏归档页并写了 16 个假缺口), 所以这些
    用例必须真走通一次入库, 光有链接不算。
    """
    from llm_ingest import extract as ex_mod
    from llm_ingest import pdf as pdf_mod
    from llm_ingest import store as store_mod

    def _fake_extract(pdf_path, ym, **kw):
        return ex_mod.Extraction(
            ym=ym, net_return=0.0065, source_quote="returned 0.65%",
            measure="table_value", measure_label_in_pdf="1 Month",
            fund_name_text=None)

    def _fake_write(conn, *, fund_id, ex, **kw):
        return store_mod.WriteDecision(ym=ex.ym, action="monthly",
                                       gate_summary="stub", reason="ok")

    return (
        patch("llm_ingest.cli._download_pdf", return_value=True),
        patch.object(ex_mod, "extract_from_pdf", _fake_extract),
        patch.object(pdf_mod, "full_text", return_value="returned 0.65%"),
        patch.object(store_mod, "write_extraction", _fake_write),
    )


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
             _ingest_one_month_ok()[0], _ingest_one_month_ok()[1], \
             _ingest_one_month_ok()[2], _ingest_one_month_ok()[3]:
            jid = ing._job_new("f1")
            ing._run_ingest_job(jid, _req(confirmed_url=stale))

        assert mock_disc.call_count == 1, "失效归档页应回退重新搜索"
        # 新找到的归档页顶替失效的那个存回去
        assert _cu(tmp_db) == found

    def test_nothing_ingested_neither_remembers_url_nor_records_gaps(self, tmp_db):
        """2026-07-29 事故: discovery 只判出 1 条 (误判的营销页) 链接, 该链接是网页,
        下载必然失败, 这一轮入库 0 个月 —— 但坏归档页已被记住 (下次更新直奔坏页,
        永久卡住), 且 16 个月被写成 no_link_found 缺口。两者都要求"真入库了数据"。"""
        from webapp.backend.app.routers import ingest as ing
        from llm_ingest import discover as disc
        from llm_ingest import fundmonitors as fm

        bad_page = "https://hellostake.com/au/support/stake-accumulate/x/468061528"
        rep = disc.DiscoveryReport(
            fund_id="f1",
            links=[("2025-01", "https://hellostake.com/au/ambition-report-2025")],
            gaps=[f"2025-{m:02d}" for m in range(2, 13)] + ["2026-01"],
            archive_pointer=disc.ArchivePointer(
                archive_url=bad_page, pagination_param=None, no_archive=True,
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
            log = "\n".join(ing._JOBS[jid]["log_tail"])

        assert _cu(tmp_db) == "", f"坏归档页不该被记住, 却记了 {_cu(tmp_db)!r}"
        conn = sqlite3.connect(tmp_db)
        rows = conn.execute("SELECT missing_month, exhausted_levels FROM "
                            "confirmed_gaps WHERE fund_id=?", ("f1",)).fetchall()
        conn.close()
        # discovery 那 12 个"压根没找到链接"的月份不该被断言
        assert [r for r in rows if r[1] == "no_link_found"] == [], (
            f"这一轮什么都没入库, 不该断言这些月份不存在: {rows}")
        # 唯一允许留下的是"有链接但下载失败"这条 -- 那是真实发生过的尝试, 且
        # exhausted_levels 明确写着 download_fail, 与"发行商没发布"区分得开
        assert rows == [("2025-01", "download_fail")], rows
        assert "不记入 confirmed_gaps" in log, "跳过缺口记录要在日志里说明"

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
             _ingest_one_month_ok()[0], _ingest_one_month_ok()[1], \
             _ingest_one_month_ok()[2], _ingest_one_month_ok()[3]:
            jid = ing._job_new("f1")
            ing._run_ingest_job(jid, _req())

        assert _cu(tmp_db) == found
