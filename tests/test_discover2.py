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
            "nav_urls": [], "error": None,
        }])
        # 只判出 1 个月 -> 会继续找更完整的来源, 打桩住别真联网
        monkeypatch.setattr(d2, "_fetch", lambda *a, **k: "")
        from llm_ingest import navigate as nav_mod
        monkeypatch.setattr(nav_mod, "navigate_one_hop", lambda *a, **k: (None, None, []))

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
        monkeypatch.setattr(d2, "_fetch", lambda *a, **k: "")
        from llm_ingest import navigate as nav_mod
        monkeypatch.setattr(nav_mod, "navigate_one_hop", lambda *a, **k: (None, None, []))

        ptr = d2.find_archive_v2("F", "I", client=object())
        assert ptr.archive_url == good
        assert ptr.discovered_links == [("2026-06", pdf)]


class TestNonPdfPageLinksNeverReachTheClassifier:
    """2026-07-29 事故的两个根因回归。

    Grok 那轮给的是 Stake 的 Zendesk 支持页。该页 0 条真 .pdf, 但页里有两条
    "链接文字像月报"的普通网页:
      https://hellostake.com/legal/monthly-performance-report   (真归档页本身)
      https://hellostake.com/au/ambition-report-2025            (营销页)
    旧实现把这两条混进 PDF 清单一起交判定, 于是:
      (a) 营销页被判成月报 (模型从 "-2025" 看到年份自行补月份 01)
      (b) "第一个非空即返回" 让这一条误判直接收工 -- 真归档页就在同一批候选里,
          再也没机会被跳过去
      (c) 那条网页当 PDF 下载, 必然失败; 还凭假的 2025-01 起点把 16 个月写成缺口
    """

    SUPPORT_HTML = (
        '<a href="/legal/monthly-performance-report">Monthly performance report</a>'
        '<a href="/au/ambition-report-2025">Ambition Report 2025</a>'
        '<a href="/au/support/contact">Contact us</a>'
    )

    def test_two_extractors_are_separated(self):
        base = "https://hellostake.com/au/support/x"
        assert d2._extract_pdf_links(self.SUPPORT_HTML, base) == []
        assert d2._extract_monthlyish_page_links(self.SUPPORT_HTML, base) == [
            "https://hellostake.com/legal/monthly-performance-report",
            "https://hellostake.com/au/ambition-report-2025",
        ]

    def test_probe_keeps_pdf_and_nav_candidates_apart(self, monkeypatch):
        base = "https://hellostake.com/au/support/x"
        monkeypatch.setattr(d2, "_fetch", lambda *a, **k: self.SUPPORT_HTML)
        p = d2._probe_one(base)
        assert p["pdf_urls"] == [], "非 PDF 网页链接不得进 pdf_urls"
        assert len(p["nav_urls"]) == 2

    def test_support_page_hops_to_real_archive_instead_of_settling(self, monkeypatch):
        """整条链路回归: 支持页 0 条 PDF -> 顺着"文字像月报"的中转链接跳过去 ->
        真归档页的 16 份月报。"""
        support = "https://hellostake.com/au/support/stake-accumulate/x/46806152848665"
        archive = "https://hellostake.com/legal/monthly-performance-report"
        monthlies = {
            f"https://cdn/AccumulateReport_{m}.pdf": ym
            for m, ym in (("March2025", "2025-03"), ("April25", "2025-04"),
                          ("Sept_2025", "2025-09"), ("Jun26", "2026-06"))
        }
        archive_html = "".join(f'<a href="{u}">x</a>' for u in monthlies)

        _stub_locate(monkeypatch, support)
        monkeypatch.setattr(d2, "probe_urls", lambda urls, **k: [{
            "url": support, "html_ok": True, "pdf_urls": [],
            "nav_urls": [archive, "https://hellostake.com/au/ambition-report-2025"],
            "error": None, "html_snippet": "",
        }])
        monkeypatch.setattr(d2, "_fetch",
                            lambda u, **k: archive_html if u == archive else "<html/>")

        ptr = d2.find_archive_v2(
            "stake accumulate", "stake",
            client=SelectStubClient(lambda u: monthlies.get(u)))

        assert ptr.archive_url == archive, "该跳到真归档页, 而不是停在支持页"
        assert ptr.no_archive is False
        assert len(ptr.discovered_links) == 4


