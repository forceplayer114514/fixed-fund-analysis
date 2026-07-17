# Task 3 Report: 基础计算函数（年化收益、波动率、最大回撤）

## Status: DONE

## What I Implemented

Created two files under `webapp/backend/`:

1. **`app/calculations.py`** - 3 pure calculation functions (no DB, no IO), ported verbatim from `scripts/metrics.py`:
   - `calculate_annualized_return(compounded_return, n_months, fund_name="Unknown") -> float` - 复利年化收益率 `(compounded_return) ** (12/n) - 1`；`n_months <= 0` 抛 `ValueError`。对应 `scripts/metrics.py:123-126`。
   - `calculate_annualized_volatility(returns, fund_name="Unknown") -> float` - 月度收益标准差 * sqrt(12)；`n < 2` 返回 0.0。对应 `scripts/metrics.py:128-137`。
   - `calculate_max_drawdown(nav_series, fund_name="Unknown") -> float` - 基于累计 NAV 的最深回撤比例（负数）；空序列返回 0.0；`peak < 1e-4` 抛 `ValueError`。对应 `scripts/metrics.py:108-121`。

2. **`tests/test_calculations.py`** - 3 unit tests, assertions copied verbatim from the brief (which are a subset of `tests/test_metrics.py` to guarantee numerical parity).

Formula parity with `scripts/metrics.py` verified line-by-line before implementation. Environment: Python 3.9.6; `from __future__ import annotations` kept so PEP 585 `list[float]` annotations work.

## TDD Evidence

### RED (Step 2)
Command: `cd webapp/backend && python3 -m pytest tests/test_calculations.py -v`
```
collected 0 items / 1 error
ERROR collecting tests/test_calculations.py
tests/test_calculations.py:5: in <module>
    from app.calculations import (
E   ModuleNotFoundError: No module named 'app.calculations'
1 error in 0.06s
```
Expected FAIL confirmed (`ModuleNotFoundError: No module named 'app.calculations'`).

### GREEN (Step 4)
Command: `cd webapp/backend && python3 -m pytest tests/test_calculations.py -v`
```
tests/test_calculations.py::test_calculate_annualized_return PASSED      [ 33%]
tests/test_calculations.py::test_calculate_annualized_volatility PASSED  [ 66%]
tests/test_calculations.py::test_calculate_max_drawdown PASSED           [100%]
3 passed, 3 warnings in 0.01s
```
3/3 passed. Only warnings are the harmless `PytestUnknownMarkWarning: Unknown pytest.mark.unit` (unregistered mark, explicitly expected per brief).

Full backend suite sanity check: `10 passed, 10 warnings in 0.04s` (3 new + 7 pre-existing test_database/test_models). No regressions.

## Files Changed

- `webapp/backend/app/calculations.py` (new, 45 lines)
- `webapp/backend/tests/test_calculations.py` (new, 33 lines)

Commit: `3afbb39` - `feat(backend): add basic calculation functions (annualized return, volatility, max drawdown)`

Only these two files were staged/committed. Pre-existing modifications to `.superpowers/sdd/*.md` tracking files were left untouched (not part of this task's scope per the brief's explicit `git add` of only the two source files).

## Self-Review Findings

- **Completeness:** 3 functions with exact formulas (verified against `scripts/metrics.py:108-137`); 3 tests pass. ✅
- **Quality:** Pure functions - no DB imports, no network IO, only `list[float]`/`float` operations; no side effects. Chinese docstrings/comments per project convention. ✅
- **Discipline (YAGNI):** Exactly the 3 functions specified; no extra helpers added. `calculations.py` will be extended in Tasks 4, 5, 7 - nothing pre-added. ✅
- **Testing:** Numerical assertions match the brief verbatim (and are a subset of `tests/test_metrics.py` for parity). Test output pristine - only the harmless `@pytest.mark.unit` unregistered-mark warnings. ✅

## Concerns

- Minor (non-blocking): The brief's test file includes `import math` which is unused in the test body. Kept verbatim per the brief (which is the authoritative spec). Does not affect test output or correctness.
- No other concerns. Formulas, error messages, and edge-case return values all match `scripts/metrics.py` exactly.
