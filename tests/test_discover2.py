"""discover2.find_archive_v2 单测.

覆盖 2026-07 Stake 事故回归: 归档页里 PDS/TMD 与真月报混在一起时, 整页月报必须
全部被带回 (旧实现靠文件名 token 打分挑一份去打样, 挑到 PDS 就整页误杀)。判定
现在统一走 discover.classify_pdf_links, 本文件用 conftest.SelectStubClient 当假
模型, 真跑那段解析/校验代码。
"""
import sys
from pathlib import Path

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from conftest import SelectStubClient
from llm_ingest import discover as disc_mod
from llm_ingest import discover2 as d2


def _stub_locate(monkeypatch, page_url):
    """把搜索+排序打桩成只返回 page_url 一个候选."""
    monkeypatch.setattr(d2, "multi_query_search", lambda *a, **k: [page_url])
    monkeypatch.setattr(d2, "_pick_issuer_domain", lambda *a, **k: None)
    monkeypatch.setattr(
        d2, "rank_urls",
        lambda urls, *a, **k: [{"url": u, "score": 90, "reason": "t"} for u in urls],
    )


class TestArchivePageDecidedByLinkListing:
    def test_page_with_pds_and_monthlies_returns_every_monthly(self, monkeypatch):
        """核心回归: 页内 2 份法律文件 + 4 份真月报 -> discovered_links 必须是 4 份,
        不是 1 份。旧实现 (token 打分 + 只留并列最高分 + 黑名单) 在这组真实文件名
        上只剩 1 个月入库。"""
        page = "https://hellostake.com/legal/monthly-performance-report"
        pds = "https://cdn/Stake_Accumulate_Fund_PDS_25May26.pdf"
        tmd = "https://cdn/Stake_Accumulate_TMD_25May26.pdf"
        monthlies = {
            "https://cdn/Accumulate report_March2025.pdf": "2025-03",
            "https://cdn/AccumulateMonthly_April25.pdf": "2025-04",
            "https://cdn/AccumulateReport_Sept_2025.pdf": "2025-09",
            "https://cdn/AccumulateReport_Jun26.pdf": "2026-06",
        }
        _stub_locate(monkeypatch, page)
        monkeypatch.setattr(d2, "probe_urls", lambda urls, **k: [{
            "url": page, "html_ok": True,
            "pdf_urls": [pds, tmd] + list(monthlies), "error": None,
        }])

        ptr = d2.find_archive_v2(
            "Stake Accumulate Fund", "Stake",
            client=SelectStubClient(lambda u: monthlies.get(u)))

        assert ptr.no_archive is False
        assert ptr.archive_url == page
        assert ptr.discovered_links == sorted((ym, u) for u, ym in monthlies.items())
        # latest_pdf_url 取月份最大的那份, 供上游单份兜底用
        assert ptr.latest_pdf_url == "https://cdn/AccumulateReport_Jun26.pdf"
        assert pds not in [u for _ym, u in ptr.discovered_links]

    def test_sibling_fund_pdfs_not_in_discovered_links(self, monkeypatch):
        """Spec G 10.3: yarracm.com/performance 同挂 Enhanced Income 与 Australian
        Income 两支基金月报, 不得把兄弟基金的带回。"""
        page = "https://yarracm.com/performance"
        target = "https://yarracm.com/docs/yarra-enhanced-income-jun-2026.pdf"
        sibling = "https://yarracm.com/docs/yarra-australian-income-jun-2026.pdf"
        _stub_locate(monkeypatch, page)
        monkeypatch.setattr(d2, "probe_urls", lambda urls, **k: [{
            "url": page, "html_ok": True, "pdf_urls": [target, sibling],
            "error": None,
        }])

        ptr = d2.find_archive_v2(
            "Yarra Enhanced Income Fund", "Yarra Capital Management",
            client=SelectStubClient(
                lambda u: "2026-06" if u == target else None))

        urls = [u for _ym, u in ptr.discovered_links]
        assert urls == [target]
        assert sibling not in urls

    def test_single_month_page_marked_no_archive_for_wayback_backfill(self, monkeypatch):
        page = "https://gcapinvest.com/our-lit"
        only = "https://gcapinvest.com/gci-inv-update-jun-2026.pdf"
        pds = "https://gcapinvest.com/current-product-disclosure-statement-gryphon.pdf"
        _stub_locate(monkeypatch, page)
        monkeypatch.setattr(d2, "probe_urls", lambda urls, **k: [{
            "url": page, "html_ok": True, "pdf_urls": [pds, only], "error": None,
        }])

        ptr = d2.find_archive_v2(
            "Gryphon Capital Income Trust", "Gryphon Capital",
            client=SelectStubClient(lambda u: "2026-06" if u == only else None))

        # 只判出 1 个月 -> 当"单份最新", 让上游继续走 wayback 补历史
        assert ptr.no_archive is True
        assert ptr.discovered_links == [("2026-06", only)]

    def test_page_with_zero_monthlies_falls_through_to_navigate(self, monkeypatch):
        """整页判不出本基金月报 (Yarra /performance 全是别的基金) -> 不能就此判
        no_archive, 要落到步 6.5 导航兜底。这也是 2026-07 Stake 事故里"页面找对了
        却被放弃"那一幕的回归。"""
        page = "https://yarracm.com/performance"
        landing = "https://yarracm.com/monthly-reports"
        target = "https://yarracm.com/docs/yarra-enhanced-income-jun-2026.pdf"
        sibling = "https://yarracm.com/docs/yarra-australian-income-jun-2026.pdf"
        _stub_locate(monkeypatch, page)
        monkeypatch.setattr(d2, "probe_urls", lambda urls, **k: [{
            "url": page, "html_ok": True, "pdf_urls": [sibling], "error": None,
            "html_snippet": "<html>...</html>",
        }])
        monkeypatch.setattr(d2, "_fetch", lambda *a, **k: "<html>full</html>")
        from llm_ingest import navigate as nav_mod
        monkeypatch.setattr(nav_mod, "navigate_one_hop",
                            lambda *a, **k: (landing, "<html/>", [target, sibling]))

        ptr = d2.find_archive_v2(
            "Yarra Enhanced Income Fund", "Yarra",
            client=SelectStubClient(
                lambda u: "2026-06" if u == target else None))

        assert ptr.archive_url == landing
        # 导航兜底同样只带回本基金的 (Spec G 10.3 补漏)
        assert [u for _ym, u in ptr.discovered_links] == [target]

    def test_classify_failure_on_one_page_moves_to_next_candidate(self, monkeypatch):
        """单页判定失败 (模型不可达) 不该拖垮整轮 -- 还有后续候选页。"""
        bad, good = "https://x.com/a", "https://x.com/b"
        pdf = "https://x.com/monthly-jun-2026.pdf"
        monkeypatch.setattr(d2, "multi_query_search", lambda *a, **k: [bad, good])
        monkeypatch.setattr(d2, "_pick_issuer_domain", lambda *a, **k: None)
        monkeypatch.setattr(
            d2, "rank_urls",
            lambda urls, *a, **k: [{"url": u, "score": 9, "reason": ""} for u in urls])
        monkeypatch.setattr(d2, "probe_urls", lambda urls, **k: [
            {"url": bad, "html_ok": True, "pdf_urls": ["https://x.com/z.pdf"],
             "error": None},
            {"url": good, "html_ok": True, "pdf_urls": [pdf], "error": None},
        ])

        calls = {"n": 0}

        def _classify(urls, fund_name, *, client=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise disc_mod.ClassifyError("upstream 503")
            return ([("2026-06", pdf)], [], 0)

        monkeypatch.setattr(disc_mod, "classify_pdf_links", _classify)

        ptr = d2.find_archive_v2("F", "I", client=object())
        assert ptr.archive_url == good
        assert ptr.discovered_links == [("2026-06", pdf)]


class TestLocateCandidates:
    """Spec G 4.1: 按引擎分派"定位候选页面", 下游抓页/抽 PDF/打样两引擎共用。"""

    def test_tavily_engine_uses_search_and_rank(self, monkeypatch):
        from llm_ingest import discover2 as d2
        calls = {"search": 0, "rank": 0, "grok": 0}

        def _search(*a, **k):
            calls["search"] += 1
            return ["https://issuer.com/reports"]

        def _rank(urls, *a, **k):
            calls["rank"] += 1
            return [{"url": urls[0], "score": 90, "reason": "r"}]

        monkeypatch.setattr(d2, "multi_query_search", _search)
        monkeypatch.setattr(d2, "rank_urls", _rank)
        domain, ranked, ev = d2.locate_candidates(
            "Some Fund", "Some Issuer", engine="tavily", client=object())
        assert calls["search"] == 1 and calls["rank"] == 1
        assert ranked[0]["url"] == "https://issuer.com/reports"
        assert ev["engine_used"] == "tavily"

    def test_grok_engine_skips_gemini_rank(self, monkeypatch):
        """本设计的核心收益: Grok 已排好序, 不再调 Gemini rank_urls。"""
        from llm_ingest import discover2 as d2
        from llm_ingest import grok

        called = {"rank": 0}
        monkeypatch.setattr(
            d2, "rank_urls",
            lambda *a, **k: called.__setitem__("rank", called["rank"] + 1) or [])
        monkeypatch.setattr(
            d2, "_grok_answer_archive",
            lambda *a, **k: grok.ArchiveAnswer(
                issuer_domain="https://gcapinvest.com",
                archive_url="https://gcapinvest.com/our-lit",
                sources=["https://gcapinvest.com/our-lit"],
            ))
        domain, ranked, ev = d2.locate_candidates(
            "Gryphon Capital Income Trust", "Gryphon Capital",
            engine="grok", client=object())
        assert called["rank"] == 0, "engine=grok 时不得调用 Gemini rank_urls"
        assert ranked[0]["url"] == "https://gcapinvest.com/our-lit"
        assert domain == "https://gcapinvest.com"
        assert ev["engine_used"] == "grok"

    def test_grok_failure_falls_back_to_tavily_visibly(self, monkeypatch):
        """降级必须可见 (Spec G 4.5): evidence 要记 engine_used 与 fallback_reason。"""
        from llm_ingest import discover2 as d2
        from llm_ingest import grok

        def _boom(*a, **k):
            raise grok.GrokError("HTTP 503: upstream_unavailable")

        monkeypatch.setattr(d2, "_grok_answer_archive", _boom)
        monkeypatch.setattr(
            d2, "multi_query_search", lambda *a, **k: ["https://issuer.com/reports"])
        monkeypatch.setattr(
            d2, "rank_urls",
            lambda urls, *a, **k: [{"url": urls[0], "score": 90, "reason": "r"}])

        domain, ranked, ev = d2.locate_candidates(
            "Some Fund", "Some Issuer", engine="grok", client=object())
        assert ranked, "降级后应仍有候选"
        assert ev["engine_requested"] == "grok"
        assert ev["engine_used"] == "tavily"
        assert "503" in ev["fallback_reason"]

    def test_default_engine_is_tavily(self, monkeypatch):
        from llm_ingest import discover2 as d2
        monkeypatch.setattr(d2, "multi_query_search", lambda *a, **k: ["https://x.com/a"])
        monkeypatch.setattr(
            d2, "rank_urls", lambda urls, *a, **k: [{"url": urls[0], "score": 1, "reason": ""}])
        _d, _r, ev = d2.locate_candidates("F", "I", client=object())
        assert ev["engine_used"] == "tavily"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