class TestPicksSourceWithMostMonths:
    def test_one_false_positive_does_not_beat_the_real_archive(self, monkeypatch):
        """排序靠前的页只判出 1 个月时, 不能就此收工 -- 后面那页有 12 个月。"""
        thin = "https://x.com/marketing"
        rich = "https://x.com/monthly-reports"
        thin_pdf = "https://cdn/brochure-Jan2025.pdf"
        rich_pdfs = {f"https://cdn/report-{m:02d}-2025.pdf": f"2025-{m:02d}"
                     for m in range(1, 13)}

        monkeypatch.setattr(d2, "multi_query_search", lambda *a, **k: [thin, rich])
        monkeypatch.setattr(d2, "_pick_issuer_domain", lambda *a, **k: None)
        monkeypatch.setattr(
            d2, "rank_urls",
            lambda urls, *a, **k: [{"url": u, "score": 9, "reason": ""} for u in urls])
        monkeypatch.setattr(d2, "probe_urls", lambda urls, **k: [
            {"url": thin, "html_ok": True, "pdf_urls": [thin_pdf], "nav_urls": [],
             "error": None},
            {"url": rich, "html_ok": True, "pdf_urls": list(rich_pdfs),
             "nav_urls": [], "error": None},
        ])

        answers = dict(rich_pdfs)
        answers[thin_pdf] = "2025-01"
        ptr = d2.find_archive_v2("F", "I",
                                 client=SelectStubClient(lambda u: answers.get(u)))

        assert ptr.archive_url == rich
        assert len(ptr.discovered_links) == 12

    def test_single_month_source_still_used_when_nothing_better(self, monkeypatch):
        page = "https://gcapinvest.com/our-lit"
        only = "https://cdn/gci-inv-update-jun-2026.pdf"
        _stub_locate(monkeypatch, page)
        monkeypatch.setattr(d2, "probe_urls", lambda urls, **k: [{
            "url": page, "html_ok": True, "pdf_urls": [only], "nav_urls": [],
            "error": None,
        }])
        ptr = d2.find_archive_v2(
            "Gryphon Capital Income Trust", "Gryphon",
            client=SelectStubClient(lambda u: "2026-06" if u == only else None))

        assert ptr.discovered_links == [("2026-06", only)]
        assert ptr.no_archive is True   # 1 个月 -> 单份最新, 上游继续补历史


class TestGrokNameVariants:
    """2026-07-29: 两次并发 Grok 各用一个基金名写法。失败那轮传进去的是全小写、
    无类型词的 "stake accumulate"; 几次成功的传的是 "Stake Accumulate Fund"。"""

    def test_capitalizes_each_word(self):
        a, b = d2._grok_name_variants("stake accumulate")
        assert a == "Stake Accumulate"
        assert b == "Stake Accumulate Fund"

    def test_preserves_acronyms_typed_in_caps(self):
        """不能用 str.title() -- 它会把 JCB 变成 Jcb。"""
        a, b = d2._grok_name_variants("JCB Active Bond Fund")
        assert a == "JCB Active Bond Fund"
        assert b == "JCB Active Bond Fund"

    def test_does_not_append_fund_after_a_type_word(self):
        """否则 "Gryphon Capital Income Trust" 会变成 "...Trust Fund", 更糟。"""
        for name in ("gryphon capital income trust", "Some ETF", "X Portfolio"):
            a, b = d2._grok_name_variants(name)
            assert b == a, f"{name!r} 不该被补 Fund, 得到 {b!r}"

    def test_already_has_fund_makes_both_variants_identical(self):
        a, b = d2._grok_name_variants("bentham global income fund")
        assert a == b == "Bentham Global Income Fund"


