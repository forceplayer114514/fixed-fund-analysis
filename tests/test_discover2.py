"""discover2.find_archive_v2 单测.

覆盖 2026-07 Stake 事故回归: 同一归档页里 PDS/TMD 与真月报 PDF 对 fund_name
token 匹配度打平时, 稳定排序可能把非月报排到最前, 首份打样失败不该连累整页
(同 candidate 内该多试几份).
"""
import sys
from pathlib import Path

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest import discover2 as d2


def test_strong_candidate_tries_next_pdf_when_first_is_not_monthly_report(monkeypatch):
    """首份 (PDS) 打样 not_ok, 同 candidate 第二份 (真月报) 该被接着试, 不能整页跳过."""
    fund_name = "stake accumulate"

    monkeypatch.setattr(d2, "multi_query_search", lambda *a, **k: ["https://hellostake.com/au/legal/monthly-performance-report"])
    monkeypatch.setattr(d2, "_pick_issuer_domain", lambda *a, **k: "https://hellostake.com")
    monkeypatch.setattr(
        d2, "rank_urls",
        lambda urls, *a, **k: [{"url": u, "score": 50, "reason": "test"} for u in urls],
    )

    # 三份 PDF 对 fund_name token 匹配度打平 (都含 stake+accumulate), 稳定排序
    # 保持原顺序 -- PDS 恰好排第一, 真月报排第二, 复现"首份非月报"场景。
    pds_url = "https://assets.contentstack.io/.../Stake_Accumulate_Fund_PDS.pdf"
    monthly_url = "https://assets.contentstack.io/.../Stake_Accumulate_report_March2025.pdf"
    third_url = "https://assets.contentstack.io/.../Stake_Accumulate_report_April2025.pdf"

    monkeypatch.setattr(
        d2, "probe_urls",
        lambda urls, **k: [{
            "url": urls[0], "html_ok": True,
            "pdf_urls": [pds_url, monthly_url, third_url],
            "error": None,
        }],
    )

    calls = []

    def fake_confirm(pdf_url, fund_name_arg, *, client=None):
        calls.append(pdf_url)
        if pdf_url == pds_url:
            return False, None  # PDS 不是月报
        return True, object()

    monkeypatch.setattr(d2, "confirm_pdf_is_monthly_report", fake_confirm)

    pointer = d2.find_archive_v2(fund_name, "stake", client=object())

    assert pointer.no_archive is False
    assert pointer.latest_pdf_url == monthly_url
    assert calls == [pds_url, monthly_url]  # 试完 pds 才试 monthly, 没跳过整页
    assert third_url not in calls  # monthly 一过就停, 不多试


def test_strong_candidate_skips_page_when_all_pdfs_unrelated(monkeypatch):
    """整页 PDF 都跟目标基金零 token 匹配 -> 跳过整页, 不发 Gemini 打样请求."""
    fund_name = "enhanced income"

    monkeypatch.setattr(d2, "multi_query_search", lambda *a, **k: ["https://yarracm.com/capabilities"])
    monkeypatch.setattr(d2, "_pick_issuer_domain", lambda *a, **k: "https://yarracm.com")
    monkeypatch.setattr(
        d2, "rank_urls",
        lambda urls, *a, **k: [{"url": u, "score": 50, "reason": "test"} for u in urls],
    )
    unrelated = [f"https://yarracm.com/australian-bond-fund-report-{i}.pdf" for i in range(3)]
    monkeypatch.setattr(
        d2, "probe_urls",
        lambda urls, **k: [{"url": urls[0], "html_ok": True, "pdf_urls": unrelated, "error": None}],
    )

    calls = []
    monkeypatch.setattr(
        d2, "confirm_pdf_is_monthly_report",
        lambda pdf_url, fund_name_arg, *, client=None: (calls.append(pdf_url), (False, None))[1],
    )
    from llm_ingest import navigate as nav_mod
    monkeypatch.setattr(nav_mod, "navigate_one_hop", lambda *a, **k: (None, "", []))

    pointer = d2.find_archive_v2(fund_name, "yarra", client=object())

    assert calls == []  # 零匹配整页跳过, 不浪费一次 Gemini 调用
    assert pointer.no_archive is True


