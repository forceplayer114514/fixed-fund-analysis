"""基础计算函数测试，断言复用 tests/test_metrics.py 以保证移植精度。"""
import pytest
import math

from app.calculations import (
    calculate_annualized_return,
    calculate_annualized_volatility,
    calculate_max_drawdown,
    calculate_omega_ratio,
    calculate_excess_win_rate,
    calculate_max_consecutive_underperform,
)


@pytest.mark.unit
def test_calculate_annualized_return():
    assert calculate_annualized_return(1.1025, 6, "TestFund") == pytest.approx(0.21550625)
    with pytest.raises(ValueError) as excinfo:
        calculate_annualized_return(1.05, 0, "TestFund")
    assert "[TestFund]" in str(excinfo.value) and "n_months=0" in str(excinfo.value)


@pytest.mark.unit
def test_calculate_annualized_volatility():
    assert calculate_annualized_volatility([0.01, 0.02, 0.03], "TestFund") == pytest.approx(0.03464101615)
    assert calculate_annualized_volatility([0.01], "TestFund") == 0.0
    assert calculate_annualized_volatility([], "TestFund") == 0.0


@pytest.mark.unit
def test_calculate_max_drawdown():
    assert calculate_max_drawdown([100.0, 105.0, 94.5, 91.35, 110.0], "TestFund") == pytest.approx(-0.13)
    with pytest.raises(ValueError) as excinfo:
        calculate_max_drawdown([0.0, 10.0], "TestFund")
    assert "peak=0.0" in str(excinfo.value)
    assert calculate_max_drawdown([], "TestFund") == 0.0


@pytest.mark.unit
def test_calculate_omega_ratio():
    # excess = [0.02, -0.01, 0.03, -0.02]
    # 正和 = 0.05, 负和绝对值 = 0.03, Omega = 1.6667
    assert calculate_omega_ratio([0.02, -0.01, 0.03, -0.02]) == pytest.approx(0.05 / 0.03)
    # 无跑输月份 -> inf
    assert math.isinf(calculate_omega_ratio([0.02, 0.03, 0.01]))
    # 全部跑输 -> 分子0, 分母>0 -> 0.0
    assert calculate_omega_ratio([-0.02, -0.01]) == 0.0
    # 空列表 -> 0.0
    assert calculate_omega_ratio([]) == 0.0


@pytest.mark.unit
def test_calculate_excess_win_rate():
    # [0.02, -0.01, 0.03, 0.0] -> 正数2个 -> 2/4 = 0.5（0.0 不算跑赢）
    assert calculate_excess_win_rate([0.02, -0.01, 0.03, 0.0]) == pytest.approx(0.5)
    assert calculate_excess_win_rate([0.02, 0.03]) == pytest.approx(1.0)
    assert calculate_excess_win_rate([]) == 0.0


@pytest.mark.unit
def test_calculate_max_consecutive_underperform():
    # excess = [0.02, -0.01, -0.03, 0.04, -0.01, -0.02, -0.01]
    # 连续 <= 0 的块：[-0.01,-0.03]长2, [-0.01,-0.02,-0.01]长3
    assert calculate_max_consecutive_underperform([0.02, -0.01, -0.03, 0.04, -0.01, -0.02, -0.01]) == 3
    # 全部跑赢 -> 0
    assert calculate_max_consecutive_underperform([0.02, 0.03]) == 0
    # 空列表 -> 0
    assert calculate_max_consecutive_underperform([]) == 0
