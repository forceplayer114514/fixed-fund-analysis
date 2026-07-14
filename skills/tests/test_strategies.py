"""PDD 测试：候选策略遍历器 run_discovery。

3+1 夹具覆盖通用性（防 hardcode 单基金分支）：
- A: featured + 官网 evergreen PDF full coverage（早停验证）
- B: non-featured + 官网 evergreen 无逐月表（Coolabah FRHY 类，穷尽+人工介入）
- C: 仅 PDS/年报边缘案例（穷尽+人工介入，无解也通过）
- D: partial（某策略给 20 月 <36，其余无，验证 partial 不过早退出）

断言重点（用户要求）：遍历完整性（精确列表非 len>=1）、结构化输出、
案例 C 无解也通过、不过早退出、不弱化断言。
"""
from __future__ import annotations

from dataclasses import asdict

import pytest

from lib.strategies import (
    STRATEGY_LIST,
    StrategyResult,
    run_discovery,
)


def _r(strategy: str, **kw) -> StrategyResult:
    """构造 tried=True 的 StrategyResult（probe 主动探测默认 tried=True）。"""
    kw.setdefault("tried", True)
    return StrategyResult(strategy=strategy, **kw)


# ---- 夹具 A: featured + 官网 PDF full coverage（早停）----
FUND_A = {"fund_id": "lscf", "target_months": 36}
PROBES_A = {
    "local_cache": lambda fi: _r("local_cache", found=False, evidence="DB 无该基金数据"),
    "official_evergreen": lambda fi: _r(
        "official_evergreen", found=True, months_count=40,
        evidence="PDF 含 Year×Month 逐月历史表",
        ingest_entry="add", fetch_method="pdf",
        confirmed_url="https://smartermoney.com.au/lscf.pdf",
    ),
    "fundmonitors": lambda fi: _r("fundmonitors", found=True, months_count=36, evidence="不应被调（早停）"),
    "wayback_cdx": lambda fi: _r("wayback_cdx", found=True, months_count=36, evidence="不应被调（早停）"),
    "distributions": lambda fi: _r("distributions", found=False, evidence="不应被调（早停）"),
    "third_party_rolling": lambda fi: _r("third_party_rolling", found=False, evidence="不应被调（早停）"),
}

# ---- 夹具 B: non-featured + 官网 evergreen 无逐月表（Coolabah FRHY 类）----
FUND_B = {"fund_id": "frhy", "target_months": 36,
          "issuer_domain": "coolabahcapital.com",
          "wayback_slugs": ["coolabah-floating-rate-high-yield-fund-ai"]}
PROBES_B = {
    "local_cache": lambda fi: _r("local_cache", found=False, evidence="DB 无该基金数据"),
    "official_evergreen": lambda fi: _r(
        "official_evergreen", found=False,
        evidence="PDF 仅滚动收益无逐月表（搜 Monthly/History/Jan-Dec 未命中）",
    ),
    "fundmonitors": lambda fi: _r(
        "fundmonitors", found=False, paywall_hit=True,
        evidence="fundmonitors 付费墙/登录墙（non-featured），立即跳过不提供账号",
    ),
    "wayback_cdx": lambda fi: _r(
        "wayback_cdx", found=False,
        evidence="CDX 零快照（查了 6 个 URL 模式：归档页+slug变体+wp-content）",
    ),
    "distributions": lambda fi: _r(
        "distributions", found=False,
        evidence="Distributions 仅分红历史，非月度 total return，不可作主数据源",
    ),
    "third_party_rolling": lambda fi: _r(
        "third_party_rolling", found=False,
        evidence="第三方滚动收益仅区间值非逐月，且多为付费墙，跳过作主源",
    ),
}

# ---- 夹具 C: 仅 PDS/年报边缘案例（全无）----
FUND_C = {"fund_id": "edge_fund", "target_months": 36}
PROBES_C = {
    "local_cache": lambda fi: _r("local_cache", found=False, evidence="DB 无该基金数据"),
    "official_evergreen": lambda fi: _r(
        "official_evergreen", found=False,
        evidence="官网仅 PDS/年报，无月度业绩 PDF",
    ),
    "fundmonitors": lambda fi: _r(
        "fundmonitors", found=False, paywall_hit=True,
        evidence="fundmonitors 无该基金收录",
    ),
    "wayback_cdx": lambda fi: _r(
        "wayback_cdx", found=False,
        evidence="CDX 零快照（官网无月报可归档）",
    ),
    "distributions": lambda fi: _r(
        "distributions", found=False,
        evidence="无 Distributions 页",
    ),
    "third_party_rolling": lambda fi: _r(
        "third_party_rolling", found=False,
        evidence="第三方无收录",
    ),
}

