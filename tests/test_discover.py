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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
