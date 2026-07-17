"""Spec C1 Phase 1: navigate 层单测. 全 mock, 不真调网络/API."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Set

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest import navigate as nav
from llm_ingest.navigate import (
    MAX_TOTAL_FETCHES,
    _clean_text,
    _extract_candidate_links,
    _gemini_pick_next_hop,
    navigate_one_hop,
)


# ---------- _clean_text ----------

class TestCleanText:
    def test_strip_tags(self):
        assert _clean_text("<span>Monthly Report</span>") == "Monthly Report"

    def test_collapse_whitespace(self):
        assert _clean_text("  A\n\nB   C  ") == "A B C"

    def test_empty(self):
        assert _clean_text("") == ""


# ---------- _extract_candidate_links ----------

class TestExtractCandidateLinks:
    BASE = "https://hellostake.com/au/support/stake-accumulate/performance-updates-and-statements/46806152848665"

    def test_same_host_keyword_hit(self):
        html = '''
        <a href="/legal/monthly-performance-report">Monthly Performance Report</a>
        <a href="/legal/pds">PDS</a>
        '''
        out = _extract_candidate_links(html, self.BASE, visited=set())
        urls = [u for _, u in out]
        assert "https://hellostake.com/legal/monthly-performance-report" in urls
        # /legal/pds 无关键词命中, 应丢
        assert "https://hellostake.com/legal/pds" not in urls

    def test_url_path_keyword_hit_when_anchor_empty(self):
        # anchor 文本无关键词, 但 URL path 命中 performance -> 保留
        html = '<a href="/au/performance"><img src="x.png" alt=""></a>'
        out = _extract_candidate_links(html, self.BASE, visited=set())
        assert any("performance" in u for _, u in out)

    def test_cross_domain_excluded(self):
        html = '<a href="https://other.com/monthly-report">Monthly Report</a>'
        assert _extract_candidate_links(html, self.BASE, visited=set()) == []

    def test_pdf_direct_link_excluded(self):
        # .pdf 直链归 _extract_pdf_links 管, 本层跳过
        html = '<a href="/reports/monthly.pdf">Monthly Report</a>'
        out = _extract_candidate_links(html, self.BASE, visited=set())
        assert out == []

    def test_excluded_paths(self):
        html = '''
        <a href="/login">Monthly Report</a>
        <a href="/contact">Monthly Report</a>
        <a href="/subscribe">Monthly Report</a>
        <a href="mailto:x@y.com">Monthly Report</a>
        '''
        assert _extract_candidate_links(html, self.BASE, visited=set()) == []

    def test_visited_excluded(self):
        html = '<a href="/legal/monthly-performance-report">Monthly Report</a>'
        visited = {"https://hellostake.com/legal/monthly-performance-report"}
        assert _extract_candidate_links(html, self.BASE, visited=visited) == []

    def test_dedup(self):
        html = '''
        <a href="/legal/monthly-performance-report">Monthly Report</a>
        <a href="/legal/monthly-performance-report">Monthly Performance</a>
        '''
        out = _extract_candidate_links(html, self.BASE, visited=set())
        assert len(out) == 1


# ---------- _gemini_pick_next_hop ----------

class _MockClient:
    """最小 Client 桩: response.text 就是返给 Gemini 的模拟应答."""
    def __init__(self, response_text: str, should_raise: bool = False):
        self.response_text = response_text
        self.should_raise = should_raise
        self.calls: list = []

    def messages(self, prompt: str, max_tokens: int = 512):
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens})
        if self.should_raise:
            raise RuntimeError("simulated api failure")
        return SimpleNamespace(text=self.response_text)


class TestGeminiPickNextHop:
    # 候选选无双强信号的组合, 保证 Gemini 挑 null 时不触发 fallback (专测 Gemini 判定).
    # test_fallback_on_null 单独测强信号 fallback.
    CANDIDATES = [
        ("Distributions", "https://hellostake.com/support/stake-accumulate/investing"),
        ("Fund Update", "https://hellostake.com/au/blog/fund-update"),
    ]
    # 双强信号候选 (anchor + URL 都命中 _NAV_STRONG_SIGNAL): 用于 fallback 单测
    STRONG_CANDIDATES = [
        ("Monthly Performance Report", "https://hellostake.com/legal/monthly-performance-report"),
        ("Blog", "https://hellostake.com/au/blog/x"),
    ]

    def test_pick_valid_url(self):
        client = _MockClient(
            '{"picked_url": "https://hellostake.com/support/stake-accumulate/investing", "reason": "?"}'
        )
        picked = _gemini_pick_next_hop(self.CANDIDATES, "Stake Accumulate", "https://x", client)
        assert picked == "https://hellostake.com/support/stake-accumulate/investing"

    def test_hallucinated_url_rejected(self):
        # Gemini 返一个不在 candidates 里的 URL -> 视作幻觉丢弃; 无双强信号 -> None
        client = _MockClient(
            '{"picked_url": "https://hellostake.com/made-up-page", "reason": "?"}'
        )
        assert _gemini_pick_next_hop(self.CANDIDATES, "Stake", "https://x", client) is None

    def test_null_picked(self):
        client = _MockClient('{"picked_url": null, "reason": "无合适"}')
        assert _gemini_pick_next_hop(self.CANDIDATES, "Stake", "https://x", client) is None

    def test_string_null_treated_as_none(self):
        client = _MockClient('{"picked_url": "null", "reason": "?"}')
        assert _gemini_pick_next_hop(self.CANDIDATES, "Stake", "https://x", client) is None

    def test_api_error_returns_none(self):
        client = _MockClient("", should_raise=True)
        assert _gemini_pick_next_hop(self.CANDIDATES, "Stake", "https://x", client) is None

    def test_empty_candidates(self):
        client = _MockClient('{"picked_url": null}')
        assert _gemini_pick_next_hop([], "Stake", "https://x", client) is None
        # 空候选不应真调 Gemini
        assert client.calls == []

    def test_json_with_markdown_fence(self):
        # _parse_json_response 应该剥 ```json 围栏
        client = _MockClient(
            '```json\n{"picked_url": "https://hellostake.com/support/stake-accumulate/investing"}\n```'
        )
        picked = _gemini_pick_next_hop(self.CANDIDATES, "Stake", "https://x", client)
        assert picked == "https://hellostake.com/support/stake-accumulate/investing"

    def test_malformed_json(self):
        client = _MockClient("not json at all")
        assert _gemini_pick_next_hop(self.CANDIDATES, "Stake", "https://x", client) is None

    def test_fallback_on_gemini_null_with_strong_signal(self):
        """Spec C1 fallback: Gemini 返 null 但 top-1 anchor+URL 双命中强信号
        (Stake 场景: Monthly Performance Report), 兜底返 top-1."""
        client = _MockClient('{"picked_url": null, "reason": "保守"}')
        picked = _gemini_pick_next_hop(
            self.STRONG_CANDIDATES, "Stake", "https://hellostake.com/au/other", client,
        )
        assert picked == "https://hellostake.com/legal/monthly-performance-report"

    def test_fallback_skips_self_reference(self):
        """top-1 URL path 与 current_url 完全相同 (自指) -> 不走 fallback."""
        client = _MockClient('{"picked_url": null}')
        # top-1 URL path = current_url path -> 自指
        cands = [
            ("Monthly Performance Report", "https://hellostake.com/legal/monthly-performance-report"),
        ]
        assert _gemini_pick_next_hop(
            cands, "Stake", "https://hellostake.com/legal/monthly-performance-report", client,
        ) is None

    def test_fallback_skips_au_prefix_self(self):
        """/au/foo vs /foo 视作自指 (Stake au 站与国际站相同页)."""
        client = _MockClient('{"picked_url": null}')
        cands = [
            ("Performance updates statement", "https://hellostake.com/support/x/performance-updates-and-statements/1"),
        ]
        picked = _gemini_pick_next_hop(
            cands, "Stake",
            "https://hellostake.com/au/support/x/performance-updates-and-statements/1",
            client,
        )
        # anchor 无 STRONG_SIGNAL 命中 -> fallback 本就不触发 -> None
        assert picked is None


# ---------- navigate_one_hop (整合) ----------

class TestNavigateOneHop:
    START_URL = "https://hellostake.com/au/support/stake-accumulate/performance-updates-and-statements/46806152848665"
    START_HTML = '''
    <html><body>
    <a href="/legal/monthly-performance-report">Monthly Performance Report</a>
    <a href="/legal/pds">PDS</a>
    </body></html>
    '''
    NEXT_URL = "https://hellostake.com/legal/monthly-performance-report"
    NEXT_HTML = '''
    <a href="https://assets.contentstack.io/AccumulateReport_January26.pdf">Jan 26</a>
    <a href="https://assets.contentstack.io/Accumulate_November_2025.pdf">Nov 25</a>
    <a href="https://assets.contentstack.io/AccumulateReport_Sept_2025.pdf">Sep 25</a>
    '''

    def test_happy_path(self, monkeypatch):
        # mock _fetch (被 navigate 直接 import) 返 NEXT_HTML
        monkeypatch.setattr(nav, "_fetch", lambda url, timeout=30: self.NEXT_HTML)
        client = _MockClient(
            f'{{"picked_url": "{self.NEXT_URL}", "reason": "path 含 monthly-performance-report"}}'
        )
        visited: Set[str] = {self.START_URL}
        next_url, next_html, pdf_urls = navigate_one_hop(
            self.START_URL, self.START_HTML, "Stake Accumulate", None, visited, client,
        )
        assert next_url == self.NEXT_URL
        assert next_html == self.NEXT_HTML
        # 3 份 PDF 全抽到
        assert len(pdf_urls) == 3
        assert all(u.endswith(".pdf") for u in pdf_urls)
        # visited 已更新
        assert self.NEXT_URL in visited

    def test_no_candidates(self, monkeypatch):
        monkeypatch.setattr(nav, "_fetch", lambda url, timeout=30: "SHOULD-NOT-BE-CALLED")
        client = _MockClient('{"picked_url": null}')
        visited: Set[str] = set()
        out = navigate_one_hop(self.START_URL, "<html></html>", "Stake", None, visited, client)
        assert out == (None, None, [])
        # Gemini 不该被调 (0 候选就直接返)
        assert client.calls == []

    def test_gemini_returns_none(self, monkeypatch):
        """Gemini 返 null 且候选无双强信号 -> 无 fallback, 返 (None, None, [])."""
        weak_html = '<a href="/legal/pds-info">PDS Info</a><a href="/support/x">Statement Update</a>'
        monkeypatch.setattr(nav, "_fetch", lambda url, timeout=30: "SHOULD-NOT-BE-CALLED")
        client = _MockClient('{"picked_url": null, "reason": "无合适"}')
        visited: Set[str] = {self.START_URL}
        out = navigate_one_hop(self.START_URL, weak_html, "Stake", None, visited, client)
        assert out == (None, None, [])

    def test_fetch_failure(self, monkeypatch):
        # Gemini 挑了 URL 但 _fetch 返 None (超时/网络)
        monkeypatch.setattr(nav, "_fetch", lambda url, timeout=30: None)
        client = _MockClient(f'{{"picked_url": "{self.NEXT_URL}"}}')
        visited: Set[str] = {self.START_URL}
        next_url, next_html, pdf_urls = navigate_one_hop(
            self.START_URL, self.START_HTML, "Stake", None, visited, client,
        )
        assert next_url == self.NEXT_URL
        assert next_html is None
        assert pdf_urls == []

    def test_max_fetches_hard_stop(self, monkeypatch):
        # visited 已到上限 -> 直接 (None, None, [])
        monkeypatch.setattr(nav, "_fetch", lambda url, timeout=30: "SHOULD-NOT-BE-CALLED")
        client = _MockClient('{"picked_url": null}')
        visited: Set[str] = {f"https://x.com/{i}" for i in range(MAX_TOTAL_FETCHES)}
        out = navigate_one_hop(self.START_URL, self.START_HTML, "Stake", None, visited, client)
        assert out == (None, None, [])
        assert client.calls == []


# ---------- fixture E2E (需要 fixture 文件, 允许 skip) ----------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.skipif(
    not (FIXTURES_DIR / "stake_archive.html").exists(),
    reason="stake fixture not captured yet (Phase 4 pending)",
)
class TestNavigateStakeFixture:
    """真样本回归: 拿 hellostake 归档页 HTML 走完 1 跳到 monthly-performance-report."""

    def test_stake_archive_to_monthly(self, monkeypatch):
        archive_html = (FIXTURES_DIR / "stake_archive.html").read_text()
        monthly_html = (FIXTURES_DIR / "stake_monthly.html").read_text()
        # 模拟 _fetch 拿 monthly page
        def fake_fetch(url, timeout=30):
            if "monthly-performance-report" in url:
                return monthly_html
            return None
        monkeypatch.setattr(nav, "_fetch", fake_fetch)
        client = _MockClient(
            '{"picked_url": "https://hellostake.com/legal/monthly-performance-report", "reason": "path monthly"}'
        )
        visited: Set[str] = set()
        next_url, next_html, pdfs = navigate_one_hop(
            "https://hellostake.com/au/support/stake-accumulate/performance-updates-and-statements/46806152848665",
            archive_html, "Stake Accumulate Fund", None, visited, client,
        )
        assert next_url == "https://hellostake.com/legal/monthly-performance-report"
        # 至少 5 份 PDF (探针实测 16)
        assert len(pdfs) >= 5