# ---- 夹具 D: partial（20 月 <36，验证 partial 不过早退出）----
FUND_D = {"fund_id": "partial_fund", "target_months": 36}
PROBES_D = {
    "local_cache": lambda fi: _r("local_cache", found=False, evidence="DB 无该基金数据"),
    "official_evergreen": lambda fi: _r(
        "official_evergreen", found=True, months_count=20,
        evidence="官网 PDF 仅含近 20 月逐月表",
        ingest_entry="add", fetch_method="pdf",
        confirmed_url="https://example.com/partial.pdf",
    ),
    "fundmonitors": lambda fi: _r(
        "fundmonitors", found=False, paywall_hit=True, evidence="付费墙",
    ),
    "wayback_cdx": lambda fi: _r(
        "wayback_cdx", found=False, evidence="CDX 零快照",
    ),
    "distributions": lambda fi: _r(
        "distributions", found=False, evidence="仅分红非 total return",
    ),
    "third_party_rolling": lambda fi: _r(
        "third_party_rolling", found=False, evidence="无免费源",
    ),
}


@pytest.mark.unit
@pytest.mark.parametrize(
    "fund_info,probes,expected",
    [
        pytest.param(FUND_A, PROBES_A, {
            "id": "A_featured_full",
            "coverage": "full",
            "exhausted": True,
            "premature_exit": False,
            "best_strategy": "official_evergreen",
            "total_months": 40,
            "strategies_tried": ["local_cache", "official_evergreen"],
            "strategies_skipped": ["fundmonitors", "wayback_cdx", "distributions", "third_party_rolling"],
            "human_intervention": False,
        }, id="A_featured_full"),
        pytest.param(FUND_B, PROBES_B, {
            "id": "B_nonfeatured_none",
            "coverage": "none",
            "exhausted": True,
            "premature_exit": False,
            "best_strategy": None,
            "total_months": 0,
            "strategies_tried": list(STRATEGY_LIST),
            "strategies_skipped": [],
            "human_intervention": True,
        }, id="B_nonfeatured_none"),
        pytest.param(FUND_C, PROBES_C, {
            "id": "C_pds_only_none",
            "coverage": "none",
            "exhausted": True,
            "premature_exit": False,
            "best_strategy": None,
            "total_months": 0,
            "strategies_tried": list(STRATEGY_LIST),
            "strategies_skipped": [],
            "human_intervention": True,
        }, id="C_pds_only_none"),
        pytest.param(FUND_D, PROBES_D, {
            "id": "D_partial",
            "coverage": "partial",
            "exhausted": True,
            "premature_exit": False,
            "best_strategy": "official_evergreen",
            "total_months": 20,
            "strategies_tried": list(STRATEGY_LIST),
            "strategies_skipped": [],
            "human_intervention": False,
        }, id="D_partial"),
    ],
)
def test_run_discovery(fund_info, probes, expected):
    report = run_discovery(fund_info, probes=probes)
    assert report.coverage == expected["coverage"]
    assert report.exhausted == expected["exhausted"]
    assert report.premature_exit == expected["premature_exit"]
    assert report.best_strategy == expected["best_strategy"]
    assert report.total_months_obtainable == expected["total_months"]
    assert report.human_intervention_needed == expected["human_intervention"]
    # 精确列表（非 len>=1，防弱化断言）
    assert report.strategies_tried == expected["strategies_tried"]
    assert report.strategies_skipped == expected["strategies_skipped"]


@pytest.mark.unit
@pytest.mark.parametrize("fund_info,probes", [
    (FUND_B, PROBES_B),
    (FUND_C, PROBES_C),
    (FUND_D, PROBES_D),
], ids=["B", "C", "D"])
def test_no_premature_exit_partial_or_none(fund_info, probes):
    """partial/none 必须遍历完整个 STRATEGY_LIST，否则 premature_exit=bug。"""
    report = run_discovery(fund_info, probes=probes)
    assert report.premature_exit is False
    assert report.exhausted is True
    # 精确全集（顺序敏感）
    assert report.strategies_tried == list(STRATEGY_LIST)
    assert report.strategies_skipped == []


@pytest.mark.unit
def test_case_c_unsolvable_passes():
    """案例 C 确认真无解，测试应通过--只要 agent 穷尽所有路径并如实标注。"""
    report = run_discovery(FUND_C, probes=PROBES_C)
    assert report.coverage == "none"
    assert report.exhausted is True
    assert report.premature_exit is False
    assert report.human_intervention_needed is True
    assert report.total_months_obtainable == 0


@pytest.mark.unit
def test_early_stop_marks_skip_reason():
    """满 coverage 早停：剩余策略 tried=False skip_reason=full_coverage。"""
    report = run_discovery(FUND_A, probes=PROBES_A)
    assert report.coverage == "full"
    skipped = {ev["strategy"]: ev for ev in report.evidence_log if ev["tried"] is False}
    assert set(skipped) == {"fundmonitors", "wayback_cdx", "distributions", "third_party_rolling"}
    for ev in skipped.values():
        assert ev["skip_reason"] == "full_coverage"


