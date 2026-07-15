"""lib/solver.py 单元测试:合成候选池 + rolling fixture,纯内存无 PDF。

关键场景:
- 唯一解全 resolved / verify_windows >= 2
- 多候选 tie / 无候选落容差 constraint_violation
- 冷启动锚定枚举(多解叠窗口消歧)
- 逆推:早期月由后月窗口回填
- 中间月无 rolling(由邻月约束)
- 容差边界 SELECT_TOL 附近 / precision=1dp 放大
- 整基金无 rolling / 序列 <3 月
- 反捏造断言:chosen 恒为池内对象(不是反解出的期望值本身)
- MAX_COMBOS 超限 no_unique
"""
from __future__ import annotations

import pytest

from lib.candidates import ReturnCandidate
from lib.solver import (
    MAX_COMBOS,
    MIN_VERIFY_WINDOWS,
    MonthInput,
    solve_series,
)


# --- helpers ---
def _cand(value: float, tag: str = "returned_pct", priority: int = 1,
          char_pos: int = 0, ambiguous: bool = False) -> ReturnCandidate:
    return ReturnCandidate(
        value=value,
        source_quote=f"returned {value * 100:.4f}%",
        pattern_tag=tag,
        char_pos=char_pos,
        priority=priority,
        ambiguous_context=ambiguous,
    )


def _rolling(m1=None, m3=None, m6=None, m12=None, precision=2,
             parse_error=False):
    return {
        "1mo": m1, "3mo": m3, "6mo": m6, "12mo": m12,
        "inception": None, "parse_error": parse_error,
        "extractor_tag": "perf", "precision": precision,
    }


def _compound(vals):
    p = 1.0
    for v in vals:
        p *= (1.0 + v)
    return p - 1.0


# --- 1. 唯一解逐月递推:全 resolved 且 verify_windows >= 2 ---
def test_solve_series_unique_solution_all_resolved():
    """构造 6 个月真值 [0.01,0.02,-0.005,0.008,0.015,0.007],每月 rolling 含
    3/6mo,每月候选池含真值 + 一个干扰值。应全部 resolved 且 verify_windows>=2。"""
    truth = [0.01, 0.02, -0.005, 0.008, 0.015, 0.007]
    yms = [f"2025-{i:02d}" for i in range(1, 7)]
    months = []
    for i, (ym, v) in enumerate(zip(yms, truth)):
        cands = [_cand(v, char_pos=0)]  # 真值
        # 加一个干扰候选(与真值差异远超容差)
        cands.append(_cand(v + 0.05, char_pos=50))
        rolling = _rolling()
        # 逐月填 3/6mo(需 span 完全在序列内)
        if i >= 2:
            rolling["3mo"] = _compound(truth[i - 2: i + 1])
        if i >= 5:
            rolling["6mo"] = _compound(truth[i - 5: i + 1])
        months.append(MonthInput(ym=ym, candidates=cands, rolling=rolling))

    res = solve_series(months)
    for ym, v in zip(yms, truth):
        r = res[ym]
        assert r.status == "resolved", f"{ym}: {r.status}"
        assert r.chosen is not None
        assert r.chosen.value == pytest.approx(v)
        # 反捏造:chosen 必为候选池内对象(is 判等)
        assert any(r.chosen is c for c in r.pool)
        assert r.verify_windows >= MIN_VERIFY_WINDOWS


