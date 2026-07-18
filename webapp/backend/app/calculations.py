"""5维核心指标纯计算函数。无数据库依赖、无网络IO，仅接受 list[float]。

移植自 scripts/metrics.py 并扩展为5维体系。所有函数保持与原实现数值一致。
"""
from __future__ import annotations


def calculate_annualized_return(compounded_return: float, n_months: int,
                                fund_name: str = "Unknown") -> float:
    """复利年化收益率：(compounded_return) ** (12/n) - 1。"""
    if n_months <= 0:
        raise ValueError(f"[{fund_name}] n_months={n_months}, 无法计算年化收益率，时间序列月份数必须为正数")
    return (compounded_return ** (12.0 / n_months)) - 1.0


def calculate_annualized_volatility(returns: list[float],
                                    fund_name: str = "Unknown") -> float:
    """年化波动率：月度收益标准差 * sqrt(12)。"""
    n = len(returns)
    if n < 2:
        return 0.0
    mean_r = sum(returns) / n
    denominator = n - 1
    if denominator <= 0:
        raise ValueError(f"[{fund_name}] denominator={denominator} (n_months - 1), 无法计算年化波动率")
    variance = sum((r - mean_r) ** 2 for r in returns) / denominator
    return (variance * 12.0) ** 0.5


def calculate_max_drawdown_detail(nav_series: list[float],
                                  fund_name: str = "Unknown") -> dict:
    """最大回撤详情：返回 {max_drawdown, recovery_months, recovered}。

    - max_drawdown: 最深回撤比例（负数；无回撤为 0.0）。
    - recovery_months: trough 之后首个 NAV >= 回撤前峰 的月份索引差（索引差即月数）；
      序列末尾仍未恢复时 recovered=False，recovery_months = 末月索引 - trough 索引（已X个月）。
    - 无回撤(max_drawdown==0)时 recovery_months=None, recovered=True。

    NAV 序列按月，故索引差等价于月数差。
    """
    if not nav_series:
        return {"max_drawdown": 0.0, "recovery_months": None, "recovered": True}
    max_dd = 0.0
    peak = nav_series[0]
    peak_idx = 0
    trough_idx = 0
    dd_peak_idx = 0  # 最大回撤对应的前峰索引
    for i, nav in enumerate(nav_series):
        if nav > peak:
            peak = nav
            peak_idx = i
        if peak < 1e-4:
            raise ValueError(f"[{fund_name}] peak={peak} (低于 1e-4)，无法计算最大回撤，请检查该基金的 NAV 数据是否异常")
        dd = (nav - peak) / peak
        if dd < max_dd:
            max_dd = dd
            trough_idx = i
            dd_peak_idx = peak_idx
    # 无回撤
    if max_dd == 0.0:
        return {"max_drawdown": 0.0, "recovery_months": None, "recovered": True}
    # 恢复：trough 之后首个 nav >= 回撤前峰值
    peak_val = nav_series[dd_peak_idx]
    recovery_idx = None
    for j in range(trough_idx + 1, len(nav_series)):
        if nav_series[j] >= peak_val:
            recovery_idx = j
            break
    last_idx = len(nav_series) - 1
    if recovery_idx is not None:
        return {"max_drawdown": max_dd,
                "recovery_months": recovery_idx - trough_idx,
                "recovered": True}
    return {"max_drawdown": max_dd,
            "recovery_months": last_idx - trough_idx,
            "recovered": False}


def calculate_max_drawdown(nav_series: list[float],
                           fund_name: str = "Unknown") -> float:
    """绝对最大回撤（标量，thin wrapper）：基于累计NAV序列，返回最深回撤比例（负数）。

    保留以减少既有调用方/测试扰动；需要恢复月数请用 calculate_max_drawdown_detail。
    """
    return calculate_max_drawdown_detail(nav_series, fund_name=fund_name)["max_drawdown"]


