# Task 2 Report: 数据库模型（6张表）

## Status: DONE_WITH_CONCERNS

> DONE on all functional requirements. The "WITH_CONCERNS" flag is solely for a
> necessary, semantically-equivalent adaptation to the available Python runtime
> (see "Adaptation" below). All tests pass; the model definitions are functionally
> identical to the brief.

## What I Implemented

Overwrote the empty `webapp/backend/app/models.py` placeholder (left by Task 1)
with the full 6-table SQLAlchemy 2.0 ORM, and created
`webapp/backend/tests/test_models.py` (verbatim from the brief).

The 6 models, all inheriting `Base` (from `app.database`):

1. **`Fund`** (`funds`) - PK `fund_id` (TEXT/String); `fund_name` UNIQUE NOT NULL;
   `apir_code` UNIQUE NULLABLE (supports Stake-like funds with no APIR);
   `confirmed_url`, `fetch_method`, `url_type` NOT NULL; `max_pdf_pages`,
   `verified_at`, `created_at` (server_default `(datetime('now'))`).
   `relationship`s: `monthly_returns`, `anomalies` (lists), `metrics`
   (one-to-one, `uselist=False`), all `cascade="all, delete-orphan"`.
2. **`MonthlyReturn`** (`monthly_returns`) - autoincrement PK; FK `fund_id`
   `ondelete="CASCADE"`; composite `UniqueConstraint("fund_id","date", name="uq_fund_date")`;
   `date`, `net_return`, `nav` NOT NULL; `commentary_truth` nullable.
3. **`Anomaly`** (`anomalies`) - autoincrement PK; FK `fund_id` `ondelete="CASCADE"`;
   `date, value, z_score, threshold_sigma, mean, stdev` NOT NULL.
4. **`RbaCashRate`** (`rba_cash_rates`) - PK `date_period` (YYYY-MM); `rate` NOT NULL;
   `updated_at` server_default. (No FK to funds - independent dimension table.)
5. **`FundMetric`** (`fund_metrics`) - PK = FK `fund_id` `ondelete="CASCADE"`; full
   5-dimension metric set (进攻/防守/性价比/体感/真实性辅助) with `orig_`/`un_` pairs,
   `unsmoothing_coefficient_phi`, `is_geltner_applied`, `history_months`,
   `is_short_history_warning`, `ljung_box_q`, `is_q_significant`, `updated_at`.
6. **`AiReport`** (`ai_reports`) - autoincrement PK; `fund_ids` (Text, denormalized
   list - no FK to funds per brief), `date_period`, `report_type`, `content`,
   `created_at`.

## Adaptation (the concern)

The brief's model code uses PEP 604 union syntax (`str | None`, `int | None`,
`FundMetric | None`). This is Python 3.10+ syntax. The only interpreter available
on this machine is **Python 3.9.6** (macOS Command-Line Tools system Python);
no `python3.10+`, no Homebrew Python, no project venv exists. With
`from __future__ import annotations` the annotations become strings, but
SQLAlchemy 2.0.51 actively resolves them and raises
`MappedAnnotationError: Could not resolve all types within mapped annotation`.
Without the future import, the class body raises
`TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`.

To make the brief's code run on Python 3.9 I made the minimal, semantically-
identical change: `X | None` -> `Optional[X]` (from `typing`), and kept
`from __future__ import annotations` for uniform forward-ref resolution.
Specifically: `apir_code`, `max_pdf_pages`, `verified_at`, `created_at` (Fund),
`commentary_truth` (MonthlyReturn), `updated_at` (RbaCashRate, FundMetric),
`created_at` (AiReport), and `metrics: Mapped[Optional["FundMetric"]]`.

**No field names, column types, nullability, constraints, FKs, cascade rules, or
relationships were changed.** The model layer is functionally identical to the
brief. If a later task upgrades the runtime to Python 3.10+, the `Optional[X]`
forms can be reverted to `X | None` with zero behavioral difference.

This deviation was flagged in the report rather than silently applied, per
project rule §六 (防范大模型自主决定并悄悄应用 changes to base data/logic).

