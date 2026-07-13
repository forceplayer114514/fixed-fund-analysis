# Task 2 Report: consistency_check A-group (1/2/3/4) + multi-field fixture

## What was implemented

Created a new self-contained module `skills/lib/consistency.py` implementing the A-group (document-internal, DB-independent) self-consistency checks plus a compound-validation helper, with B-group stubs reserved for Task 3.

### Files changed (all new)
- `skills/tests/fixtures/pdf_multifield.json` — synthetic multi-field monthly records fixture (CORRECTED version with `excess_net` / `excess_gross` split keys, all 4 A-group checks self-consistent).
- `skills/tests/test_consistency_check.py` — 5 tests: A-group positive, Check 1 / 3 / 4 negatives, and fields-missing-skip case.
- `skills/lib/consistency.py` — `consistency_check(...)` public entry + `_check_compound` helper + `_check_bgroup` / `_check_correlation` no-op stubs.

### Module surface (must stay stable for Tasks 3/4/5/7)
- Public: `consistency_check(fund_id, records, conn, *, gross_records=None, benchmark_records=None, excess_records=None, excess_gross_records=None, growth_records=None, income_records=None, shareclass_prefix=None, rolling=None, corr_threshold=0.98, fee_diff_monthly_max=0.001) -> tuple[bool, list[str], list[str]]` returning `(pass, errors_block, errors_warn)`. `pass = (len(errors_block) == 0)`.
- A-group (block-level, each skipped when its fields are absent, never fails on missing data):
  - Check 1: `excess_net ≈ net - benchmark` (tol `_TOL = 0.0005`).
  - Check 2: `excess_gross ≈ gross - benchmark` (tol `_TOL`).
  - Check 3: `net < gross + 0.001` (fee-waiver tolerance).
  - Check 4: `net ≈ growth + income` (tol `_TOL`).
- Compound (block-level, A-group substitute for Plotly sources): runs only when `rolling` truthy AND `rolling.get("parse_error", True)` is falsy. Strict all-windows check (3mo/6mo/12mo/inception) at 0.5% threshold — every window must pass, not "at least one". Reuses `lib.extract.verify_monthly_vs_rolling` internally but re-implements the strict judgment per the brief.
- B-group stubs: `_check_bgroup` (Checks 5/6) and `_check_correlation` (Check 7 warn) are `pass` no-ops; `_check_correlation` is always called (warn-level), `_check_bgroup` only when `shareclass_prefix` provided. Task 3 fills these.

## TDD evidence

### RED (Step 3)
Command: `cd skills && /usr/bin/python3 -m pytest tests/test_consistency_check.py -v`
```
ERROR collecting tests/test_consistency_check.py
tests/test_consistency_check.py:7: in <module>
    from lib.consistency import consistency_check
E   ModuleNotFoundError: No module named 'lib.consistency'
!!! Interrupted: 1 error during collection !!!
```

### GREEN (Step 5)
Command: `cd skills && /usr/bin/python3 -m pytest tests/test_consistency_check.py -v`
```
tests/test_consistency_check.py::test_agroup_all_pass PASSED             [ 20%]
tests/test_consistency_check.py::test_check1_net_excess_mismatch_blocks PASSED [ 40%]
tests/test_consistency_check.py::test_check3_net_not_less_than_gross_blocks PASSED [ 60%]
tests/test_consistency_check.py::test_check4_total_return_decomposition_blocks PASSED [ 80%]
tests/test_consistency_check.py::test_agroup_fields_missing_skips_not_fails PASSED [100%]
============================== 5 passed in 0.02s ===============================
```

### Full-suite regression
`cd skills && /usr/bin/python3 -m pytest` -> `94 passed, 35 warnings in 0.24s` (warnings are pre-existing `PytestUnknownMarkWarning` from `test_strategies.py`, unrelated to this task).

## Commit
`c97aa09 feat(consistency): A-group self-consistency checks 1/2/3/4 + compound validation` — 3 files, +300 lines.

## Self-review findings
- Return type is exactly `tuple[bool, list[str], list[str]]` (both the normal return and the empty-records early return `(False, ["无数据"], [])`). PASS.
- A-group checks skip (not fail) when their fields are missing — each guarded by `if net and benchmark and excess_net:` style predicate; verified by `test_agroup_fields_missing_skips_not_fails`. PASS.
- `_check_compound` runs only when `rolling` truthy AND `not rolling.get("parse_error", True)` — parse_error defaults to True when the key is absent, so compound is opt-in on a clean parse. PASS.
- `_check_bgroup` and `_check_correlation` present as `pass` no-ops for Task 3. PASS.
- No `if fund_id ==` special cases anywhere. PASS.
- Tests use real JSON fixtures and the real `db_conn` tmp SQLite fixture (no mocks). PASS.
- Test output pristine (5 passed, no warnings from new tests). PASS.

## Fix: dead code removal

Removed two dead lines in `_check_compound`:
- `from lib.extract import verify_monthly_vs_rolling` (lazy import, only supported the dead call)
- `short = verify_monthly_vs_rolling(records, rolling)` (assigned, never used)

The strict 3mo/6mo/12mo/inception loop below remains unchanged.

Test command: `/usr/bin/python3 -m pytest tests/test_consistency_check.py -v`
```
5 passed in 0.02s
```
All 5 tests pass, unchanged from baseline.
- Minor: the brief's `_check_compound` calls `verify_monthly_vs_rolling(records, rolling)` and binds the result to `short` but never uses it (the strict re-implementation loop below is what actually reports errors). I kept this faithful to the brief; it is dead code but harmless. Task 3 or a later cleanup can remove the unused call if desired.
- No other concerns. The interface contract is stable for downstream tasks.