class TestGrokAskedTwiceConcurrently:
    def _stub_answer(self, monkeypatch, by_name):
        from llm_ingest import grok
        seen = []

        def _ask(name, issuer, asx):
            seen.append(name)
            url = by_name.get(name)
            if isinstance(url, Exception):
                raise url
            return grok.ArchiveAnswer(issuer_domain="https://hellostake.com",
                                      archive_url=url, sources=[], evidence="e")

        monkeypatch.setattr(d2, "_grok_answer_archive", _ask)
        return seen

    def test_both_name_variants_are_asked(self, monkeypatch):
        seen = self._stub_answer(monkeypatch, {
            "Stake Accumulate": "https://a/1",
            "Stake Accumulate Fund": "https://a/2",
        })
        _d, ranked, ev = d2.locate_candidates(
            "stake accumulate", "stake", engine="grok", client=object())
        assert sorted(seen) == ["Stake Accumulate", "Stake Accumulate Fund"]
        assert ev["grok_name_variants"] == ["Stake Accumulate",
                                            "Stake Accumulate Fund"]

    def test_two_different_answers_both_become_candidates(self, monkeypatch):
        """不一致时不投票, 两个都打开验证, 由 find_archive_v2 按月份数取胜者。"""
        self._stub_answer(monkeypatch, {
            "Stake Accumulate": "https://hellostake.com/au/support/x",
            "Stake Accumulate Fund": "https://hellostake.com/legal/monthly",
        })
        _d, ranked, ev = d2.locate_candidates(
            "stake accumulate", "stake", engine="grok", client=object())
        assert [r["url"] for r in ranked] == [
            "https://hellostake.com/au/support/x",
            "https://hellostake.com/legal/monthly",
        ]
        assert ev["grok_agreed"] is False

    def test_identical_answers_dedupe_to_one_candidate(self, monkeypatch):
        same = "https://hellostake.com/legal/monthly"
        self._stub_answer(monkeypatch, {"Stake Accumulate": same,
                                        "Stake Accumulate Fund": same})
        _d, ranked, ev = d2.locate_candidates(
            "stake accumulate", "stake", engine="grok", client=object())
        assert [r["url"] for r in ranked] == [same]
        assert ev["grok_agreed"] is True

    def test_one_failure_still_uses_the_other_answer(self, monkeypatch):
        """原来只问一次, 一个 503 就整轮降级 Tavily。"""
        from llm_ingest import grok
        self._stub_answer(monkeypatch, {
            "Stake Accumulate": grok.GrokError("HTTP 503"),
            "Stake Accumulate Fund": "https://hellostake.com/legal/monthly",
        })
        monkeypatch.setattr(d2, "multi_query_search",
                            lambda *a, **k: pytest.fail("不该降级 Tavily"))
        _d, ranked, ev = d2.locate_candidates(
            "stake accumulate", "stake", engine="grok", client=object())
        assert [r["url"] for r in ranked] == ["https://hellostake.com/legal/monthly"]
        assert ev["engine_used"] == "grok"

    def test_both_failures_fall_back_to_tavily_visibly(self, monkeypatch):
        from llm_ingest import grok
        self._stub_answer(monkeypatch, {
            "Stake Accumulate": grok.GrokError("HTTP 503"),
            "Stake Accumulate Fund": grok.GrokError("HTTP 503"),
        })
        monkeypatch.setattr(d2, "multi_query_search", lambda *a, **k: ["https://x/a"])
        monkeypatch.setattr(d2, "rank_urls",
                            lambda urls, *a, **k: [{"url": urls[0], "score": 1,
                                                    "reason": ""}])
        _d, ranked, ev = d2.locate_candidates(
            "stake accumulate", "stake", engine="grok", client=object())
        assert ranked and ev["engine_used"] == "tavily"
        assert "503" in ev["fallback_reason"]


class TestFetchRetryAndEarlyStop:
    def test_probe_retries_fetch_once(self, monkeypatch):
        """瞬时抓取失败不该让整页算空 -- Grok 路上候选只有一两个, 丢掉就降级导航。"""
        calls = {"n": 0}

        def _flaky(url, timeout=None):
            calls["n"] += 1
            return "" if calls["n"] == 1 else '<a href="/a.pdf">x</a>'

        monkeypatch.setattr(d2, "_fetch", _flaky)
        p = d2._probe_one("https://x.com/archive")
        assert calls["n"] == 2
        assert p["html_ok"] is True
        assert p["pdf_urls"] == ["https://x.com/a.pdf"]

    def test_one_month_is_enough_no_navigation(self, monkeypatch):
        """归档页上确实只挂 1 份月报时 (GCI 实况) 就该收工。原来要求 >= 2 个月,
        不到就跑导航 -- 导航更贵、成功率更低, 只该当托底。"""
        page = "https://gcapinvest.com/our-lit"
        only = "https://cdn/gci-inv-update-jun-2026.pdf"
        _stub_locate(monkeypatch, page)
        monkeypatch.setattr(d2, "probe_urls", lambda urls, **k: [{
            "url": page, "html_ok": True, "pdf_urls": [only], "nav_urls": [],
            "error": None,
        }])
        monkeypatch.setattr(d2, "_fetch",
                            lambda *a, **k: pytest.fail("不该再抓页 (已收工)"))
        from llm_ingest import navigate as nav_mod
        monkeypatch.setattr(nav_mod, "navigate_one_hop",
                            lambda *a, **k: pytest.fail("不该跑导航 (已收工)"))

        ptr = d2.find_archive_v2(
            "Gryphon Capital Income Trust", "Gryphon",
            client=SelectStubClient(lambda u: "2026-06" if u == only else None))
        assert ptr.discovered_links == [("2026-06", only)]


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