def calculate_information_ratio(excess_returns: list[float],
                                fund_name: str = "Unknown") -> float | None:
    """信息比率 = mean(e) / std(e, ddof=1) * sqrt(12)（PDD 1.1）。

    月度超额序列复用管道现有超额收益序列（r_fund - RBA/12）。
    n<2 或 std=0 时返回 None（无法计算/无意义），前端显示 "-"。
    校验锚点：分子 mean(e)*12 ≈ 年化超额收益（算术 vs 几何年化，容差 <0.3pp）。
    """
    n = len(excess_returns)
    if n < 2:
        return None
    mean_e = sum(excess_returns) / n
    variance = sum((e - mean_e) ** 2 for e in excess_returns) / (n - 1)
    std_e = variance ** 0.5
    if std_e == 0.0:
        return None
    return mean_e / std_e * (12.0 ** 0.5)


def calculate_excess_win_rate(excess_returns: list[float]) -> float:
    """超额收益胜率：跑赢 RBA（excess > 0）的月数占比。"""
    n = len(excess_returns)
    if n == 0:
        return 0.0
    wins = sum(1 for r in excess_returns if r > 0)
    return wins / n


def calculate_max_consecutive_underperform(excess_aligned: list[float | None]) -> int:
    """跑输 RBA 的最长连续月数（excess <= 0 视为跑输，含持平）。

    输入为未压缩对齐序列：RBA 缺失月以 None 表示，遇到 None 视为 streak 中断（保守），
    避免把被剔除月份两侧的跑输月静默拼成连续 streak（PDD 第7条禁止事项精神 / 决策5）。
    """
    max_run = 0
    current = 0
    for e in excess_aligned:
        if e is None:
            current = 0
            continue
        if e <= 0:
            current += 1
            if current > max_run:
                max_run = current
        else:
            current = 0
    return max_run


def calculate_autocorrelation(returns: list[float],
                              fund_name: str = "Unknown") -> tuple[float, float]:
    """一阶自相关系数 phi 与 Ljung-Box Q 统计量。"""
    n = len(returns)
    if n < 2:
        return 0.0, 0.0
    mean_r = sum(returns) / n
    numerator = sum((returns[t] - mean_r) * (returns[t - 1] - mean_r) for t in range(1, n))
    denominator = sum((r - mean_r) ** 2 for r in returns)
    if denominator == 0:
        return 0.0, 0.0
    phi = numerator / denominator
    q_stat = n * (n + 2) * (phi ** 2) / (n - 1)
    return phi, q_stat


def unsmooth_returns(returns: list[float], phi: float,
                     fund_name: str = "Unknown") -> list[float]:
    """Geltner 去平滑：r'_t = (r_t - phi * r_{t-1}) / (1 - phi)。"""
    if not returns:
        return []
    denom = 1.0 - phi
    if denom < 0.01:
        raise ValueError(f"[{fund_name}] phi={phi} 导致分母 1 - phi 为 {denom} (低于 0.01)，无法进行 Geltner 去平滑计算")
    unsmoothed = [returns[0]]
    for t in range(1, len(returns)):
        unsmoothed_val = (returns[t] - phi * returns[t - 1]) / denom
        unsmoothed.append(unsmoothed_val)
    return unsmoothed


# Ljung-Box 检验 95% 置信度临界值（自由度1）
LJUNG_BOX_CRITICAL_VALUE = 3.841


def should_apply_geltner(n_months: int, phi: float, q_stat: float) -> bool:
    """Geltner 去平滑三重防火墙判定。

    防火墙1：历史月数 >= 36
    防火墙2：Ljung-Box Q > 3.841（统计显著）
    防火墙3：0 <= phi <= 0.85（合理非负区间）
    三者全满足才返回 True。
    """
    if n_months < 36:
        return False
    if q_stat <= LJUNG_BOX_CRITICAL_VALUE:
        return False
    if phi < 0.0 or phi > 0.85:
        return False
    return True