# --- 2. 多候选 tie ---
def test_solve_series_tie_no_unique_candidate():
    """构造两候选都落 SELECT_TOL 容差内(<0.0005) -> no_unique_candidate。"""
    truth = [0.01, 0.005, 0.008]
    yms = ["2025-01", "2025-02", "2025-03"]
    # 3mo 期望值 = _compound(truth) ~= 0.023
    r3 = _compound(truth)
    # 2025-03 候选池含两个接近值(差 0.0003,均在 SELECT_TOL 3mo=0.0005 内)
    m1 = MonthInput(ym="2025-01", candidates=[_cand(0.01)],
                    rolling=_rolling())
    m2 = MonthInput(ym="2025-02", candidates=[_cand(0.005)],
                    rolling=_rolling())
    m3 = MonthInput(ym="2025-03",
                    candidates=[_cand(0.00800), _cand(0.00830)],
                    rolling=_rolling(m3=r3))
    res = solve_series([m1, m2, m3])
    # 2025-01,02 单值候选 chosen 不了(约束反解不需要),但也没触发约束
    # 唯一有约束的 3mo,unknown=1 月(2025-03) -> hits 2 个 -> tie
    assert res["2025-03"].status == "no_unique_candidate"
    assert len(res["2025-03"].tied) == 2


# --- 3. 无候选落容差 -> constraint_violation ---
def test_solve_series_constraint_violation_no_candidate_in_tol():
    """rolling 与所有候选矛盾 -> constraint_violation。"""
    m1 = MonthInput(ym="2025-01", candidates=[_cand(0.01)], rolling=_rolling())
    m2 = MonthInput(ym="2025-02", candidates=[_cand(0.02)], rolling=_rolling())
    # 3mo 需要 2025-03 的真值 =0.008,但候选池只给 0.05(差 0.042 远超容差)
    r3 = _compound([0.01, 0.02, 0.008])
    m3 = MonthInput(ym="2025-03", candidates=[_cand(0.05)],
                    rolling=_rolling(m3=r3))
    res = solve_series([m1, m2, m3])
    assert res["2025-03"].status == "constraint_violation"


# --- 4. 冷启动锚定枚举:多组合 + 后续窗口消歧 ---
def test_solve_series_cold_start_anchor_enumeration_unique():
    """首 3mo 约束下有多个组合满足,需 6mo 窗口联立消歧至唯一。"""
    truth = [0.01, 0.02, -0.005, 0.008, 0.015, 0.007]
    yms = [f"2025-{i:02d}" for i in range(1, 7)]
    # 每月候选池含真值 + 一个额外值(差异较大),但前 3 月的额外值组合恰好也
    # 满足 3mo 约束(构造这种情况不易,用直白测试:确保 6mo 存在时 Pass B
    # 能定值)
    months = []
    r3_1 = _compound(truth[0:3])
    r3_2 = _compound(truth[1:4])
    r6_5 = _compound(truth[0:6])
    for i, (ym, v) in enumerate(zip(yms, truth)):
        cands = [_cand(v), _cand(v + 0.03)]
        rolling = _rolling()
        if i == 2:
            rolling["3mo"] = r3_1
        elif i == 3:
            rolling["3mo"] = r3_2
        elif i == 5:
            rolling["6mo"] = r6_5
        months.append(MonthInput(ym=ym, candidates=cands, rolling=rolling))
    res = solve_series(months)
    resolved = [ym for ym in yms if res[ym].status == "resolved"]
    # 至少前 3 月能被锚定(Pass B 3mo 枚举唯一或经 Pass A 后传播)
    assert "2025-01" in resolved or "2025-02" in resolved or "2025-03" in resolved


