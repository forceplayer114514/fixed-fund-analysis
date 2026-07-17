### Task 4: Omega 比率、超额胜率、最长连续跑输月数

**Files:**
- Modify: `webapp/backend/app/calculations.py`（追加函数）
- Modify: `webapp/backend/tests/test_calculations.py`（追加测试）

**Interfaces:**
- Produces: `calculate_omega_ratio(excess_returns: list[float]) -> float`（Target=RBA 的盈亏面积比，分母0返回 `inf`）、`calculate_excess_win_rate(excess_returns: list[float]) -> float`（跑赢RBA月数占比）、`calculate_max_consecutive_underperform(excess_returns: list[float]) -> int`（连续 `r_e <= 0` 的最长月数）。

- [ ] **Step 1: 追加失败测试到 tests/test_calculations.py**

```python
from app.calculations import (
    calculate_omega_ratio,
    calculate_excess_win_rate,
    calculate_max_consecutive_underperform,
)
import math


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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v -k "omega or win_rate or underperform"`
Expected: FAIL（`ImportError: cannot import name 'calculate_omega_ratio'`）

- [ ] **Step 3: 追加实现到 app/calculations.py 末尾**

```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/calculations.py webapp/backend/tests/test_calculations.py
git commit -m "feat(backend): add Omega ratio, win rate, max consecutive underperform

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

