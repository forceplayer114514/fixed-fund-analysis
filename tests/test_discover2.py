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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
