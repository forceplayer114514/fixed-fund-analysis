"""Phase 2 discover 单测. 只测确定性 helpers 与 CDX 记录解析, 不真调 API/网络."""
import sys
from pathlib import Path

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest.discover import (
    ArchivePointer,
    _dedup_links,
    _month_range,
    _parse_ym_from_text,
    _recent_published_month,
    _valid_ym,
    parse_archive_page,
    probe_l2_wayback,
    run_discovery,
    _parse_json_response,
)


# ---------- _parse_ym_from_text ----------

class TestParseYmFromText:
    def test_month_name_year(self):
        assert _parse_ym_from_text("March 2025") == "2025-03"

    def test_abbr_dash_year(self):
        assert _parse_ym_from_text("Mar-2025") == "2025-03"

    def test_iso_ym(self):
        assert _parse_ym_from_text("2025-03") == "2025-03"

    def test_urlish_yyyymm(self):
        assert _parse_ym_from_text("monthly-report-202503.pdf") == "2025-03"

    def test_yyyymmdd(self):
        assert _parse_ym_from_text("gci_gross_20250131.pdf") == "2025-01"

    def test_yyyymmdd_leading(self):
        assert _parse_ym_from_text("20250131-Report.pdf") == "2025-01"

    def test_year_first_month_name(self):
        assert _parse_ym_from_text("2025-March") == "2025-03"

    def test_underscore_month_name_year(self):
        # Spec C1: Stake 命名 Accumulate_June_2025.pdf, \b 不识 _underscore, 用 (?:\b|_)
        assert _parse_ym_from_text("Accumulate_June_2025.pdf") == "2025-06"

    def test_month_name_two_digit_year(self):
        # Stake: AccumulateReport_January26.pdf
        assert _parse_ym_from_text("AccumulateReport_January26.pdf") == "2026-01"
        assert _parse_ym_from_text("Accumulate_report_March26.pdf") == "2026-03"

    def test_two_digit_year_out_of_range_rejected(self):
        # 只接受 19..30 (2019-2030); 老年份不该被 2-digit 匹配
        assert _parse_ym_from_text("something_January99.pdf") is None
        assert _parse_ym_from_text("something_January05.pdf") is None

    def test_none_when_no_match(self):
        assert _parse_ym_from_text("nothing_relevant.pdf") is None

    def test_empty(self):
        assert _parse_ym_from_text("") is None

    def test_invalid_month_rejected(self):
        # 202513 = 13 月 -> reject
        assert _parse_ym_from_text("something-202513.pdf") is None


# ---------- _valid_ym ----------

class TestValidYm:
    def test_ok(self):
        assert _valid_ym("2025-03")

    def test_bad_month(self):
        assert not _valid_ym("2025-13")

    def test_wrong_format(self):
        assert not _valid_ym("2025-3")

    def test_empty(self):
        assert not _valid_ym("")
        assert not _valid_ym(None)


# ---------- _month_range ----------

class TestMonthRange:
    def test_within_year(self):
        assert _month_range("2025-01", "2025-03") == ["2025-01", "2025-02", "2025-03"]

    def test_cross_year(self):
        assert _month_range("2024-11", "2025-02") == ["2024-11", "2024-12", "2025-01", "2025-02"]

    def test_single(self):
        assert _month_range("2025-05", "2025-05") == ["2025-05"]


# ---------- _dedup_links ----------

class TestDedupLinks:
    def test_dedup_by_ym(self):
        pairs = [("2025-03", "a"), ("2025-03", "b"), ("2025-01", "c")]
        assert _dedup_links(pairs) == [("2025-01", "c"), ("2025-03", "a")]

    def test_drops_invalid_ym(self):
        pairs = [("2025-13", "a"), ("bogus", "b"), ("2025-01", "c")]
        assert _dedup_links(pairs) == [("2025-01", "c")]

    def test_drops_empty_url(self):
        assert _dedup_links([("2025-01", "")]) == []


