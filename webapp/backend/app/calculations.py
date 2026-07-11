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


def calculate_max_drawdown(nav_series: list[float],
                           fund_name: str = "Unknown") -> float:
    """绝对最大回撤：基于累计NAV序列，返回最深回撤比例（负数）。"""
    if not nav_series:
        return 0.0
    max_dd = 0.0
    peak = nav_series[0]
    for nav in nav_series:
        if nav > peak:
            peak = nav
        if peak < 1e-4:
            raise ValueError(f"[{fund_name}] peak={peak} (低于 1e-4)，无法计算最大回撤，请检查该基金的 NAV 数据是否异常")
        dd = (nav - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return max_dd


def calculate_omega_ratio(excess_returns: list[float]) -> float:
    """Omega 比率（Target = RBA）：超额收益为正的累积面积 / 为负的累积绝对值面积。

    分母为0（无跑输月份）返回 inf；空列表返回 0.0。
    """
    if not excess_returns:
        return 0.0
    gains = sum(r for r in excess_returns if r > 0)
    losses = sum(-r for r in excess_returns if r < 0)
    if losses == 0.0:
        return float("inf")
    return gains / losses


def calculate_excess_win_rate(excess_returns: list[float]) -> float:
    """超额收益胜率：跑赢 RBA（excess > 0）的月数占比。"""
    n = len(excess_returns)
    if n == 0:
        return 0.0
    wins = sum(1 for r in excess_returns if r > 0)
    return wins / n


def calculate_max_consecutive_underperform(excess_returns: list[float]) -> int:
    """跑输 RBA 的最长连续月数（excess <= 0 视为跑输，含持平）。"""
    max_run = 0
    current = 0
    for r in excess_returns:
        if r <= 0:
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


def _excess_returns(returns: list[float], rf_rates: list[float]) -> list[float]:
    """逐月超额收益：r_e,t = r_t - RBA_t / 12。"""
    return [r - (rf / 12.0) for r, rf in zip(returns, rf_rates)]


def _annualized_excess_return_compounded(excess: list[float], n_months: int,
                                         fund_name: str) -> float:
    """spec 4.1 权威算法：逐月扣减后复利年化 (∏(1+r_e))^(12/n) - 1。"""
    if n_months <= 0:
        return 0.0
    comp = 1.0
    for r in excess:
        comp *= (1.0 + r)
    return calculate_annualized_return(comp, n_months, fund_name=fund_name)


def compute_all_metrics(returns: list[float], rf_rates: list[float],
                        fund_name: str = "Unknown") -> dict:
    """计算全部5维 orig/un 指标。

    Args:
        returns: 月度原始收益率序列。
        rf_rates: 对应逐月 RBA 年化利率序列（与 returns 等长）。
        fund_name: 基金名称（用于错误信息）。

    Returns:
        dict，键名与 FundMetric 模型字段一致（不含 fund_id/updated_at）。
    """
    n = len(returns)
    is_short = n < 36

    # 自相关与去平滑判定
    phi, q_stat = calculate_autocorrelation(returns, fund_name=fund_name) if n >= 2 else (0.0, 0.0)
    is_q_sig = q_stat > LJUNG_BOX_CRITICAL_VALUE
    is_geltner = should_apply_geltner(n, phi, q_stat) if not is_short else False
    unsmoothed = unsmooth_returns(returns, phi, fund_name=fund_name) if is_geltner else list(returns)

    # 超额收益序列
    excess_orig = _excess_returns(returns, rf_rates)
    excess_un = _excess_returns(unsmoothed, rf_rates)

    # 维度1：进攻（复利年化超额收益）
    orig_ann_excess = _annualized_excess_return_compounded(excess_orig, n, fund_name)
    un_ann_excess = _annualized_excess_return_compounded(excess_un, n, fund_name)

    # 维度2：防守（最大回撤，基于累计NAV）
    orig_max_dd = calculate_max_drawdown(_build_nav_series(returns), fund_name=fund_name)
    un_max_dd = calculate_max_drawdown(_build_nav_series(unsmoothed), fund_name=fund_name)

    # 维度3：性价比（Omega 比率）
    orig_omega = calculate_omega_ratio(excess_orig)
    un_omega = calculate_omega_ratio(excess_un)

    # 维度4：体感与煎熬度
    orig_win_rate = calculate_excess_win_rate(excess_orig)
    un_win_rate = calculate_excess_win_rate(excess_un)
    orig_max_under = calculate_max_consecutive_underperform(excess_orig)
    un_max_under = calculate_max_consecutive_underperform(excess_un)

    # 维度5：真实性辅助（波动率）
    orig_vol = calculate_annualized_volatility(returns, fund_name=fund_name)
    un_vol = calculate_annualized_volatility(unsmoothed, fund_name=fund_name)

    return {
        "history_months": n,
        "is_short_history_warning": 1 if is_short else 0,
        "unsmoothing_coefficient_phi": phi,
        "is_geltner_applied": 1 if is_geltner else 0,
        "orig_annualized_excess_return": orig_ann_excess,
        "un_annualized_excess_return": un_ann_excess,
        "orig_max_drawdown": orig_max_dd,
        "un_max_drawdown": un_max_dd,
        "orig_omega_ratio": orig_omega,
        "un_omega_ratio": un_omega,
        "orig_excess_win_rate": orig_win_rate,
        "un_excess_win_rate": un_win_rate,
        "orig_max_underperform_months": orig_max_under,
        "un_max_underperform_months": un_max_under,
        "orig_annualized_volatility": orig_vol,
        "un_annualized_volatility": un_vol,
        "ljung_box_q": q_stat,
        "is_q_significant": 1 if is_q_sig else 0,
    }
