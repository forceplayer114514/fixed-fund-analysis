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
