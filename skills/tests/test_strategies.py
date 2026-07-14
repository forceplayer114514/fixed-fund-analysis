"""skills/lib/strategies.py 单元测试(集合差驱动 fallback 链)。

mock probe 测 run_discovery 调度逻辑:gap 空早停、缺口入 gaps(非失败)、
下界单向收敛(★重点1)、pending_input 挂起、L2 CDX 单月快照上限(补1)、
per_level_contribution、obtained 空集 -> human_intervention。

真实 probe 的网络部分用 mock _curl 测(probe_wayback_cdx)。
"""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from lib.strategies import (
    CDX_SNAPSHOTS_PER_MONTH,
    LEVELS,
    STRATEGY_LIST,
    DiscoveryReport,
    ProbeResult,
    probe_wayback_cdx,
    run_discovery,
)


def _pr(**kw) -> ProbeResult:
    """构造 ProbeResult(new_months 默认空 set)。"""
    kw.setdefault("new_months", set())
    kw.setdefault("failures", 0)
    return ProbeResult(**kw)


# ---- 调度逻辑(mock probe)----

def test_full_gap_empty_early_stop():
    """L0 补满 expected,gap 空 -> L1/L2/L3 skipped(gap_empty)。"""
    fund_info = {"fund_id": "f1", "inception_date": "2023-01-31",
                 "latest_month": "2023-03-31"}
    probes = {
        "local_cache": lambda fi, g: _pr(new_months={(2023, 1), (2023, 2), (2023, 3)},
                                         evidence="DB full"),
        "official_evergreen": lambda fi, g: _pr(evidence="不应被调"),
        "wayback_cdx": lambda fi, g: _pr(evidence="不应被调"),
        "fundmonitors": lambda fi, g: _pr(evidence="不应被调"),
    }
    report = run_discovery(fund_info, probes=probes)
    assert report.obtained == ["2023-01", "2023-02", "2023-03"]
    assert report.gaps == []
    assert report.human_intervention_needed is False
    assert report.per_level_contribution == {"L0": 3}
    skipped = [e for e in report.evidence_log if not e.get("tried")]
    assert len(skipped) == 3
    assert all(e["skip_reason"] == "gap_empty" for e in skipped)


def test_partial_gaps_recorded_not_human():
    """部分月,穷尽后 gaps 非空,非 human(缺口非失败,修正3.2.4)。"""
    fund_info = {"fund_id": "f1", "inception_date": "2023-01-31",
                 "latest_month": "2023-06-30"}
    probes = {
        "local_cache": lambda fi, g: _pr(
            new_months={(2023, 1), (2023, 2), (2023, 4), (2023, 5), (2023, 6)},
            evidence="DB 5月缺3月"),
        "official_evergreen": lambda fi, g: _pr(failures=1, evidence="无归档页"),
        "wayback_cdx": lambda fi, g: _pr(failures=1, evidence="CDX 无"),
        "fundmonitors": lambda fi, g: _pr(failures=1, evidence="付费墙"),
    }
    report = run_discovery(fund_info, probes=probes)
    assert set(report.obtained) == {"2023-01", "2023-02", "2023-04", "2023-05", "2023-06"}
    assert report.gaps == ["2023-03"]
    assert report.human_intervention_needed is False
    assert report.per_level_contribution["L0"] == 5
    assert report.per_level_contribution["L1"] == 0


def test_none_obtained_human_intervention():
    """全无贡献 -> obtained 空集 -> human_intervention(仅空集 True)。"""
    fund_info = {"fund_id": "f1", "inception_date": "2023-01-31",
                 "latest_month": "2023-03-31"}
    probes = {
        "local_cache": lambda fi, g: _pr(failures=1, evidence="DB 空"),
        "official_evergreen": lambda fi, g: _pr(failures=1, evidence="无"),
        "wayback_cdx": lambda fi, g: _pr(failures=1, evidence="无"),
        "fundmonitors": lambda fi, g: _pr(failures=1, evidence="无"),
    }
    report = run_discovery(fund_info, probes=probes)
    assert report.obtained == []
    assert report.gaps == ["2023-01", "2023-02", "2023-03"]
    assert report.human_intervention_needed is True


