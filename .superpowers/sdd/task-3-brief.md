### Task 3: 基础计算函数（年化收益、波动率、最大回撤）

**Files:**
- Create: `webapp/backend/app/calculations.py`
- Create: `webapp/backend/tests/test_calculations.py`

**Interfaces:**
- Produces: `calculate_annualized_return(compounded_return: float, n_months: int, fund_name: str = "Unknown") -> float`、`calculate_annualized_volatility(returns: list[float], fund_name: str = "Unknown") -> float`、`calculate_max_drawdown(nav_series: list[float], fund_name: str = "Unknown") -> float`。行为与现有 `scripts/metrics.py` 完全一致（数值断言复用 `tests/test_metrics.py`）。

- [ ] **Step 1: 写失败测试 tests/test_calculations.py**

```python
"""基础计算函数测试，断言复用 tests/test_metrics.py 以保证移植精度。"""
import pytest
import math

from app.calculations import (
    calculate_annualized_return,
    calculate_annualized_volatility,
    calculate_max_drawdown,
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.calculations'`）

- [ ] **Step 3: 实现 app/calculations.py（基础部分）**

```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/calculations.py webapp/backend/tests/test_calculations.py
git commit -m "feat(backend): add basic calculation functions (annualized return, volatility, max drawdown)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