# ---------- _parse_json_response ----------

class TestParseJsonResponse:
    def test_bare(self):
        assert _parse_json_response('{"a":1}') == {"a": 1}

    def test_fenced(self):
        assert _parse_json_response('```json\n{"a":1}\n```') == {"a": 1}

    def test_with_prose(self):
        assert _parse_json_response('here you go:\n{"x":"y"}\n') == {"x": "y"}

    def test_invalid(self):
        assert _parse_json_response("not json at all") is None

    def test_bad_json(self):
        assert _parse_json_response('{"a": 1,,}') is None


# ---------- _recent_published_month ----------

def test_recent_published_month_format():
    ym = _recent_published_month()
    assert _valid_ym(ym)


# ---------- probe_l2_wayback: gap_set 空短路 ----------

class TestProbeL2:
    def test_no_gap_returns_empty(self):
        assert probe_l2_wayback("example.com", set()) == []

    def test_no_domain_returns_empty(self):
        assert probe_l2_wayback("", {"2025-01"}) == []


# ---------- parse_archive_page + run_discovery: 用 monkeypatch 打桩 ----------

class TestParseArchivePage:
    def test_stub_gemini(self, monkeypatch):
        """打桩 Client.messages 模拟 Gemini 返 JSON list."""
        from llm_ingest import discover as disc

        class FakeResp:
            text = '{"links":[{"ym":"2025-01","url":"https://x/2025-01.pdf"},{"ym":"2025-02","url":"https://x/2025-02.pdf"}],"has_more_pages":true,"next_page_hint":"page=2","unparseable_count":1}'

        class FakeClient:
            def messages(self, prompt, max_tokens=None):
                return FakeResp()

        links, more, hint, unp = parse_archive_page("<html>...</html>", client=FakeClient())
        assert links == [("2025-01", "https://x/2025-01.pdf"),
                         ("2025-02", "https://x/2025-02.pdf")]
        assert more is True
        assert hint == "page=2"
        assert unp == 1

    def test_gemini_invalid_json_returns_empty(self):
        class FakeResp:
            text = "nope, not json"

        class FakeClient:
            def messages(self, prompt, max_tokens=None):
                return FakeResp()

        links, more, hint, unp = parse_archive_page("<html>", client=FakeClient())
        assert links == []
        assert more is False

    def test_code_regex_path_skips_llm_when_pdf_links_past_80kb_cutoff(self):
        """Stake 实况回归: 归档页 >80KB 且全部 PDF 链接落在截断线之后,
        代码正则应直接从全文抓到, 完全不调 LLM (client.messages 被调即 fail)."""
        class FailingClient:
            def messages(self, prompt, max_tokens=None):
                raise AssertionError("不该调 LLM -- 代码正则应已命中")

        padding = "<!-- filler -->" * 6000  # far more than 80_000 chars
        html = (
            "<html><body>" + padding
            + '<a href="https://cdn.example.com/AccumulateReport_March2025.pdf">March 2025</a>'
            + '<a href="https://cdn.example.com/Accumulate_April_2025.pdf">April 2025</a>'
            + "</body></html>"
        )
        assert len(html) > 80_000
        links, more, hint, unp = parse_archive_page(
            html, client=FailingClient(), base_url="https://issuer.com/archive",
        )
        assert links == [
            ("2025-03", "https://cdn.example.com/AccumulateReport_March2025.pdf"),
            ("2025-04", "https://cdn.example.com/Accumulate_April_2025.pdf"),
        ]
        assert unp == 0

    def test_code_regex_path_filters_unrelated_domain_date_path_noise(self):
        """Stake 实况: 混入无关域名的条款 PDF, 路径含 "/2024/02/" (上传日期非月报期),
        不应被文件名反解逻辑之外的整段 URL 兜底误判为 2024-02。"""
        class FailingClient:
            def messages(self, prompt, max_tokens=None):
                raise AssertionError("不该调 LLM")

        html = (
            '<a href="https://finclear.com.au/wp-content/uploads/2024/02/Terms-of-Trade.pdf">Terms</a>'
            '<a href="https://cdn.example.com/Accumulate_March_2025.pdf">March 2025</a>'
        )
        links, _more, _hint, _unp = parse_archive_page(
            html, client=FailingClient(), base_url="https://issuer.com/archive",
        )
        assert links == [("2025-03", "https://cdn.example.com/Accumulate_March_2025.pdf")]

    def test_code_regex_path_falls_back_to_llm_when_no_pdf_hrefs(self):
        """正则一无所获 (无 .pdf 后缀 href, 如中转页无扩展名) 时仍退回 LLM 路径, 行为不变."""
        class FakeResp:
            text = '{"links":[{"ym":"2025-01","url":"https://x/2025-01"}],"has_more_pages":false,"next_page_hint":null,"unparseable_count":0}'

        class FakeClient:
            def messages(self, prompt, max_tokens=None):
                return FakeResp()

        html = '<a href="https://x/2025-01">Report Jan 2025</a>'
        links, more, hint, unp = parse_archive_page(
            html, client=FakeClient(), base_url="https://x/",
        )
        assert links == [("2025-01", "https://x/2025-01")]

    def test_code_regex_path_pagination_heuristic(self):
        """代码路径命中时, 页面含分页字样应把 has_more_pages 猜为 True (启发式)."""
        class FailingClient:
            def messages(self, prompt, max_tokens=None):
                raise AssertionError("不该调 LLM")

        html = (
            '<a href="https://cdn.example.com/Accumulate_March_2025.pdf">March 2025</a>'
            '<button>Load More</button>'
        )
        links, more, hint, unp = parse_archive_page(
            html, client=FailingClient(), base_url="https://issuer.com/archive",
        )
        assert links == [("2025-03", "https://cdn.example.com/Accumulate_March_2025.pdf")]
        assert more is True
        assert hint == ""