def test_lower_bound_convergence_exposes_earlier_gap():
    """★重点1:L1 发现更早月 -> 下界前移 -> expected 扩展 -> 新暴露早期未得月入 gaps。"""
    fund_info = {"fund_id": "f1", "inception_assumed": True,
                 "latest_month": "2023-06-30"}
    probes = {
        "local_cache": lambda fi, g: _pr(new_months={(2023, 4), (2023, 6)},
                                         evidence="DB 4,6月(缺5月)"),
        "official_evergreen": lambda fi, g: _pr(
            new_months={(2023, 1), (2023, 2), (2023, 5)},
            evidence="归档页 1,2,5月(更早+补5月)"),
        "wayback_cdx": lambda fi, g: _pr(failures=1),
        "fundmonitors": lambda fi, g: _pr(failures=1),
    }
    report = run_discovery(fund_info, probes=probes)
    # L0: lower=04, expected=04..06, obtained={04,06}, gap={05}
    # L1: new={01,02,05}, obtained={01,02,04,05,06}, new_min=01<04 -> lower=01,
    #     expected=01..06, gap=01..06-{01,02,04,05,06}={03}
    assert report.gaps == ["2023-03"]
    assert report.human_intervention_needed is False
    assert report.per_level_contribution["L0"] == 2
    assert report.per_level_contribution["L1"] == 3
    assert report.obtained == ["2023-01", "2023-02", "2023-04", "2023-05", "2023-06"]


def test_lower_bound_never_shrinks():
    """★重点1:下界只单向向更早收敛,禁后缩(后缩=静默删缺口)。
    后续 probe 返回的最早月晚于当前下界时,下界不后移。"""
    fund_info = {"fund_id": "f1", "inception_assumed": True,
                 "latest_month": "2023-06-30"}
    probes = {
        "local_cache": lambda fi, g: _pr(new_months={(2023, 1), (2023, 6)},
                                         evidence="DB 1,6月(下界01)"),
        # L1 返回最早月(03)晚于下界(01),不应让下界后移到 03
        "official_evergreen": lambda fi, g: _pr(new_months={(2023, 3), (2023, 5)},
                                                evidence="归档页 3,5月"),
        "wayback_cdx": lambda fi, g: _pr(failures=1),
        "fundmonitors": lambda fi, g: _pr(failures=1),
    }
    report = run_discovery(fund_info, probes=probes)
    # lower=01(L0),L1 new_min=03 不 < 01 -> lower 不变(01)
    # expected=01..06, obtained={01,03,05,06}, gap={02,04}
    assert "2023-01" in report.obtained
    assert report.gaps == ["2023-02", "2023-04"]


def test_pending_input_suspended():
    """L1 缺输入 -> pending_input 挂起(断点续跑),不 crash。"""
    fund_info = {"fund_id": "f1", "inception_date": "2023-01-31",
                 "latest_month": "2023-03-31"}
    probes = {
        "local_cache": lambda fi, g: _pr(failures=1, evidence="DB 空"),
        "official_evergreen": lambda fi, g: _pr(
            pending_input={"need": "confirmed_url", "reason": "需归档页URL"},
            evidence="缺输入"),
        "wayback_cdx": lambda fi, g: _pr(failures=1),
        "fundmonitors": lambda fi, g: _pr(failures=1),
    }
    report = run_discovery(fund_info, probes=probes)
    assert report.pending_input == {"need": "confirmed_url", "reason": "需归档页URL"}
    assert report.obtained == []
    assert report.human_intervention_needed is True


def test_inception_assumed_defers_lower_bound():
    """inception_assumed=True 时不信 inception_date 作下界,待 probe 确定最早月。"""
    fund_info = {"fund_id": "f1", "inception_date": "2023-05-31",
                 "inception_assumed": True, "latest_month": "2023-06-30"}
    probes = {
        "local_cache": lambda fi, g: _pr(new_months={(2023, 2), (2023, 6)},
                                         evidence="DB 2,6月(早于inception)"),
        "official_evergreen": lambda fi, g: _pr(failures=1),
        "wayback_cdx": lambda fi, g: _pr(failures=1),
        "fundmonitors": lambda fi, g: _pr(failures=1),
    }
    report = run_discovery(fund_info, probes=probes)
    # assumed -> lower 初始 None,L0 设 lower=02(早于 inception 05)
    # expected=02..06, obtained={02,06}, gap={03,04,05}
    assert report.obtained == ["2023-02", "2023-06"]
    assert report.gaps == ["2023-03", "2023-04", "2023-05"]
    assert report.inception_assumed is True


def test_strategies_tried_exact_not_len():
    """防弱化:断言精确级别列表(非 len>=1)。"""
    fund_info = {"fund_id": "f1", "inception_date": "2023-01-31",
                 "latest_month": "2023-03-31"}
    probes = {name: (lambda fi, g: _pr(failures=1, evidence="无")) for name in STRATEGY_LIST}
    report = run_discovery(fund_info, probes=probes)
    tried = [e["strategy"] for e in report.evidence_log if e.get("tried")]
    assert tried == [name for _, name in LEVELS]
    assert set(tried) == set(STRATEGY_LIST)


