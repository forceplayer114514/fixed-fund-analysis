import sys
import os
import pytest

# Ensure scripts directory is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))

from metrics import (
    calculate_annualized_return,
    calculate_annualized_volatility,
    calculate_downside_deviation_excess,
    calculate_max_drawdown,
    calculate_sortino_ratio,
    unsmooth_returns
)

@pytest.mark.unit
def test_calculate_annualized_return():
    # Scenario A: Valid inputs (compounded_return = 1.1025, n_months = 6)
    # (1.1025 ** (12 / 6)) - 1.0 = (1.1025 ** 2) - 1.0 = 1.21550625 - 1.0 = 0.21550625
    assert calculate_annualized_return(1.1025, 6, "TestFund") == pytest.approx(0.21550625)

    # Scenario B: Invalid input (zero months) -> raises ValueError
    with pytest.raises(ValueError) as excinfo:
        calculate_annualized_return(1.05, 0, "TestFund")
    assert "[TestFund]" in str(excinfo.value)
    assert "n_months=0" in str(excinfo.value)

    # Scenario C: Invalid input (negative months) -> raises ValueError
    with pytest.raises(ValueError) as excinfo:
        calculate_annualized_return(1.05, -3, "TestFund")
    assert "[TestFund]" in str(excinfo.value)
    assert "n_months=-3" in str(excinfo.value)


@pytest.mark.unit
def test_calculate_annualized_volatility():
    # Scenario A: Standard inputs (returns = [0.01, 0.02, 0.03])
    # Mean = 0.02, Variance = 0.0001, Monthly SD = 0.01
    # Annualized Volatility = 0.01 * sqrt(12) = 0.03464101615
    assert calculate_annualized_volatility([0.01, 0.02, 0.03], "TestFund") == pytest.approx(0.03464101615)

    # Scenario B: Short history (less than 2 returns) -> returns 0.0
    assert calculate_annualized_volatility([0.01], "TestFund") == 0.0
    assert calculate_annualized_volatility([], "TestFund") == 0.0


@pytest.mark.unit
def test_calculate_downside_deviation_excess():
    # Scenario A: Mix of positive and negative returns
    # excess_returns = [-0.01, 0.02, -0.02, 0.01]
    # Negative squared sum = (-0.01)^2 + (-0.02)^2 = 0.0001 + 0.0004 = 0.0005
    # Mean squared = 0.0005 / 4 = 0.000125
    # Annualized downside deviation = sqrt(0.000125 * 12) = sqrt(0.0015) = 0.03872983346
    assert calculate_downside_deviation_excess([-0.01, 0.02, -0.02, 0.01], "TestFund") == pytest.approx(0.03872983346)

    # Scenario B: Empty list -> returns 0.0
    assert calculate_downside_deviation_excess([], "TestFund") == 0.0

    # Scenario C: No negative returns -> returns 0.0
    assert calculate_downside_deviation_excess([0.01, 0.02, 0.0], "TestFund") == 0.0


@pytest.mark.unit
def test_calculate_max_drawdown():
    # Scenario A: Valid NAV series
    # nav_series = [100.0, 105.0, 94.5, 91.35, 110.0]
    # drawdowns at t=2: (94.5 - 105.0) / 105.0 = -0.10
    # drawdowns at t=3: (91.35 - 105.0) / 105.0 = -0.13
    # Max drawdown = -0.13
    assert calculate_max_drawdown([100.0, 105.0, 94.5, 91.35, 110.0], "TestFund") == pytest.approx(-0.13)

    # Scenario B: Zero or negative peak NAV -> raises ValueError
    with pytest.raises(ValueError) as excinfo:
        calculate_max_drawdown([0.0, 10.0], "TestFund")
    assert "[TestFund]" in str(excinfo.value)
    assert "peak=0.0" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        calculate_max_drawdown([-5.0, -2.0], "TestFund")
    assert "[TestFund]" in str(excinfo.value)
    assert "peak=-5.0" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        calculate_max_drawdown([5e-5, 10.0], "TestFund")
    assert "[TestFund]" in str(excinfo.value)
    assert "peak=5e-05" in str(excinfo.value) or "peak=5e-5" in str(excinfo.value)

    # Scenario C: Empty NAV series -> returns 0.0
    assert calculate_max_drawdown([], "TestFund") == 0.0


@pytest.mark.unit
def test_calculate_sortino_ratio():
    # Scenario A: Normal calculation
    # excess = 0.08, downside = 0.02 -> Sortino = 4.0
    assert calculate_sortino_ratio(0.08, 0.02, "TestFund") == pytest.approx(4.0)

    # Scenario B: Downside deviation is below 1e-6 -> raises ValueError
    with pytest.raises(ValueError) as excinfo:
        calculate_sortino_ratio(0.08, 0.0, "TestFund", "downside_deviation_original")
    assert "[TestFund]" in str(excinfo.value)
    assert "downside_deviation_original=0.0" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        calculate_sortino_ratio(0.08, 5e-7, "TestFund", "downside_deviation_original")
    assert "[TestFund]" in str(excinfo.value)
    assert "downside_deviation_original=5e-07" in str(excinfo.value) or "downside_deviation_original=5e-7" in str(excinfo.value)


@pytest.mark.unit
def test_unsmooth_returns():
    # Scenario A: Standard unsmoothing (returns = [0.01, 0.015, 0.02], phi = 0.5)
    # u_0 = 0.01
    # u_1 = (0.015 - 0.5 * 0.01) / 0.5 = 0.02
    # u_2 = (0.02 - 0.5 * 0.015) / 0.5 = 0.025
    assert unsmooth_returns([0.01, 0.015, 0.02], 0.5, "TestFund") == pytest.approx([0.01, 0.02, 0.025])

    # Scenario B: phi causes denom < 0.01 -> raises ValueError
    with pytest.raises(ValueError) as excinfo:
        unsmooth_returns([0.01, 0.02], 0.999, "TestFund")
    assert "[TestFund]" in str(excinfo.value)
    assert "phi=0.999" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        unsmooth_returns([0.01, 0.02], 1.0, "TestFund")
    assert "[TestFund]" in str(excinfo.value)
    assert "phi=1.0" in str(excinfo.value)

    # Scenario C: Empty returns list -> returns []
    assert unsmooth_returns([], 0.5, "TestFund") == []
