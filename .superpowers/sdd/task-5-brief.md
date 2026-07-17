### Task 5: 自相关、Geltner 去平滑三重防火墙

**Files:**
- Modify: `webapp/backend/app/calculations.py`
- Modify: `webapp/backend/tests/test_calculations.py`

**Interfaces:**
- Produces: `calculate_autocorrelation(returns: list[float], fund_name: str = "Unknown") -> tuple[float, float]`（返回 `(phi, q_stat)`）、`unsmooth_returns(returns: list[float], phi: float, fund_name: str = "Unknown") -> list[float]`、`should_apply_geltner(n_months: int, phi: float, q_stat: float) -> bool`（三重防火墙判定）。

- [ ] **Step 1: 追加失败测试到 tests/test_calculations.py**

```python
from app.calculations import (
    calculate_autocorrelation,
    unsmooth_returns,
    should_apply_geltner,
)


@pytest.mark.unit
def test_calculate_autocorrelation():
    # 完全正自相关序列：phi 接近 1
    phi, q_stat = calculate_autocorrelation([0.01, 0.02, 0.03, 0.04, 0.05] * 10, "TestFund")
    assert phi > 0.9
    assert q_stat > 0
    # 短序列 -> (0.0, 0.0)
    assert calculate_autocorrelation([0.01], "TestFund") == (0.0, 0.0)


@pytest.mark.unit
def test_unsmooth_returns():
    assert unsmooth_returns([0.01, 0.015, 0.02], 0.5, "TestFund") == pytest.approx([0.01, 0.02, 0.025])
    with pytest.raises(ValueError) as excinfo:
        unsmooth_returns([0.01, 0.02], 0.999, "TestFund")
    assert "phi=0.999" in str(excinfo.value)
    assert unsmooth_returns([], 0.5, "TestFund") == []


@pytest.mark.unit
def test_should_apply_geltner_three_firewalls():
    # 全部通过：n>=36, Q>3.841, 0<=phi<=0.85
    assert should_apply_geltner(36, 0.5, 10.0) is True
    # 防火墙1：n < 36
    assert should_apply_geltner(35, 0.5, 10.0) is False
    # 防火墙2：Q <= 3.841
    assert should_apply_geltner(36, 0.5, 2.0) is False
    # 防火墙3：phi < 0
    assert should_apply_geltner(36, -0.1, 10.0) is False
    # 防火墙3：phi > 0.85
    assert should_apply_geltner(36, 0.9, 10.0) is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v -k "autocorrelation or unsmooth or geltner"`
Expected: FAIL（`ImportError`）

- [ ] **Step 3: 追加实现到 app/calculations.py 末尾**

```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/calculations.py webapp/backend/tests/test_calculations.py
git commit -m "feat(backend): add autocorrelation, Geltner unsmoothing with triple firewall

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