def test_no_target_threshold_early_stop_on_gap_empty():
    """修正3.2.1:无 target=36 阈值,早停只靠 gap 空。3 月(远<36)补满即早停。"""
    fund_info = {"fund_id": "f1", "inception_date": "2023-01-31",
                 "latest_month": "2023-03-31"}
    probes = {
        "local_cache": lambda fi, g: _pr(new_months={(2023, 1), (2023, 2), (2023, 3)},
                                         evidence="3月全"),
        "official_evergreen": lambda fi, g: _pr(evidence="不应被调"),
        "wayback_cdx": lambda fi, g: _pr(evidence="不应被调"),
        "fundmonitors": lambda fi, g: _pr(evidence="不应被调"),
    }
    report = run_discovery(fund_info, probes=probes)
    assert report.obtained == ["2023-01", "2023-02", "2023-03"]
    assert len([e for e in report.evidence_log if e.get("tried")]) == 1


def test_unparseable_collected_to_report():
    """★重点2:probe 返回的 unparseable 累计到 DiscoveryReport,不静默消失。"""
    fund_info = {"fund_id": "f1", "inception_date": "2023-01-31",
                 "latest_month": "2023-02-28"}
    probes = {
        "local_cache": lambda fi, g: _pr(failures=1, evidence="DB 空"),
        "official_evergreen": lambda fi, g: _pr(
            new_months={(2023, 1)},
            unparseable=[{"url": "https://x/no-date.pdf", "raw_text": "Mystery",
                          "reason": "month_not_parsed"}],
            evidence="归档页1月+1个解析失败"),
        "wayback_cdx": lambda fi, g: _pr(new_months={(2023, 2)}, evidence="CDX 补2月"),
        "fundmonitors": lambda fi, g: _pr(evidence="不应被调(gap空)"),
    }
    report = run_discovery(fund_info, probes=probes)
    assert len(report.unparseable_links) == 1
    assert report.unparseable_links[0]["url"] == "https://x/no-date.pdf"


def test_structured_output_fields():
    """DiscoveryReport 含全部新字段(修正3.2.5),已删字段不存在。"""
    fund_info = {"fund_id": "f1", "inception_date": "2023-01-31",
                 "latest_month": "2023-01-31"}
    probes = {name: (lambda fi, g: _pr(failures=1)) for name in STRATEGY_LIST}
    report = run_discovery(fund_info, probes=probes)
    d = asdict(report)
    for f in ["fund_id", "inception_date", "inception_assumed", "obtained",
              "gaps", "per_level_contribution", "per_level_payload",
              "unparseable_links", "pending_input", "human_intervention_needed",
              "evidence_log"]:
        assert f in d, f"缺少字段 {f}"
    # 已删字段不存在(修正3.2.5)
    for gone in ["coverage", "exhausted", "premature_exit", "total_months_obtainable",
                 "strategies_tried", "strategies_skipped", "best_strategy"]:
        assert gone not in d, f"已删字段 {gone} 不应存在"


# ---- 真实 probe_wayback_cdx(mock _curl,测补1)----

def test_cdx_fill_gap_from_snapshot_url(monkeypatch):
    """L2 从快照 original URL 提月份补 gap_set 中洞(不在 gap_set 的不补)。"""
    def fake_curl(url, timeout=60):
        if "cdx/search" in url:
            return json.dumps([
                ["timestamp", "original", "statuscode"],
                ["20230115", "https://x.com/perf-2023-01.pdf", "200"],
                ["20230215", "https://x.com/perf-2023-02.pdf", "200"],
            ])
        return None
    monkeypatch.setattr("lib.strategies._curl", fake_curl)
    result = probe_wayback_cdx({"issuer_domain": "x.com"}, {(2023, 1), (2023, 3)})
    assert (2023, 1) in result.new_months      # 在 gap_set 且有快照
    assert (2023, 2) not in result.new_months  # 有快照但不在 gap_set,不补
    assert (2023, 3) not in result.new_months  # 在 gap_set 但无快照


def test_cdx_snapshot_cap_per_month(monkeypatch):
    """补1:每缺失月至多 CDX_SNAPSHOTS_PER_MONTH 个快照被计入(防海量快照开销)。"""
    rows = [["timestamp", "original", "statuscode"]]
    for i in range(10):
        rows.append([f"202301{i:02d}", "https://x.com/perf-2023-01.pdf", "200"])
    def fake_curl(url, timeout=60):
        return json.dumps(rows) if "cdx/search" in url else None
    monkeypatch.setattr("lib.strategies._curl", fake_curl)
    result = probe_wayback_cdx({"issuer_domain": "x.com"}, {(2023, 1)})
    assert result.new_months == {(2023, 1)}
    assert CDX_SNAPSHOTS_PER_MONTH == 3


def test_cdx_no_snapshot_zero_hit(monkeypatch):
    """CDX 零快照 -> failures=1,new_months 空。"""
    monkeypatch.setattr("lib.strategies._curl", lambda url, timeout=60: None)
    result = probe_wayback_cdx({"issuer_domain": "x.com"}, {(2023, 1)})
    assert result.new_months == set()
    assert result.failures == 1


def test_cdx_missing_domain():
    """无 issuer_domain -> failures=1。"""
    result = probe_wayback_cdx({}, {(2023, 1)})
    assert result.failures == 1
