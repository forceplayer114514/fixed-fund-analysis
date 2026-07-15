"""滚动收益约束求解器:用 PDF 系列自带的 3/6/12mo 滚动值,自动从候选池选定每月真值。

设计动机:清洗层第一性原理 -- PDF 每份月报的 performance 表滚动收益是天然的
校验钥匙,正确的月度序列复利必与滚动值吻合。这是纯数学约束,不需要 LLM 或
每个发行商专属正则试错。

核心承诺(与 skills/CLAUDE.md / lib/candidates.py 一致):
- 求解器只"选择"候选,不"生成"数值。MonthResolution.chosen 永远 is 池内对象;
  反解期望值仅用于比对选择,绝不写库(反捏造铁律)。
- 决定 resolved 需 >=2 个独立滚动窗口 (n, end_ym) 通过 (VERIFY_TOL=0.005);
  这是 A4 数学准入闸门(用户 2026-07-15 拍板)。
- 无法数学验证 / 无候选 / tie 未消 / 约束矛盾 -> pending_review,不静默入库。

算法:
- Pass A 约束传播 (fixpoint):窗口内仅剩 1 个未知月 -> 反解期望值,在该月候选
  去重值中找 SELECT_TOL 内命中(1=定,>1=tie,0=miss)。天然处理逆推(早期月由
  后月窗口回填)和中间月无滚动表(由邻月窗口约束)。
- Pass B 冷启动锚定枚举:传播卡死时,取最早的窗口完全在序列内的 3mo 约束,
  对 3 个月的去重候选值做笛卡尔枚举(<=MAX_COMBOS),后续窗口联立过滤至唯一。
  1mo 列永不作单独选择依据(防 Stake 式 1mo 口径错)。
- Pass C 验证窗口计数 + 终判:每月计算通过窗口数 -> 分流 status。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional

from lib.candidates import ReturnCandidate


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 用于反解 + 验证的滚动窗口(不含 inception:通常为年化 p.a.,与月度复利不可比;
# 仅当序列起点=成立月时作为确认窗口叠加)。
WINDOWS: list[tuple[str, int]] = [("3mo", 3), ("6mo", 6), ("12mo", 12)]

# 选择候选时的容差(基于 PDF 2dp 舍入误差累积估计):
# 单值舍入 ±0.005% = 5e-5;n 月窗口累积 ~n*5e-5;取 3x 余量。
# rolling precision=1 时全部 ×5(宁 pending 不误选)。
SELECT_TOL: dict[str, float] = {"3mo": 0.0005, "6mo": 0.00075, "12mo": 0.0015}

# 验证窗口容差 (与现 verify_monthly_vs_rolling 一致 = 0.5%)。
VERIFY_TOL = 0.005

# 自动入库准入:>= MIN_VERIFY_WINDOWS 个独立通过窗口 -> resolved
# (A4 新政:代替 generic_first_use 首批人工审)。
MIN_VERIFY_WINDOWS = 2

# 冷启动锚定枚举组合上限。超限 -> 涉及月标 no_unique_candidate(不硬算)。
MAX_COMBOS = 2000


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class MonthInput:
    ym: str                                  # 'YYYY-MM'
    candidates: list[ReturnCandidate]
    rolling: dict                            # extract_rolling_any 的输出


@dataclass
class WindowCheck:
    window: str        # '3mo' / '6mo' / '12mo' / '1mo' / 'inception'
    end_ym: str        # 窗口结束月
    expected: float    # rolling 表登记的累计收益
    actual: float      # 用选定候选复利算出的累计收益
    error: float
    passed: bool


@dataclass
class MonthResolution:
    ym: str
    status: str                                    # resolved / no_unique_candidate /
                                                   # constraint_violation /
                                                   # unverifiable_no_rolling /
                                                   # no_candidates
    chosen: Optional[ReturnCandidate]              # 池内对象(反捏造铁律)
    verify_windows: int
    windows_detail: list[WindowCheck] = field(default_factory=list)
    tied: list[ReturnCandidate] = field(default_factory=list)
    pool: list[ReturnCandidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _tol(window: str, precision: int) -> float:
    """选择容差,precision=1dp 时 ×5。"""
    base = SELECT_TOL.get(window, 0.001)
    if precision <= 1:
        return base * 5
    return base


def _unique_values(cands: list[ReturnCandidate]) -> list[float]:
    """候选池按 round(value, 6) 去重,返 distinct 值列表。"""
    seen: set = set()
    out: list[float] = []
    for c in cands:
        k = round(c.value, 6)
        if k not in seen:
            seen.add(k)
            out.append(c.value)
    return out


def _pick_candidate_for_value(
    cands: list[ReturnCandidate], value: float
) -> Optional[ReturnCandidate]:
    """从候选池中挑选与 value 相等(round 6 位)的第一个候选。

    priority 越小越优先(展示更权威),同 priority 按 char_pos 稳定;完全匹配失败
    返 None。求解器最终 chosen 必定 is 池内对象,不生成新对象(反捏造)。
    """
    target = round(value, 6)
    matched = [c for c in cands if round(c.value, 6) == target]
    if not matched:
        return None
    matched.sort(key=lambda c: (c.priority, c.char_pos))
    return matched[0]


def _find_hits(
    cands: list[ReturnCandidate], expected: float, tol: float
) -> list[float]:
    """候选池 distinct 值中,与 expected 距离 < tol 的值列表。"""
    hits: list[float] = []
    for v in _unique_values(cands):
        if abs(v - expected) < tol:
            hits.append(v)
    return hits


def _compound(returns: list[float]) -> float:
    """(1+r1)(1+r2)...(1+rn) - 1。"""
    p = 1.0
    for r in returns:
        p *= (1.0 + r)
    return p - 1.0


def _ym_iter(months: list[MonthInput]) -> list[str]:
    return [m.ym for m in months]


# ---------------------------------------------------------------------------
# 约束枚举
# ---------------------------------------------------------------------------

@dataclass
class WindowConstraint:
    """一条 (n, end_ym) 约束:end_ym 及其前 n-1 月的复利 == rolling[n]。"""
    n: int
    window: str
    end_ym: str
    end_idx: int          # end_ym 在 months 序列的索引
    rolling_val: float
    precision: int        # end 月 rolling 的 precision


def _collect_constraints(
    months: list[MonthInput], ym_to_idx: dict[str, int]
) -> list[WindowConstraint]:
    """扫全部 rolling,产完全落在序列内的窗口约束。

    inception 排除(通常年化 p.a., 与月度复利不可比)。1mo 也不作反解约束
    (通过 verify 阶段用作确认窗口)。3/6/12mo 窗口跨月数超过序列范围 -> 跳过。
    """
    out: list[WindowConstraint] = []
    for i, m in enumerate(months):
        if not m.rolling or m.rolling.get("parse_error"):
            continue
        precision = m.rolling.get("precision", 2)
        for tag, n in WINDOWS:
            v = m.rolling.get(tag)
            if v is None:
                continue
            if i - (n - 1) < 0:
                # 窗口延伸到序列之前,约束不完整 -> 丢弃
                continue
            out.append(WindowConstraint(
                n=n, window=tag, end_ym=m.ym, end_idx=i,
                rolling_val=float(v), precision=precision,
            ))
    return out


# ---------------------------------------------------------------------------
# Pass A:约束传播 (fixpoint)
# ---------------------------------------------------------------------------

def _pass_a_propagate(
    months: list[MonthInput],
    constraints: list[WindowConstraint],
    chosen: dict[str, float],
    tie: dict[str, list[float]],
) -> tuple[bool, dict[str, list[float]]]:
    """在 chosen(已定 ym->value)/tie 上做 fixpoint 传播。

    每轮扫全部约束:窗口内仅 1 个未知月 -> 反解 expected -> 候选池找 SELECT_TOL
    内命中。唯一 -> chosen[m]=v;>1 -> tie[m]=hits(下轮其他约束消歧);0 -> miss。
    返 (progressed_at_all: bool, per_month_last_hits: 供 Pass C 诊断)。

    注意:同一未知月在不同约束下 hits 不一致的容忍策略:任一约束定值 chosen
    就当定,后续约束再验证(Pass C)。tie 只在所有约束都无唯一命中时保留。
    """
    per_month_last_hits: dict[str, list[float]] = {}
    changed = True
    any_change = False
    while changed:
        changed = False
        for cons in constraints:
            end_idx = cons.end_idx
            span = months[end_idx - (cons.n - 1): end_idx + 1]
            unknown_yms: list[str] = []
            known_returns: list[float] = []
            for mm in span:
                if mm.ym in chosen:
                    known_returns.append(chosen[mm.ym])
                else:
                    unknown_yms.append(mm.ym)
            if len(unknown_yms) != 1:
                continue
            target_ym = unknown_yms[0]
            if target_ym in chosen:
                continue
            # 反解 expected:(1 + rolling) = product(1 + known) * (1 + x)
            known_prod = 1.0
            for r in known_returns:
                known_prod *= (1.0 + r)
            if known_prod == 0:
                continue
            expected = (1.0 + cons.rolling_val) / known_prod - 1.0
            tol = _tol(cons.window, cons.precision)
            target_month = next(m for m in months if m.ym == target_ym)
            hits = _find_hits(target_month.candidates, expected, tol)
            per_month_last_hits[target_ym] = hits
            if len(hits) == 1:
                chosen[target_ym] = hits[0]
                # 一旦定值,清掉之前的 tie 记录
                tie.pop(target_ym, None)
                changed = True
                any_change = True
            elif len(hits) > 1:
                # 记 tie,后续其他约束可能消歧(轮次仍可能变)
                prior = tie.get(target_ym)
                if prior is None:
                    tie[target_ym] = hits
                    changed = True
                else:
                    # 交集缩小算进展
                    inter = [v for v in prior if v in hits]
                    if inter and len(inter) < len(prior):
                        tie[target_ym] = inter
                        changed = True
                        any_change = True
            # hits 空:约束与候选矛盾,不定值,下一约束继续
    return any_change, per_month_last_hits


# ---------------------------------------------------------------------------
# Pass B:冷启动锚定枚举
# ---------------------------------------------------------------------------

def _pass_b_anchor(
    months: list[MonthInput],
    constraints: list[WindowConstraint],
    chosen: dict[str, float],
) -> bool:
    """传播卡死时,取最早的完全在序列内的 3mo 约束,对未定月做笛卡尔枚举。

    组合 <= MAX_COMBOS 内唯一满足 -> 锚定 chosen;否则不动(交 Pass C 判 tie)。
    返 True 表示锚定成功(至少 1 月被 chosen 新增),否则 False。
    """
    # 找最早的 3mo 约束(其 span 内的未定月最少)
    cons_3mo = [c for c in constraints if c.n == 3]
    cons_3mo.sort(key=lambda c: c.end_idx)
    for cons in cons_3mo:
        span = months[cons.end_idx - 2: cons.end_idx + 1]
        unknown = [mm for mm in span if mm.ym not in chosen]
        if not unknown:
            continue  # 全定
        if len(unknown) == 1:
            continue  # 交 Pass A 处理(不该走 Pass B)
        # 每未知月的去重候选值
        pools = [_unique_values(mm.candidates) for mm in unknown]
        combo_count = 1
        for p in pools:
            combo_count *= max(len(p), 1)
        if combo_count == 0 or combo_count > MAX_COMBOS:
            continue  # 空池或超限,交 Pass C
        # 已定月的复利
        known_prod = 1.0
        span_yms = [mm.ym for mm in span]
        unknown_yms = [mm.ym for mm in unknown]
        # 遍历所有组合:算 span 完整复利,判 3mo 约束
        tol = _tol(cons.window, cons.precision)
        surviving: list[dict[str, float]] = []
        for combo in itertools.product(*pools):
            # 组装 span 完整 returns
            values_by_ym = dict(zip(unknown_yms, combo))
            returns = []
            for mm in span:
                if mm.ym in chosen:
                    returns.append(chosen[mm.ym])
                else:
                    returns.append(values_by_ym[mm.ym])
            comp = _compound(returns)
            if abs(comp - cons.rolling_val) < tol:
                surviving.append(dict(values_by_ym))
        if not surviving:
            continue  # 该约束无解,试下一个 3mo 约束
        if len(surviving) == 1:
            for ym, v in surviving[0].items():
                chosen[ym] = v
            return True
        # 多解:叠加后续约束联立过滤
        for extra in constraints:
            if extra is cons:
                continue
            # 只考虑 span 完全覆盖已 surviving 未知月的约束
            extra_span = months[extra.end_idx - (extra.n - 1): extra.end_idx + 1]
            extra_yms = [mm.ym for mm in extra_span]
            # 每 surviving 组合下,extra span 的所有未知月是否覆盖或已定
            def _combo_matches(cmb: dict[str, float]) -> bool:
                returns_e: list[float] = []
                for mm in extra_span:
                    if mm.ym in cmb:
                        returns_e.append(cmb[mm.ym])
                    elif mm.ym in chosen:
                        returns_e.append(chosen[mm.ym])
                    else:
                        return True  # 有未覆盖未定月,不能用此约束过滤,保守保留
                comp_e = _compound(returns_e)
                tol_e = _tol(extra.window, extra.precision)
                return abs(comp_e - extra.rolling_val) < tol_e

            surviving = [c for c in surviving if _combo_matches(c)]
            if len(surviving) == 1:
                for ym, v in surviving[0].items():
                    chosen[ym] = v
                return True
            if not surviving:
                break  # 全被过滤,该约束组合无一致解
        # 走完全部 extra 约束仍多解 -> 交 Pass C 标 tie(涉及月)
        # 不 chosen 任何值,返 False 让上层继续尝试下一 3mo 约束
    return False


# ---------------------------------------------------------------------------
# Pass C:验证窗口计数 + 终判
# ---------------------------------------------------------------------------

def _verify_windows(
    months: list[MonthInput], chosen: dict[str, float], target_ym: str
) -> list[WindowCheck]:
    """统计所有包含 target_ym 的窗口的验证情况(distinct (n, end_ym))。

    含 3/6/12mo 主窗口;若 target_ym 的 rolling 有 1mo 且 target_ym 已 chosen,
    1mo 也作独立确认窗口。仅 span 内全部月都已 chosen 才能算(否则 skip)。
    inception 若序列起点是成立月且 target_ym 在序列内,作确认窗口(通常年化,
    需 span 完全覆盖至今;为避免复杂化,本实现不加 inception 确认,MIN=2 已够)。
    """
    checks: list[WindowCheck] = []
    ym_to_idx = {m.ym: i for i, m in enumerate(months)}
    target_idx = ym_to_idx[target_ym]

    # 3/6/12mo 主窗口
    for i, m in enumerate(months):
        if not m.rolling or m.rolling.get("parse_error"):
            continue
        for tag, n in WINDOWS:
            v = m.rolling.get(tag)
            if v is None:
                continue
            span_lo = i - (n - 1)
            if span_lo < 0:
                continue
            if not (span_lo <= target_idx <= i):
                continue  # target 不在此窗口 span 内
            span_yms = [months[j].ym for j in range(span_lo, i + 1)]
            if not all(y in chosen for y in span_yms):
                continue  # span 内有未定月,不能算
            actual = _compound([chosen[y] for y in span_yms])
            err = abs(actual - float(v))
            checks.append(WindowCheck(
                window=tag, end_ym=m.ym, expected=float(v),
                actual=actual, error=err, passed=err < VERIFY_TOL,
            ))
    # 1mo 确认窗口:target 月 rolling.1mo(若存在且 target 已 chosen)
    if target_ym in chosen:
        m_target = months[target_idx]
        v1 = (m_target.rolling or {}).get("1mo") if m_target.rolling else None
        if v1 is not None and not (m_target.rolling or {}).get("parse_error"):
            actual = chosen[target_ym]
            err = abs(actual - float(v1))
            checks.append(WindowCheck(
                window="1mo", end_ym=target_ym, expected=float(v1),
                actual=actual, error=err, passed=err < VERIFY_TOL,
            ))
    return checks


# ---------------------------------------------------------------------------
# 主入口:solve_series
# ---------------------------------------------------------------------------

def solve_series(
    months: list[MonthInput],
    inception_ym: Optional[str] = None,   # 目前仅接口保留,inception 确认窗口
                                          # 未启用(需序列起点=成立月且 rolling
                                          # 含 inception,MIN_VERIFY_WINDOWS=2
                                          # 通过 3/6/12 主窗口已够,保留参数不
                                          # 影响求解正确性)
) -> dict[str, MonthResolution]:
    """求解:候选池 + rolling -> 每月 MonthResolution。

    保证:任何 chosen 都是候选池内对象(反捏造铁律);无法验证 -> pending。
    """
    months = sorted(months, key=lambda x: x.ym)
    ym_to_idx = {m.ym: i for i, m in enumerate(months)}
    constraints = _collect_constraints(months, ym_to_idx)

    chosen: dict[str, float] = {}
    tie: dict[str, list[float]] = {}

    # Pass Z 预定值:候选池 distinct value 数 == 1 -> 该月只有一个唯一值可选,
    # 直接 chosen。这是求解器的启动锚点,让后续 Pass A 反解只剩 1 未知月的约束。
    # (纯选择,不生成:值来自候选池,不违反反捏造铁律。)
    for m in months:
        uniq = _unique_values(m.candidates)
        if len(uniq) == 1:
            chosen[m.ym] = uniq[0]

    # 反复 Pass A 与 Pass B 交替,直到都无进展
    last_chosen_size = -1
    while last_chosen_size != len(chosen):
        last_chosen_size = len(chosen)
        _progress_a, _hits = _pass_a_propagate(
            months, constraints, chosen, tie
        )
        if len(chosen) < len(months):
            _pass_b_anchor(months, constraints, chosen)

    # Pass C 终判
    out: dict[str, MonthResolution] = {}
    for m in months:
        pool = list(m.candidates)
        if not pool:
            out[m.ym] = MonthResolution(
                ym=m.ym, status="no_candidates",
                chosen=None, verify_windows=0, pool=pool,
            )
            continue
        # rolling 未提供有效数据 -> 无可用窗口
        no_rolling = (
            m.rolling is None or m.rolling.get("parse_error")
        ) and not any(
            (c.rolling and not c.rolling.get("parse_error"))
            for c in months
        )
        if m.ym in chosen:
            picked = _pick_candidate_for_value(pool, chosen[m.ym])
            # 反捏造铁律断言:必定为池内对象
            assert picked is not None, (
                f"solver internal bug: chosen[{m.ym}]={chosen[m.ym]} "
                f"不在候选池 -- 违反反捏造铁律"
            )
            windows = _verify_windows(months, chosen, m.ym)
            passed_windows = [w for w in windows if w.passed]
            n_pass = len(passed_windows)
            # 若通过窗口=0 但候选被约束选中 -> 说明选择在容差内但验证 0.5% 更严
            # (不太可能,SELECT_TOL << VERIFY_TOL);保守判 constraint_violation
            if n_pass == 0 and any(not w.passed for w in windows):
                out[m.ym] = MonthResolution(
                    ym=m.ym, status="constraint_violation",
                    chosen=picked, verify_windows=0,
                    windows_detail=windows, pool=pool,
                )
                continue
            if n_pass >= MIN_VERIFY_WINDOWS:
                out[m.ym] = MonthResolution(
                    ym=m.ym, status="resolved",
                    chosen=picked, verify_windows=n_pass,
                    windows_detail=windows, pool=pool,
                )
            else:
                # 无窗口可算(整基金无 rolling)或仅 1 窗口 -> 不够准入
                if len(windows) == 0:
                    out[m.ym] = MonthResolution(
                        ym=m.ym, status="unverifiable_no_rolling",
                        chosen=picked, verify_windows=0,
                        windows_detail=windows, pool=pool,
                    )
                else:
                    out[m.ym] = MonthResolution(
                        ym=m.ym, status="unverifiable_no_rolling",
                        chosen=picked, verify_windows=n_pass,
                        windows_detail=windows, pool=pool,
                    )
        else:
            # 未 chosen:tie(hits >1) 或 constraint_violation(hits 空) 或
            # unverifiable(无约束/枚举超限)
            if m.ym in tie:
                tied_vals = tie[m.ym]
                tied_cands = [
                    _pick_candidate_for_value(pool, v) for v in tied_vals
                ]
                tied_cands = [c for c in tied_cands if c is not None]
                out[m.ym] = MonthResolution(
                    ym=m.ym, status="no_unique_candidate",
                    chosen=None, verify_windows=0,
                    tied=tied_cands, pool=pool,
                )
            else:
                # 判定 constraint_violation vs unverifiable:是否至少有一条
                # 约束的 span 覆盖本月
                covering = any(
                    (c.end_idx - (c.n - 1)) <= ym_to_idx[m.ym] <= c.end_idx
                    for c in constraints
                )
                if covering:
                    out[m.ym] = MonthResolution(
                        ym=m.ym, status="constraint_violation",
                        chosen=None, verify_windows=0, pool=pool,
                    )
                else:
                    out[m.ym] = MonthResolution(
                        ym=m.ym, status="unverifiable_no_rolling",
                        chosen=None, verify_windows=0, pool=pool,
                    )
    return out