@pytest.mark.unit
def test_structured_output_fields():
    """DiscoveryReport 含全部结构化字段（部分成功+缺口格式）。"""
    report = run_discovery(FUND_D, probes=PROBES_D)
    d = asdict(report)
    for f in ["strategies_tried", "strategies_succeeded", "strategies_skipped",
              "best_strategy", "total_months_obtainable", "gaps", "coverage",
              "exhausted", "premature_exit", "human_intervention_needed",
              "evidence_log"]:
        assert f in d, f"缺少字段 {f}"
    # evidence_log 每项含 strategy+tried+found+evidence
    for ev in report.evidence_log:
        for f in ["strategy", "tried", "found", "evidence"]:
            assert f in ev


@pytest.mark.unit
def test_best_strategy_is_first_found():
    """best_strategy 是首个 found=True 的策略（优先级最高）。"""
    report = run_discovery(FUND_A, probes=PROBES_A)
    assert report.best_strategy == "official_evergreen"
    assert report.strategies_succeeded == ["official_evergreen"]


@pytest.mark.unit
def test_strategies_tried_exact_not_len():
    """防弱化：断言精确列表，非 len>=1。"""
    report = run_discovery(FUND_B, probes=PROBES_B)
    # 精确全集 + 顺序
    assert report.strategies_tried == [
        "local_cache", "official_evergreen", "fundmonitors",
        "wayback_cdx", "distributions", "third_party_rolling",
    ]
    # 不允许只断言 len
    assert len(report.strategies_tried) == len(STRATEGY_LIST)
    assert set(report.strategies_tried) == set(STRATEGY_LIST)


# ---- probe_official_evergreen 真实函数测试（单月 Commentary 检测，2026-07-14）----
# 回测：Bentham GIF 单月 PDF 无 Year×Month 表但有 Commentary 当月收益，旧逻辑
# 仅判 _pdf_has_monthly_table -> found=False，浪费 9 分钟找逐月表才转单月合成。
# 新逻辑：无逐月表时调 extract_commentary_return，命中即 found=True（多 PDF 合成）。

@pytest.mark.unit
def test_probe_official_evergreen_single_month_commentary(tmp_path, monkeypatch):
    """单月 Commentary PDF（无 Year×Month 表）-> found=True, add（多 PDF 合成）。"""
    from lib.strategies import probe_official_evergreen
    pdf = tmp_path / "report.pdf"
    pdf.write_text("fake")  # 存在即可，parse_pdf_text 被 mock
    # 无 Monthly/History/<6 month tokens，但 Commentary 有当月收益
    text = "The fund returned 0.53% in May. On a before fees basis returned 0.59%."
    monkeypatch.setattr("lib.extract.parse_pdf_text", lambda p: text)

    result = probe_official_evergreen({"official_pdf_path": str(pdf)})
    assert result.found is True
    assert result.ingest_entry == "add"
    assert result.fetch_method == "pdf"
    assert "Commentary" in result.evidence or "单月" in result.evidence


@pytest.mark.unit
def test_probe_official_evergreen_year_month_table(tmp_path, monkeypatch):
    """Year×Month 逐月表 PDF -> found=True, add（单 PDF，罕见路径回归锁）。"""
    from lib.strategies import probe_official_evergreen
    pdf = tmp_path / "report.pdf"
    pdf.write_text("fake")
    # 含 Monthly + >=6 month tokens -> _pdf_has_monthly_table 命中
    text = ("Monthly Performance History\n"
            "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec\n"
            "0.5 0.6 0.7 0.8 0.9 1.0 1.1 1.2 1.3 1.4 1.5 1.6\n"
            "Total return (after fees)")
    monkeypatch.setattr("lib.extract.parse_pdf_text", lambda p: text)

    result = probe_official_evergreen({"official_pdf_path": str(pdf)})
    assert result.found is True
    assert result.ingest_entry == "add"
    assert "Year×Month" in result.evidence or "逐月历史表" in result.evidence


@pytest.mark.unit
def test_probe_official_evergreen_neither_table_nor_commentary(tmp_path, monkeypatch):
    """无逐月表且无 Commentary 当月收益（仅滚动收益）-> found=False。"""
    from lib.strategies import probe_official_evergreen
    pdf = tmp_path / "report.pdf"
    pdf.write_text("fake")
    # 无 Monthly/History/month tokens 集合，无 "returned X%"
    text = ("Performance\n1 month 3 months 6 months 12 months since inception\n"
            "Class A 0.51% 1.53% 2.08% 2.08% 2.08%\n")
    monkeypatch.setattr("lib.extract.parse_pdf_text", lambda p: text)

    result = probe_official_evergreen({"official_pdf_path": str(pdf)})
    assert result.found is False
    assert "无 Commentary" in result.evidence or "均未命中" in result.evidence


@pytest.mark.unit
def test_probe_official_evergreen_no_url_no_path():
    """fund_info 无 official_pdf_url/path -> found=False，提示主会话回填。"""
    from lib.strategies import probe_official_evergreen
    result = probe_official_evergreen({})
    assert result.found is False
    assert "official_pdf_url" in result.evidence or "回填" in result.evidence