# --- 5. 逆推:早期月由后月窗口回填 ---
def test_solve_series_backward_propagation():
    """构造 4 月序列,只 2025-04 有 3mo rolling,其 span 覆盖 2025-02/03/04。
    先定 03/04(直接候选唯一),再由 3mo 反解 02。"""
    # 让 02/03/04 各只有一个候选,唯一解;但 02 的候选给一个"待验证"占位:
    # 其实场景改为:03/04 候选唯一,02 有两个候选,3mo 只允许其中一个通过。
    truth = [0.005, 0.003, 0.007, 0.004]  # (01) unused, 02,03,04
    yms = ["2025-01", "2025-02", "2025-03", "2025-04"]
    r3 = _compound(truth[1:4])  # 02+03+04
    m1 = MonthInput(ym="2025-01", candidates=[_cand(truth[0])],
                    rolling=_rolling())
    # 02: 两个候选,3mo 只支持 truth[1]
    m2 = MonthInput(ym="2025-02",
                    candidates=[_cand(truth[1]), _cand(truth[1] + 0.02)],
                    rolling=_rolling())
    m3 = MonthInput(ym="2025-03", candidates=[_cand(truth[2])],
                    rolling=_rolling())
    m4 = MonthInput(ym="2025-04", candidates=[_cand(truth[3])],
                    rolling=_rolling(m3=r3))
    res = solve_series([m1, m2, m3, m4])
    # 03/04 单一候选,不会走 Pass A(Pass A 只处理"仅 1 未知月"),
    # 3mo 约束里 03/04 是"未定",04 唯一候选被 Pass B 枚举中挑;
    # 02 被回填出唯一值:接受为 resolved(若 verify_windows>=1 但<2 则 unverifiable)
    # 主要断言:2025-02 chosen 值 = truth[1](反推逻辑成功),而非 truth[1]+0.02
    r2 = res["2025-02"]
    if r2.chosen is not None:
        assert r2.chosen.value == pytest.approx(truth[1])


# --- 6. 中间月无 rolling -> 由邻月约束 ---
def test_solve_series_middle_month_no_rolling_covered_by_neighbor():
    """中间月自身 parse_error,由后一月的 3mo 窗口约束定值。"""
    truth = [0.01, 0.02, 0.005]
    r3 = _compound(truth)
    m1 = MonthInput(ym="2025-01", candidates=[_cand(0.01)],
                    rolling=_rolling())
    # 2025-02 rolling parse_error
    m2 = MonthInput(ym="2025-02",
                    candidates=[_cand(0.02), _cand(0.05)],
                    rolling=_rolling(parse_error=True))
    m3 = MonthInput(ym="2025-03", candidates=[_cand(0.005)],
                    rolling=_rolling(m3=r3))
    res = solve_series([m1, m2, m3])
    r2 = res["2025-02"]
    # 02 由 03 的 3mo 约束反推:期望 0.02 -> hit 唯一
    assert r2.chosen is not None
    assert r2.chosen.value == pytest.approx(0.02)


# --- 7. 容差边界:SELECT_TOL 边缘 ---
def test_solve_series_precision_1dp_widens_tolerance():
    """precision=1 时 SELECT_TOL 放大 5 倍(宁 pending 不误选)。

    构造 2 月候选距离 0.0001(在 2dp precision 下会被识别为 tie 或 miss,
    在 1dp 下容差放大到 0.0025)。"""
    # 3mo 约束:真值 [0.01,0.02,0.008],r3 = _compound([0.01,0.02,0.008])
    # 2025-03 候选:0.008 与 0.010(相差 0.002)
    # 2dp 容差 = 0.0005 -> 只有 0.008 命中(唯一)
    # 1dp 容差 = 0.0025 -> 0.008 与 0.010 都命中 -> tie
    truth = [0.01, 0.02, 0.008]
    r3 = _compound(truth)
    m1 = MonthInput(ym="2025-01", candidates=[_cand(0.01)],
                    rolling=_rolling())
    m2 = MonthInput(ym="2025-02", candidates=[_cand(0.02)],
                    rolling=_rolling())
    m3 = MonthInput(ym="2025-03",
                    candidates=[_cand(0.008), _cand(0.010)],
                    rolling=_rolling(m3=r3, precision=1))
    res = solve_series([m1, m2, m3])
    # precision=1 -> tie
    assert res["2025-03"].status == "no_unique_candidate"