# ---------- run_discovery: 全打桩集成 ----------

class TestRunDiscovery:
    def test_l1_ok_no_l2_needed(self, monkeypatch):
        """L1 覆盖到 inception, gaps=空, L2 无缺口应短路."""
        from llm_ingest import discover as disc

        def fake_probe_l1(*args, **kwargs):
            links = [("2025-01", "a"), ("2025-02", "b"), ("2025-03", "c")]
            ptr = ArchivePointer(
                archive_url="https://issuer.com/archive",
                pagination_param=None, no_archive=False,
                latest_pdf_url=None,
                issuer_domain_confirmed="https://issuer.com",
                evidence="ok",
            )
            return (links, ptr, 0)

        def fake_probe_l2(*args, **kwargs):
            raise AssertionError("L2 不应被调 (无缺口)")

        monkeypatch.setattr(disc, "probe_l1_official", fake_probe_l1)
        monkeypatch.setattr(disc, "probe_l2_wayback", fake_probe_l2)

        r = disc.run_discovery(
            "F", "Issuer", "fund_id",
            issuer_domain="https://issuer.com",
            inception_ym="2025-01",
            latest_ym="2025-03",
        )
        assert len(r.links) == 3
        assert r.gaps == []
        assert r.per_level_contribution == {"L1": 3}

    def test_l1_partial_l2_fills(self, monkeypatch):
        """L1 只覆盖 2025-02/03, L2 补 2025-01."""
        from llm_ingest import discover as disc

        def fake_probe_l1(*args, **kwargs):
            links = [("2025-02", "b"), ("2025-03", "c")]
            ptr = ArchivePointer(
                archive_url="https://issuer.com/archive",
                pagination_param=None, no_archive=False,
                latest_pdf_url=None,
                issuer_domain_confirmed="https://issuer.com",
                evidence="",
            )
            return (links, ptr, 0)

        def fake_probe_l2(domain, gap_set):
            assert gap_set == {"2025-01"}
            return [("2025-01", "wayback:a")]

        monkeypatch.setattr(disc, "probe_l1_official", fake_probe_l1)
        monkeypatch.setattr(disc, "probe_l2_wayback", fake_probe_l2)

        r = disc.run_discovery(
            "F", "Issuer", "fund_id",
            issuer_domain="https://issuer.com",
            inception_ym="2025-01",
            latest_ym="2025-03",
        )
        assert set(ym for ym, _ in r.links) == {"2025-01", "2025-02", "2025-03"}
        assert r.gaps == []
        assert r.per_level_contribution["L1"] == 2
        assert r.per_level_contribution["L2"] == 1

    def test_l1_empty_no_expected_range(self, monkeypatch):
        """L1 空 + 无 inception_ym -> expected 空, gaps=空, L2 无从查."""
        from llm_ingest import discover as disc

        def fake_probe_l1(*args, **kwargs):
            ptr = ArchivePointer(
                archive_url=None, pagination_param=None, no_archive=True,
                latest_pdf_url=None, issuer_domain_confirmed=None, evidence="",
            )
            return ([], ptr, 0)

        called = {"l2": False}

        def fake_probe_l2(*args, **kwargs):
            called["l2"] = True
            return []

        monkeypatch.setattr(disc, "probe_l1_official", fake_probe_l1)
        monkeypatch.setattr(disc, "probe_l2_wayback", fake_probe_l2)

        r = disc.run_discovery("F", "Issuer", "fund_id")
        assert r.links == []
        assert r.gaps == []
        assert not called["l2"], "无期望范围时 L2 不该被调"


