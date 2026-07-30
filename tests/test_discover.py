"""Phase 2 discover 单测. 只测确定性 helpers 与 CDX 记录解析, 不真调 API/网络."""
import sys
from pathlib import Path

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from conftest import SelectStubClient
from llm_ingest.discover import (
    ArchivePointer,
    _dedup_links,
    _fetch,
    _month_range,
    _parse_ym_from_text,
    _recent_published_month,
    _valid_ym,
    ClassifyError,
    classify_pdf_links,
    extract_all_pdf_links,
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

    def test_sept_4letter_abbreviation(self):
        """回归 (2026-07 Stake 9月幻影缺口事故): 文件名 AccumulateReport_Sept_2025.pdf
        用 4 字母 "Sept" (September 唯一常见的非 3 字母缩写), 旧版 _MONTHS_ABBR 只有
        3 字母 "sep", 正则被多出的 "t" 卡住整体失配, ym 解析静默返回 None, 该月 PDF
        被当时按文件名反解 ym 的链接筛选悄悄丢弃 (不进 confirmed_gaps, 看起来像
        "文档没有这个月")。该筛选现已换成 classify_pdf_links; 本函数仍用于 wayback
        缺口粗筛与本地缓存文件名解析, 所以这条回归继续保留。"""
        assert _parse_ym_from_text("AccumulateReport_Sept_2025.pdf") == "2025-09"
        assert _parse_ym_from_text("Sept 2025") == "2025-09"
        assert _parse_ym_from_text("Accumulate_Sept25.pdf") == "2025-09"

    def test_glued_month_abbr_4digit_year_no_separator(self):
        """月份缩写与 4 位年之间无分隔符也能认 (dateutil fuzzy 容忍)."""
        assert _parse_ym_from_text("Jan2025.pdf") == "2025-01"

    def test_full_month_name_underscore_separated(self):
        assert _parse_ym_from_text("AccumulateReport_November_2025.pdf") == "2025-11"

    def test_noise_word_with_bare_2digit_number_not_misread_as_month(self):
        """回归: dateutil fuzzy 模式碰到非月份词 + 裸2位数字, 不能瞎猜成"1月"
        (report26.pdf 这类 -- "report" 不是月份名, 26 不该被当年份用)。"""
        assert _parse_ym_from_text("weekly_report26.pdf") is None

    def test_hash_like_filename_segment_no_false_positive(self):
        assert _parse_ym_from_text("blte98f61d722cde430.pdf") is None

    def test_bare_ambiguous_digits_no_month_word_rejected(self):
        """纯数字无字母语境 (无法判断哪个是月哪个是年) -- 拒, 不瞎猜。"""
        assert _parse_ym_from_text("1 2025") is None
        assert _parse_ym_from_text("2025 1") is None

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
        assert probe_l2_wayback("example.com", set(), "Some Fund") == []

    def test_no_domain_returns_empty(self):
        assert probe_l2_wayback("", {"2025-01"}, "Some Fund") == []


# ---------- parse_archive_page + run_discovery: 用 monkeypatch 打桩 ----------

class TestExtractAllPdfLinks:
    def test_lists_every_pdf_href_absolute_and_deduped(self):
        html = (
            '<a href="/docs/a.pdf">A</a>'
            '<a href="https://cdn.x/b.pdf?v=2">B</a>'
            '<a href="/docs/a.pdf">A again</a>'
            '<a href="/about">not pdf</a>'
        )
        assert extract_all_pdf_links(html, "https://issuer.com/archive") == [
            "https://issuer.com/docs/a.pdf",
            "https://cdn.x/b.pdf?v=2",
        ]

    def test_no_filtering_pds_and_sibling_funds_still_listed(self):
        """本函数只负责如实列出, 判归属是 classify_pdf_links 的事 -- 这里若先筛,
        判断就又散成两处 (打地鼠的根源)."""
        html = ('<a href="/Stake_Accumulate_Fund_PDS.pdf">PDS</a>'
                '<a href="/yarra-australian-equities-30-june-2024.pdf">other fund</a>')
        assert len(extract_all_pdf_links(html, "https://x.com/")) == 2


class TestClassifyPdfLinks:
    """判"哪些链接是本基金月报"唯一的一处逻辑. 覆盖原来散在 7 处的手写文件名
    规则各自的失效模式。"""

    def test_real_stake_page_returns_all_monthlies_not_just_one(self):
        """2026-07 Stake 事故 (核心回归): 同页 PDS/TMD 文件名把基金全称拼全,
        真月报只写简称且部分是驼峰无分隔 -- 旧的 token 打分 + 黑名单组合让
        16 份真月报只剩 1 份入库。"""
        pds = "https://cdn/Stake_Accumulate_Fund_PDS_25May26.pdf"
        tmd = "https://cdn/Stake_Accumulate_TMD_25May26.pdf"
        monthlies = {
            "https://cdn/Accumulate report_March2025.pdf": "2025-03",
            "https://cdn/AccumulateMonthly_April25.pdf": "2025-04",
            "https://cdn/AccumulateReport_Sept_2025.pdf": "2025-09",
            "https://cdn/AccumulateReport_Jun26.pdf": "2026-06",
        }
        client = SelectStubClient(lambda u: monthlies.get(u))
        pairs, rejected, dropped = classify_pdf_links(
            [pds, tmd] + list(monthlies), "Stake Accumulate Fund", client=client)

        assert pairs == sorted((ym, u) for u, ym in monthlies.items())
        assert {r["url"] for r in rejected} == {pds, tmd}
        assert dropped == 0

    def test_sibling_fund_file_rejected(self):
        """Yarra 归档页实况: 同页挂兄弟基金月报, 文件名日期解析得出来且不在任何
        黑名单里 -- 旧代码会当本基金月报入库。"""
        target = "https://cdn/yarra-enhanced-income-jun-2026.pdf"
        sibling = "https://cdn/Yarra-Australian-Equities-Fund-30-June-2024.pdf"
        client = SelectStubClient(lambda u: "2026-06" if u == target else None)
        pairs, rejected, _dr = classify_pdf_links(
            [sibling, target], "Yarra Enhanced Income Fund", client=client)
        assert pairs == [("2026-06", target)]
        assert rejected[0]["url"] == sibling

    def test_model_cannot_inject_url_only_index_is_trusted(self):
        """反捏造: 模型回文里夹带 URL 字段一律无视, 只按编号从真实清单取。"""
        real = "https://cdn/Accumulate_June_2025.pdf"
        client = SelectStubClient(
            lambda u: None,
            raw_text='{"reports":[{"i":1,"ym":"2025-06",'
                     '"date_text":"June_2025",'
                     '"url":"https://evil.com/fabricated.pdf"}]}')
        pairs, _rej, _dr = classify_pdf_links([real], "Stake Accumulate", client=client)
        assert pairs == [("2025-06", real)]

    def test_out_of_range_index_and_bad_ym_dropped(self):
        real = "https://cdn/a.pdf"
        client = SelectStubClient(
            lambda u: None,
            raw_text='{"reports":[{"i":9,"ym":"2025-06"},{"i":1,"ym":"2025-13"},'
                     '{"i":1,"ym":"June 2025"}]}')
        pairs, _rej, dropped = classify_pdf_links([real], "F", client=client)
        assert pairs == []
        assert dropped == 3

    def test_empty_url_list_makes_no_call(self):
        client = SelectStubClient(lambda u: "2025-01")
        assert classify_pdf_links([], "F", client=client) == ([], [], 0)
        assert client.prompts == []

    def test_batches_long_history_instead_of_truncating_output(self):
        """20 年历史 240 份月报若一次全塞进去, 输出会撞 max_tokens 被截断 --
        截断即静默丢月份。必须分批。"""
        urls = [f"https://cdn/monthly-{i:03d}.pdf" for i in range(130)]
        client = SelectStubClient(lambda u: "2025-01")
        classify_pdf_links(urls, "F", client=client)
        assert len(client.prompts) == 3  # SELECT_BATCH=60 -> 60+60+10

    def test_retries_once_then_raises_rather_than_silently_losing_months(self):
        client = SelectStubClient(lambda u: "2025-01", raise_times=99)
        with pytest.raises(ClassifyError):
            classify_pdf_links(["https://cdn/a.pdf"], "F", client=client)
        assert len(client.prompts) == 2  # 重试一次后才放弃

    def test_transient_failure_recovers_on_retry(self):
        u = "https://cdn/monthly-Jan2025.pdf"
        client = SelectStubClient(lambda _u: "2025-01", raise_times=1)
        pairs, _rej, _dr = classify_pdf_links([u], "F", client=client)
        assert pairs == [("2025-01", u)]

class TestMonthMustComeFromTheLink:
    """2026-07-29 事故: 链接 ".../ambition-report-2025" 只有年份没有月份, 模型
    仍返回 ym=2025-01 (年份真, 月份 01 是它自行补的默认值)。提示词已明令"读不出
    月份不要猜", 约束不住 -- 代码侧必须能验。"""

    def test_year_only_link_cannot_yield_a_month(self):
        u = "https://hellostake.com/au/ambition-report-2025.pdf"
        client = SelectStubClient(lambda _u: ("2025-01", "2025"))
        pairs, _rej, dropped = classify_pdf_links([u], "stake accumulate",
                                                  client=client)
        assert pairs == [], "只有年份的链接不该产出月份"
        assert dropped == 1

    def test_date_text_not_present_in_link_is_dropped(self):
        """模型说不出原文出处 (date_text 不在链接里) 就不采信 -- 与本项目
        source_quote 逐字校验同一手法。"""
        u = "https://cdn/report-2025.pdf"
        client = SelectStubClient(lambda _u: ("2025-06", "June_2025"))
        pairs, _rej, dropped = classify_pdf_links([u], "F", client=client)
        assert pairs == []
        assert dropped == 1

    def test_date_text_that_parses_to_a_different_month_is_dropped(self):
        u = "https://cdn/AccumulateReport_Sept_2025.pdf"
        client = SelectStubClient(lambda _u: ("2025-10", "Sept_2025"))
        pairs, _rej, dropped = classify_pdf_links([u], "F", client=client)
        assert pairs == []
        assert dropped == 1

    def test_missing_date_text_is_dropped(self):
        client = SelectStubClient(
            lambda _u: None,
            raw_text='{"reports":[{"i":1,"ym":"2025-06"}]}')
        pairs, _rej, dropped = classify_pdf_links(
            ["https://cdn/Accumulate_June_2025.pdf"], "F", client=client)
        assert pairs == []
        assert dropped == 1

    @pytest.mark.parametrize("fname,date_text,ym", [
        ("AccumulateReport_Jun26.pdf", "Jun26", "2026-06"),
        ("Yarra-Fund-30-September-2025.pdf", "30-September-2025", "2025-09"),
        ("AccumulateReport_Sept_2025.pdf", "Sept_2025", "2025-09"),
        ("gci-update-202603.pdf", "202603", "2026-03"),
        ("Accumulate report_March2025.pdf", "March2025", "2025-03"),
        ("AccumulateReport_May26.pdf?branch=odyssey", "May26", "2026-05"),
    ])
    def test_real_filename_formats_all_survive_the_check(self, fname, date_text, ym):
        """兜底校验不能反过来误杀真月报 -- 这几种都是真实归档页上的写法
        (含驼峰/4 字母 Sept/两位年/紧凑数字/带查询串)。"""
        u = f"https://cdn/{fname}"
        client = SelectStubClient(lambda _u: (ym, date_text))
        pairs, _rej, dropped = classify_pdf_links([u], "F", client=client)
        assert pairs == [(ym, u)], f"{fname} 被误杀"
        assert dropped == 0


class TestClassifyPdfLinksMisc:
    def test_requires_fund_name(self):
        with pytest.raises(ValueError):
            classify_pdf_links(["https://cdn/a.pdf"], "", client=object())


class TestParseArchivePage:
    def test_links_past_80kb_cutoff_still_reach_the_model(self):
        """Stake 实况回归: 归档页 149KB, 全部 PDF 链接落在 8.3 万字节之后。旧实现
        把 HTML 截到 80KB 才交给模型, 一个链接都看不见, 恒返 0 links。现在交给
        模型的是代码扫全文抽出的链接清单, 与 HTML 长度无关。"""
        a = "https://cdn.example.com/AccumulateReport_March2025.pdf"
        b = "https://cdn.example.com/Accumulate_April_2025.pdf"
        html = ("<html><body>" + "<!-- filler -->" * 6000
                + f'<a href="{a}">March 2025</a><a href="{b}">April 2025</a>'
                + "</body></html>")
        assert len(html) > 80_000
        client = SelectStubClient(
            lambda u: {a: "2025-03", b: "2025-04"}.get(u))
        links, more, hint, unp = parse_archive_page(
            html, fund_name="Stake Accumulate Fund", client=client,
            base_url="https://issuer.com/archive")
        assert links == [("2025-03", a), ("2025-04", b)]
        assert unp == 0
        assert a in client.prompts[0] and b in client.prompts[0]

    def test_pagination_heuristic(self):
        pdf = "https://cdn.example.com/Accumulate_March_2025.pdf"
        html = f'<a href="{pdf}">March 2025</a><button>Load More</button>'
        client = SelectStubClient(lambda u: "2025-03")
        links, more, hint, _unp = parse_archive_page(
            html, fund_name="F", client=client, base_url="https://issuer.com/a")
        assert links == [("2025-03", pdf)]
        assert more is True
        assert hint == ""

    def test_no_pdf_hrefs_makes_no_llm_call(self):
        client = SelectStubClient(lambda u: "2025-01")
        links, more, _hint, unp = parse_archive_page(
            '<a href="/about">About</a>', fund_name="F", client=client,
            base_url="https://x/")
        assert links == []
        assert client.prompts == []


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

        def fake_probe_l2(domain, gap_set, fund_name, **kwargs):
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


# ---------- _fetch: requests 优先, Playwright 仅内容不完整时才升级 ----------

class TestFetchPriority:
    def test_requests_with_links_skips_playwright(self, monkeypatch):
        """requests 抓到的 HTML 含 <a href> (SSR/静态页) -> 直接用, 不起浏览器."""
        from llm_ingest import discover as disc

        def fake_requests(url, timeout):
            return '<a href="https://x/2025-01.pdf">Jan</a>'

        def fail_playwright(url, timeout):
            raise AssertionError("不该升级到 Playwright -- requests 内容已完整")

        monkeypatch.setattr(disc, "_fetch_requests", fake_requests)
        monkeypatch.setattr(disc, "_fetch_playwright", fail_playwright)
        html = _fetch("https://x/archive")
        assert "2025-01.pdf" in html

    def test_requests_empty_escalates_to_playwright(self, monkeypatch):
        """requests 抓不到内容 (无 <a href>, 典型 SPA 空壳) -> 升级到浏览器渲染."""
        from llm_ingest import discover as disc

        monkeypatch.setattr(disc, "_fetch_requests", lambda url, timeout: "<div id='root'></div>")
        monkeypatch.setattr(disc, "_fetch_playwright",
                            lambda url, timeout: '<a href="https://x/2025-01.pdf">Jan</a>')
        html = _fetch("https://x/spa-archive")
        assert "2025-01.pdf" in html

    def test_requests_none_escalates_to_playwright(self, monkeypatch):
        """requests 直接失败 (None) -> 升级到浏览器渲染."""
        from llm_ingest import discover as disc

        monkeypatch.setattr(disc, "_fetch_requests", lambda url, timeout: None)
        monkeypatch.setattr(disc, "_fetch_playwright",
                            lambda url, timeout: '<a href="https://x/2025-01.pdf">Jan</a>')
        html = _fetch("https://x/archive")
        assert "2025-01.pdf" in html

    def test_requests_has_nav_hrefs_but_no_pdf_escalates_to_playwright(self, monkeypatch):
        """回归 (2026-07): Stake Zendesk 支持页 requests 抓下来有一堆导航 href,
        但 0 个 PDF href (附件走 JS 异步注入) -- 不能因为"有 href 就算完整"而
        跳过 playwright, 得看有没有 .pdf href。"""
        from llm_ingest import discover as disc

        monkeypatch.setattr(
            disc, "_fetch_requests",
            lambda url, timeout: '<a href="/support">Support</a><a href="/legal">Legal</a>',
        )
        monkeypatch.setattr(
            disc, "_fetch_playwright",
            lambda url, timeout: '<a href="https://x/2025-01.pdf">Jan</a>',
        )
        html = _fetch("https://x/support-article")
        assert "2025-01.pdf" in html

    def test_both_fail_returns_none(self, monkeypatch):
        from llm_ingest import discover as disc

        monkeypatch.setattr(disc, "_fetch_requests", lambda url, timeout: None)
        monkeypatch.setattr(disc, "_fetch_playwright", lambda url, timeout: None)
        assert _fetch("https://x/archive") is None


class TestSearchLayerDoesNotYieldPdfLinks:
    """Spec G 10.6: 搜索层只回答"哪一页", PDF 链接只能来自真实抓取的页面 HTML。

    历史漏洞: v1 兜底直接扫搜索结果取第一个 .pdf 当月报, 不抓页不验域名。
    实证 Tavily 搜 GCI 时首位结果是第三方理财顾问站 pricefinancial.com.au
    转贴的 factsheet。
    """

    def test_third_party_pdf_in_search_results_is_not_adopted(self, monkeypatch):
        from llm_ingest import discover as disc

        third_party_pdf = (
            "https://www.pricefinancial.com.au/wp-content/uploads/"
            "2024/05/Gryphon-GCI-Jun-2026.pdf"
        )
        sources = [third_party_pdf, "https://gcapinvest.com/our-lit"]

        monkeypatch.setattr(disc, "multi_query_search", lambda *a, **k: sources)

        # 阶段 B 的 Gemini 判 JSON 返回空 -> 走兜底分支
        class _FakeResp:
            text = "{}"

        class _FakeClient:
            def messages(self, *a, **k):
                return _FakeResp()

        ptr = disc.find_archive_via_search(
            "Gryphon Capital Income Trust", "Gryphon Capital",
            client=_FakeClient(),
        )

        assert ptr.latest_pdf_url != third_party_pdf, (
            "搜索结果里的第三方 PDF 被直接当成月报采纳了 -- "
            "PDF 链接只能来自真实抓取的页面 HTML"
        )


class TestWaybackNarrowing:
    """Spec G 10.2: Wayback 按整个发行商域名抓, 必须筛基金名与文档类型。

    该步专用于补缺口, 而 CLAUDE.md 一.3 对缺口零容忍禁填补 --
    此处却曾用全系统最宽松的条件往缺口里塞东西。
    """

    def _cdx_payload(self, originals):
        import json
        rows = [["timestamp", "original", "statuscode"]]
        for o in originals:
            rows.append(["20260701000000", o, "200"])
        return json.dumps(rows)

    def test_sibling_fund_pdf_not_used_to_fill_gap(self, monkeypatch):
        """probe_l2_wayback 内部走 classify_pdf_links, 不传 client 就会用真
        Client() 打真网络 -- 这里必须打桩, 否则测试结果取决于外部 API 是否可达
        (2026-07-29 发现: 合并后曾偶发超时/失败)。"""
        from llm_ingest import discover as disc

        target = "https://yarracm.com/docs/yarra-enhanced-income-jun-2026.pdf"
        sibling = "https://yarracm.com/docs/yarra-australian-income-jun-2026.pdf"
        monkeypatch.setattr(
            disc, "_curl",
            lambda url, timeout=30: self._cdx_payload([sibling, target]),
        )

        hits = disc.probe_l2_wayback(
            "yarracm.com", {"2026-06"}, "Yarra Enhanced Income Fund",
            client=SelectStubClient(
                lambda u: ("2026-06", "jun-2026") if u == target else None),
        )
        urls = [u for _ym, u in hits]

        assert any(target in u for u in urls), "目标基金的 PDF 应当被采纳"
        assert not any(sibling in u for u in urls), (
            "兄弟基金 Yarra Australian Income 的 PDF 被用来填缺口了"
        )

    def test_pds_tmd_not_used_to_fill_gap(self, monkeypatch):
        from llm_ingest import discover as disc

        pds = "https://yarracm.com/docs/yarra-enhanced-income-PDS-jun-2026.pdf"
        monkeypatch.setattr(
            disc, "_curl", lambda url, timeout=30: self._cdx_payload([pds]),
        )

        hits = disc.probe_l2_wayback(
            "yarracm.com", {"2026-06"}, "Yarra Enhanced Income Fund",
            client=SelectStubClient(lambda u: None),
        )

        assert hits == [], "PDS 不是月度业绩报告, 不得用来填缺口"


class TestEngineThreading:
    """Spec G 4.6: engine 参数逐层透传, 默认 tavily 保证既有行为不变。"""

    def test_run_discovery_passes_engine_to_v2(self, monkeypatch):
        from llm_ingest import discover as disc
        seen = {}

        def _fake_v2(fund_name, issuer, issuer_domain=None, asx_code=None,
                     *, client=None, top_n=4, engine="tavily"):
            seen["engine"] = engine
            return disc.ArchivePointer(
                archive_url=None, pagination_param=None, no_archive=True,
                latest_pdf_url=None, issuer_domain_confirmed=None,
                evidence="", raw={},
            )

        from llm_ingest import discover2 as d2
        monkeypatch.setattr(d2, "find_archive_v2", _fake_v2)
        monkeypatch.setattr(
            disc, "find_archive_via_search",
            lambda *a, **k: disc.ArchivePointer(
                archive_url=None, pagination_param=None, no_archive=True,
                latest_pdf_url=None, issuer_domain_confirmed=None,
                evidence="", raw={}))

        disc.run_discovery("F", "I", "fid", engine="grok", client=object())
        assert seen["engine"] == "grok"

    def test_run_discovery_default_engine_is_tavily(self, monkeypatch):
        from llm_ingest import discover as disc
        from llm_ingest import discover2 as d2
        seen = {}

        def _fake_v2(fund_name, issuer, issuer_domain=None, asx_code=None,
                     *, client=None, top_n=4, engine="tavily"):
            seen["engine"] = engine
            return disc.ArchivePointer(
                archive_url=None, pagination_param=None, no_archive=True,
                latest_pdf_url=None, issuer_domain_confirmed=None,
                evidence="", raw={})

        monkeypatch.setattr(d2, "find_archive_v2", _fake_v2)
        monkeypatch.setattr(
            disc, "find_archive_via_search",
            lambda *a, **k: disc.ArchivePointer(
                archive_url=None, pagination_param=None, no_archive=True,
                latest_pdf_url=None, issuer_domain_confirmed=None,
                evidence="", raw={}))
        disc.run_discovery("F", "I", "fid", client=object())
        assert seen["engine"] == "tavily"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