## TDD Evidence

### RED (Step 2)

Command:
```
cd webapp/backend && python3 -m pytest tests/test_models.py -v
```
Failing output (collection error):
```
ImportError while importing test module 'tests/test_models.py'.
tests/test_models.py:5: in <module>
    from app.models import Fund, MonthlyReturn, Anomaly, RbaCashRate, FundMetric, AiReport
E   ImportError: cannot import name 'Fund' from 'app.models' (.../app/models.py)
=========================== short test summary info ============================
ERROR tests/test_models.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```
Why expected: Task 1 left `app/models.py` as a 0-byte placeholder so
`conftest.py`'s `from app import models` would not ImportError. The module
exists but defines no `Fund`, so the test's `from app.models import Fund` fails.
This is the correct RED (the brief's literal "ModuleNotFoundError" wording does
not apply because the module file exists; the task context predicted this exact
error).

### GREEN (Step 4)

Command:
```
cd webapp/backend && python3 -m pytest tests/test_models.py tests/test_database.py -v
```
Passing output:
```
tests/test_models.py::test_insert_fund_and_returns PASSED                [ 14%]
tests/test_models.py::test_fund_name_unique_constraint PASSED            [ 28%]
tests/test_models.py::test_apir_code_nullable PASSED                     [ 42%]
tests/test_models.py::test_monthly_return_unique_date_per_fund PASSED    [ 57%]
tests/test_models.py::test_cascade_delete_fund_removes_children PASSED   [ 71%]
tests/test_models.py::test_rba_cash_rate_upsert_style PASSED             [ 85%]
tests/test_database.py::test_init_db_creates_all_tables PASSED           [100%]
======================== 7 passed, 7 warnings in 0.03s =========================
```
The 7 warnings are all `PytestUnknownMarkWarning` for the unregistered
`@pytest.mark.unit` marker - harmless and expected per the task context.

`test_database.py` turned GREEN as a side effect: defining `Fund` means
`init_db()` now creates the `funds` table, satisfying its assertion. Full suite
(`python3 -m pytest tests/ -v`) confirms 7/7 passing with no regressions.

## Files Changed

- `webapp/backend/app/models.py` - overwrote empty placeholder with 6-model ORM
  (modified, +~105 lines).
- `webapp/backend/tests/test_models.py` - new file, verbatim from brief
  (added, +~107 lines).

Both committed in `9a7570d`.

## Self-Review Findings

- **Completeness:** All 6 models present with correct fields, PKs, FKs
  (`ondelete="CASCADE"`), the composite UNIQUE on `(fund_id, date)`,
  `fund_name` UNIQUE, `apir_code` nullable+UNIQUE, and cascade relationships.
  `FundMetric` is one-to-one (`uselist=False`). `AiReport` has no FK to funds
  (denormalized `fund_ids` Text) - matches the brief.
- **Quality:** Clean, SQLAlchemy 2.0 style (`Mapped`/`mapped_column`/
  `relationship`/`DeclarativeBase`), Chinese comments preserved, dimension
  section comments kept verbatim.
- **Discipline (YAGNI):** No models or fields beyond the brief. The only
  addition is `from __future__ import annotations` + `from typing import Optional`
  (justified above).
- **Testing:** 6/6 model tests pass; `test_database.py` still passes; output
  pristine (only the unregistered-mark warning).

## Concerns

1. **Python 3.9 adaptation** (detailed above): `Optional[X]` substituted for the
   brief's `X | None`. Functionally identical; flag for awareness and in case a
   later task standardizes on Python 3.10+.
2. **No `pytest.ini`/`pyproject.toml` mark registration:** the
   `@pytest.mark.unit` warnings persist. Not introduced by this task (Task 1's
   `test_database.py` already uses the same marker). Registering the mark is out
   of scope for Task 2 but could be a small follow-up.
3. **`AiReport` is imported but unused** in `test_models.py` - this is verbatim
   from the brief (the import itself exercises that the class exists). No test
   currently asserts AiReport behavior; acceptable per the brief.