class TestL2LocalCacheFallback:
    """Spec C1 Phase 3: run_discovery 空手时扫本地 pdf_cache 兜底."""

    def test_gci_local_cache_hit(self, monkeypatch):
        """GCI 88 份本地 PDF 应在 L1/L2 全空时经 L2.6 全部识别为 file:// links."""
        from llm_ingest import discover as disc
        ap = disc.ArchivePointer(
            archive_url=None, pagination_param=None, no_archive=True,
            latest_pdf_url=None, issuer_domain_confirmed=None, evidence="mock",
            raw={},
        )
        monkeypatch.setattr(disc, "probe_l1_official", lambda *a, **kw: ([], ap, 0))
        monkeypatch.setattr(disc, "probe_l2_wayback", lambda *a, **kw: [])
        rep = disc.run_discovery(
            "Gryphon Capital Income Trust", "Gryphon Capital",
            fund_id="gryphon_capital_income",
            issuer_domain="gcapinvest.com",
        )
        # 88 份历史 PDF 都应变成 file:// links (spec: data/pdf_cache/gryphon_capital_income/)
        assert len(rep.links) >= 80, f"expected ≥80 local PDFs, got {len(rep.links)}"
        assert all(url.startswith("file://") for _, url in rep.links)
        assert rep.per_level_contribution.get("L_local", 0) >= 80
        # evidence_log 应有 L_local 条
        assert any(e.get("level") == "L_local" for e in rep.evidence_log)

    def test_no_cache_dir_no_effect(self, monkeypatch):
        """本地无 pdf_cache 目录时不 crash, links 依旧为 0."""
        from llm_ingest import discover as disc
        ap = disc.ArchivePointer(
            archive_url=None, pagination_param=None, no_archive=True,
            latest_pdf_url=None, issuer_domain_confirmed=None, evidence="mock",
            raw={},
        )
        monkeypatch.setattr(disc, "probe_l1_official", lambda *a, **kw: ([], ap, 0))
        monkeypatch.setattr(disc, "probe_l2_wayback", lambda *a, **kw: [])
        rep = disc.run_discovery(
            "Nonexistent Fund", "N",
            fund_id="nonexistent_fund_xyz_no_dir",
        )
        assert rep.links == []
        assert "L_local" not in rep.per_level_contribution


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