def _build_nav_series(returns: list[float]) -> list[float]:
    """由月度收益率构建累计复利 NAV 序列（起点 1.0）。"""
    nav = 1.0
    nav_series = [nav]
    for r in returns:
        nav = nav * (1.0 + r)
        nav_series.append(nav)
    return nav_series


def _excess_aligned(returns: list[float], rf_rates: list[float | None]) -> list[float | None]:
    """逐月超额收益对齐序列：r_e,t = r_t - RBA_t/12。RBA 缺失月（rf=None）保留 None。

    未压缩对齐：供连续性敏感指标（最长跑输）。压缩版由调用方过滤 None 得到。
    """
    return [r - (rf / 12.0) if rf is not None else None
            for r, rf in zip(returns, rf_rates)]


def _compound(returns: list[float]) -> float:
    """逐月复利因子 ∏(1+r)。"""
    comp = 1.0
    for r in returns:
        comp *= (1.0 + r)
    return comp


def _guard_positive_compound(comp: float, fund_name: str, label: str) -> None:
    """comp <= 0 意味着存在单月收益 <= -100% 的极端月（开分数次幂会得复数而非报错）。

    保留原值参与复利，但拒绝年化并报错交人工复核，而非静默产出复数写入 Float 列。
    """
    if comp <= 0:
        raise ValueError(
            f"[{fund_name}] 累计{label}因子 comp={comp:.4f} <= 0，"
            f"存在单月收益 <= -100% 的极端月度数据，数据异常，"
            f"拒绝年化（请人工复核该基金月度数据）"
        )


def _annualized_excess_return_compounded(excess: list[float], n_months: int,
                                         fund_name: str) -> float:
    """spec 4.1 权威算法：逐月扣减后复利年化 (∏(1+r_e))^(12/n) - 1。

    n_months 必须为超额序列有效月数（剔除 RBA 缺失后的 n_excess），而非基金全序列月数。
    """
    if n_months <= 0:
        return 0.0
    comp = _compound(excess)
    _guard_positive_compound(comp, fund_name, "超额收益")
    return calculate_annualized_return(comp, n_months, fund_name=fund_name)