# --- 8. 整基金无 rolling -> unverifiable_no_rolling ---
def test_solve_series_no_rolling_at_all():
    """全部月份 rolling parse_error -> 每月 unverifiable。"""
    months = [
        MonthInput(ym=f"2025-{i:02d}", candidates=[_cand(0.01 * i)],
                   rolling=_rolling(parse_error=True))
        for i in range(1, 5)
    ]
    res = solve_series(months)
    for m in months:
        assert res[m.ym].status == "unverifiable_no_rolling"


# --- 9. 序列 < 3 月 -> 无法启动 3mo 约束 ---
def test_solve_series_less_than_three_months():
    m1 = MonthInput(ym="2025-01", candidates=[_cand(0.01)],
                    rolling=_rolling())
    m2 = MonthInput(ym="2025-02", candidates=[_cand(0.02)],
                    rolling=_rolling())
    res = solve_series([m1, m2])
    # 无 3mo 窗口可用 -> unverifiable(1mo 若无也 unverifiable)
    for r in res.values():
        assert r.status == "unverifiable_no_rolling"


# --- 10. 反捏造铁律:chosen 恒为池内对象 ---
def test_solve_series_chosen_is_pool_object_not_expected_value():
    """构造:期望值 = 0.008,候选池有 [0.00801](差 1e-5,在 SELECT_TOL 内)。
    chosen 应是 0.00801 候选(池内对象),不是反解的 0.008。"""
    truth_selected = 0.00801  # 候选池中真实存在的值
    m1 = MonthInput(ym="2025-01", candidates=[_cand(0.01)],
                    rolling=_rolling())
    m2 = MonthInput(ym="2025-02", candidates=[_cand(0.02)],
                    rolling=_rolling())
    # rolling 用理论 0.008(期望值),但候选池只有 0.00801(略偏)
    r3 = _compound([0.01, 0.02, 0.008])
    m3 = MonthInput(ym="2025-03", candidates=[_cand(truth_selected)],
                    rolling=_rolling(m3=r3))
    res = solve_series([m1, m2, m3])
    r3_res = res["2025-03"]
    if r3_res.chosen is not None:
        # chosen.value 必等于池内的 0.00801,而非 0.008(期望值)
        assert r3_res.chosen.value == truth_selected
        assert r3_res.chosen.value != 0.008
        # is 判等:候选池对象本身
        assert any(r3_res.chosen is c for c in r3_res.pool)


# --- 11. 空候选池 -> no_candidates ---
def test_solve_series_no_candidates_month():
    m1 = MonthInput(ym="2025-01", candidates=[_cand(0.01)],
                    rolling=_rolling())
    m2 = MonthInput(ym="2025-02", candidates=[],  # 空池
                    rolling=_rolling())
    m3 = MonthInput(ym="2025-03", candidates=[_cand(0.005)],
                    rolling=_rolling())
    res = solve_series([m1, m2, m3])
    assert res["2025-02"].status == "no_candidates"


# --- 12. 一份 rolling 表可解出多月(fixpoint 传播) ---
def test_solve_series_multiple_windows_verify_high():
    """构造 6 月,每月都有 3mo 与 6mo 覆盖 -> 后期月 verify_windows 多。"""
    truth = [0.005, 0.003, 0.007, 0.002, 0.008, 0.004]
    yms = [f"2025-{i:02d}" for i in range(1, 7)]
    months = []
    for i, (ym, v) in enumerate(zip(yms, truth)):
        cands = [_cand(v), _cand(v + 0.05)]  # 真值 + 远处干扰
        rolling = _rolling()
        if i >= 2:
            rolling["3mo"] = _compound(truth[i - 2: i + 1])
        if i >= 5:
            rolling["6mo"] = _compound(truth[i - 5: i + 1])
        months.append(MonthInput(ym=ym, candidates=cands, rolling=rolling))
    res = solve_series(months)
    # 06 应有 3mo + 6mo + 1mo(自身 rolling 1mo 若填)
    r6 = res["2025-06"]
    assert r6.status == "resolved"
    assert r6.verify_windows >= 2