class TestDiscoveredPdfsExcludeSiblingFunds:
    """Spec G 10.3: 同页多基金时, discovered_pdfs 不得带回其他基金的 PDF。

    真实场景 (discover2.py 注释自述): yarracm.com/performance 同挂
    Yarra Enhanced Income Fund 与 Yarra Australian Income Fund 两支基金月报。
    """

    def test_sibling_fund_pdfs_not_returned(self, monkeypatch):
        from llm_ingest import discover2 as d2

        target_pdf = "https://yarracm.com/docs/yarra-enhanced-income-jun-2026.pdf"
        sibling_pdf = "https://yarracm.com/docs/yarra-australian-income-jun-2026.pdf"
        page_url = "https://yarracm.com/performance"

        monkeypatch.setattr(d2, "multi_query_search", lambda *a, **k: [page_url])
        monkeypatch.setattr(
            d2, "rank_urls",
            lambda *a, **k: [{"url": page_url, "score": 90, "reason": "t"}],
        )
        # 该页抓下来含 3 份 PDF: 目标基金 1 份 + 兄弟基金 2 份
        monkeypatch.setattr(
            d2, "probe_urls",
            lambda urls, **k: [{
                "url": page_url,
                "pdf_urls": [
                    target_pdf,
                    sibling_pdf,
                    "https://yarracm.com/docs/yarra-australian-income-may-2026.pdf",
                ],
                "html": "",
            }],
        )
        # 打样一律通过 (模拟目标基金 PDF 验证成功)
        monkeypatch.setattr(
            d2, "confirm_pdf_is_monthly_report", lambda *a, **k: (True, None),
        )

        ptr = d2.find_archive_v2(
            "Yarra Enhanced Income Fund", "Yarra Capital Management",
            client=object(),
        )

        assert target_pdf in ptr.discovered_pdfs
        assert sibling_pdf not in ptr.discovered_pdfs, (
            "兄弟基金 Yarra Australian Income 的 PDF 被带回了 -- "
            "下游 probe_l1_official 不做基金名匹配, 会直接入库"
        )

    def test_navigate_fallback_excludes_sibling_fund_pdfs(self, monkeypatch):
        """Spec G 10.3 补漏 (Task 4 审查发现): 步 6.5 (Spec C1) 自主导航兜底分支
        同样只能带回与本基金匹配度最高的 PDF。

        场景: probe_urls 对 top-N 候选一份 PDF 都没抽到 (只有 HTML), 触发步 6.5
        导航兜底 -- navigate_one_hop 跳一跳后落到一个多基金共用的页面, 里面既有
        目标基金 (Stake Accumulate) 的月报, 也有兄弟基金 (Stake Growth) 的月报。
        修复前 discovered_pdfs=list(next_pdfs) 原样透传整页, 兄弟基金 PDF 会被
        下游 probe_l1_official (不做基金名匹配) 直接入库。
        """
        from llm_ingest import navigate as nav_mod

        target_pdf = "https://hellostake.com/docs/stake-accumulate-report-jun-2026.pdf"
        sibling_pdf = "https://hellostake.com/docs/stake-growth-report-jun-2026.pdf"
        start_url = "https://hellostake.com/legal/monthly-performance-report"
        landing_url = "https://hellostake.com/legal/monthly-performance-report/all"

        monkeypatch.setattr(d2, "multi_query_search", lambda *a, **k: [start_url])
        monkeypatch.setattr(
            d2, "rank_urls",
            lambda *a, **k: [{"url": start_url, "score": 90, "reason": "t"}],
        )
        # top-N 候选抓下来只有 HTML, 没有直接 PDF 链接 -> 步 4/5/6 全部空跑,
        # 落入步 6.5 导航兜底
        monkeypatch.setattr(
            d2, "probe_urls",
            lambda urls, **k: [{
                "url": start_url, "html_ok": True, "pdf_urls": [], "error": None,
                "html_snippet": "<html>...</html>",
            }],
        )
        # 步 6.5.a 重抓全量 HTML (probes 只留 2000 字符 snippet)
        monkeypatch.setattr(d2, "_fetch", lambda *a, **k: "<html>full page</html>")
        # navigate_one_hop 导航跳到多基金共用页, 含目标基金 1 份 + 兄弟基金 1 份
        monkeypatch.setattr(
            nav_mod, "navigate_one_hop",
            lambda *a, **k: (landing_url, "<html>...</html>", [target_pdf, sibling_pdf]),
        )
        monkeypatch.setattr(
            d2, "confirm_pdf_is_monthly_report", lambda *a, **k: (True, None),
        )

        ptr = d2.find_archive_v2(
            "Stake Accumulate Fund", "Stake", client=object(),
        )

        assert ptr.no_archive is False
        assert ptr.latest_pdf_url == target_pdf
        assert target_pdf in ptr.discovered_pdfs
        assert sibling_pdf not in ptr.discovered_pdfs, (
            "兄弟基金 Stake Growth 的 PDF 被步 6.5 导航兜底路径带回了 -- "
            "下游 probe_l1_official 不做基金名匹配, 会直接入库"
        )


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