def compute_all_metrics(returns: list[float], rf_rates: list[float | None],
                        fund_name: str = "Unknown") -> dict:
    """计算全部5维 orig/un 指标。

    Args:
        returns: 月度原始收益率序列（基金全序列，已通过缺口零容忍校验）。
        rf_rates: 对应逐月 RBA 年化利率序列（与 returns 等长，缺失月为 None）。
        fund_name: 基金名称（用于错误信息）。

    Returns:
        dict，键名与 FundMetric 模型字段一致（不含 fund_id/updated_at/date_period）。

    RBA 缺失处理（PDD 1.7 / 决策1）：rf=None 的月份从超额序列剔除——
    超额口径指标（年化超额、IR、胜率）用压缩序列 + n_excess；
    基金口径指标（年化收益率、最大回撤+恢复、波动率）用全序列 + n_fund；
    最长跑输用未压缩对齐序列（None 中断 streak，决策5）。
    """
    # 长度守卫：zip 静默截断会导致年化用错误的 n，结果偏差极大且无报错。
    if len(returns) != len(rf_rates):
        raise ValueError(
            f"[{fund_name}] returns 长度 {len(returns)} != rf_rates 长度 {len(rf_rates)}，"
            f"拒绝计算（长度不等 zip 会静默截断致年化错误）"
        )
    n_fund = len(returns)
    is_short = n_fund < 36

    # 自相关与去平滑判定（基金全序列）
    phi, q_stat = calculate_autocorrelation(returns, fund_name=fund_name) if n_fund >= 2 else (0.0, 0.0)
    is_q_sig = q_stat > LJUNG_BOX_CRITICAL_VALUE
    is_geltner = should_apply_geltner(n_fund, phi, q_stat) if not is_short else False
    unsmoothed = unsmooth_returns(returns, phi, fund_name=fund_name) if is_geltner else list(returns)

    # 超额序列：未压缩对齐（含 None）+ 压缩（None 过滤）
    excess_aligned_orig = _excess_aligned(returns, rf_rates)
    excess_aligned_un = _excess_aligned(unsmoothed, rf_rates)
    excess_orig = [e for e in excess_aligned_orig if e is not None]
    excess_un = [e for e in excess_aligned_un if e is not None]
    n_excess = len(excess_orig)

    # 维度1：进攻
    # 年化收益率（基金口径，几何年化，PDD 1.3）：用全序列
    comp_raw_orig = _compound(returns)
    comp_raw_un = _compound(unsmoothed)
    _guard_positive_compound(comp_raw_orig, fund_name, "收益率")
    _guard_positive_compound(comp_raw_un, fund_name, "收益率")
    orig_annualized_return = calculate_annualized_return(comp_raw_orig, n_fund, fund_name=fund_name)
    un_annualized_return = calculate_annualized_return(comp_raw_un, n_fund, fund_name=fund_name)
    # 年化超额收益（超额口径，复利年化）：用压缩序列 + n_excess
    orig_ann_excess = _annualized_excess_return_compounded(excess_orig, n_excess, fund_name)
    un_ann_excess = _annualized_excess_return_compounded(excess_un, n_excess, fund_name)

    # 维度2：防守（最大回撤 + 恢复月数，基金口径）
    orig_dd = calculate_max_drawdown_detail(_build_nav_series(returns), fund_name=fund_name)
    un_dd = calculate_max_drawdown_detail(_build_nav_series(unsmoothed), fund_name=fund_name)

    # 维度3：性价比（信息比率，超额口径，压缩序列）
    orig_ir = calculate_information_ratio(excess_orig, fund_name=fund_name)
    un_ir = calculate_information_ratio(excess_un, fund_name=fund_name)

    # 维度4：体感与煎熬度
    # 超额胜率（超额口径，压缩序列）；最长跑输（未压缩对齐序列，None 中断）
    orig_win_rate = calculate_excess_win_rate(excess_orig)
    un_win_rate = calculate_excess_win_rate(excess_un)
    orig_max_under = calculate_max_consecutive_underperform(excess_aligned_orig)
    un_max_under = calculate_max_consecutive_underperform(excess_aligned_un)

    # 维度5：真实性辅助（波动率，基金口径）
    orig_vol = calculate_annualized_volatility(returns, fund_name=fund_name)
    un_vol = calculate_annualized_volatility(unsmoothed, fund_name=fund_name)

    return {
        "history_months": n_fund,
        "excess_sample_months": n_excess,
        "is_short_history_warning": 1 if is_short else 0,
        "unsmoothing_coefficient_phi": phi,
        "is_geltner_applied": 1 if is_geltner else 0,
        "orig_annualized_return": orig_annualized_return,
        "un_annualized_return": un_annualized_return,
        "orig_annualized_excess_return": orig_ann_excess,
        "un_annualized_excess_return": un_ann_excess,
        "orig_max_drawdown": orig_dd["max_drawdown"],
        "un_max_drawdown": un_dd["max_drawdown"],
        "orig_recovery_months": orig_dd["recovery_months"],
        "un_recovery_months": un_dd["recovery_months"],
        "orig_dd_recovered": 1 if orig_dd["recovered"] else 0,
        "un_dd_recovered": 1 if un_dd["recovered"] else 0,
        "orig_information_ratio": orig_ir,
        "un_information_ratio": un_ir,
        "orig_excess_win_rate": orig_win_rate,
        "un_excess_win_rate": un_win_rate,
        "orig_max_underperform_months": orig_max_under,
        "un_max_underperform_months": un_max_under,
        "orig_annualized_volatility": orig_vol,
        "un_annualized_volatility": un_vol,
        "ljung_box_q": q_stat,
        "is_q_significant": 1 if is_q_sig else 0,
    }
