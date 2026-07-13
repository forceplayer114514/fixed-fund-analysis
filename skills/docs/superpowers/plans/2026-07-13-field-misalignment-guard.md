# Field Misalignment Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a write-path consistency guard that blocks field-misaligned monthly returns from silently entering the DB (e.g. AusBond benchmark stored as a fund share class), via prevention (Plotly extraction function, read-only probe agent, write token) + detection (7-check self-consistency, full-window compound validation, post-ingest audit).

**Architecture:** New `lib/consistency.py` runs 7 checks (A-group document-internal 1/2/3/4 + compound-validation, B-group cross-sequence 5/6 block, 7 correlation warn) returning `(pass, errors_block, errors_warn)`. New `lib/extract.py::parse_plotly_nav_series` extracts NAV from Plotly hovertext by `name` filter (zero/multi match raise, benchmark auto-drop). New `lib/ingest.py::add_fund_from_plotly_html` entry for Coolabah-style HTML+PDF sources. New `lib/audit.py` batch-scans all funds after every ingest. `lib/db.py` write ops gated by `FUND_DB_WRITE_TOKEN` env. Ingest entry points call consistency after single-sequence `gate_check`, block on `errors_block`.

**Tech Stack:** Python 3.9.6 (use `Optional[X]` not PEP 604), sqlite3, pytest, PyMuPDF (parse_pdf_text), no new deps.

## Global Constraints

- Python 3.9.6: `from typing import Optional`, `list[tuple[str, float]]` OK (PEP 585), NO `X | None`.
- All tests use the `db_conn` fixture (tmp_path SQLite), never touch `data/fund_analysis.db`.
- Run tests from `skills/`: `cd skills && python3 -m pytest tests/<file>::<test> -v`.
- No `if fund_id == "coolabah..."` special-case branches anywhere. All checks generic.
- No silent discard / old-value fallback on check failure: block + preserve raw text path.
- `parse_plotly_nav_series` zero-match -> raise (never empty list); multi-match -> raise; benchmark trace auto-drop.
- Check 7 (correlation) is **warn only**, never block.
- Check 6 `fee_diff_monthly_max` default 0.001 (0.1%/mo), document "assumes no performance-fee diff", no hardcoded constant as universal solution.
- Each task ends with a passing test + commit. TDD: red (failing test) -> green (minimal impl) -> commit.
- DRY, YAGNI, frequent commits.

---

## File Structure

```
lib/extract.py          + parse_plotly_nav_series()        # Plotly hovertext NAV extraction
lib/consistency.py      # NEW: 7-check self-consistency + compound validation
lib/ingest.py           + add_fund_from_plotly_html()      # Coolabah HTML+PDF entry
                        gate pipeline calls consistency_check
lib/audit.py            # NEW: batch scan all funds, auto-run post-ingest
lib/db.py               + FUND_DB_WRITE_TOKEN gate on write ops
tests/test_parse_plotly_nav.py     # fixtures A/B
tests/test_consistency_check.py    # fixtures D/E + 7-check positive/negative
tests/test_compound_validation.py  # full-window compound (D/E scenarios)
tests/test_header_parsing.py       # fixture C column-shuffle regression
tests/test_audit.py                # batch scan + auto-trigger
tests/test_db_write_token.py       # token gate PermissionError
tests/fixtures/                    # A/B/C/D/E fixture files
.claude/skills/add_fixed_fund.md   # probe step -> read-only agent
```

---

### Task 1: parse_plotly_nav_series + fixtures A/B (Plotly extraction foundation)

**Files:**
- Create: `skills/tests/fixtures/frhy_assisted.html`
- Create: `skills/tests/fixtures/frhy_institutional.html`
- Create: `skills/tests/test_parse_plotly_nav.py`
- Modify: `skills/lib/extract.py` (append new function near line 700, after `gate_check_table`)

**Interfaces:**
- Produces: `parse_plotly_nav_series(html: str, fund_name_pattern: str) -> list[tuple[str, float]]` returns `[(YYYY-MM-DD, nav), ...]` ascending by date. Raises `ValueError` on zero trace match or multi-trace match. Benchmark traces (name contains Benchmark/Index/AusBond) auto-dropped before pattern matching.

- [ ] **Step 1: Create fixture A (`frhy_assisted.html`)**

Minimal synthetic Plotly HTML with 2 traces: Assisted fund (43 points, 2022-11-30 $100 -> 2026-05-31 $134.24) + AusBond benchmark (43 points, 2022-11-30 $100 -> 2026-05-31 $119.19). Real format uses single-line Plotly JSON with `"text": [...]` array, each entry `"<Fund Name><br />YYYY-MM-DD: $NAV"`. Use 5 points each for a compact fixture (full 43 not needed for unit test; keep dates monthly 2022-11-30..2023-03-31).

```html
<div class="plotly-graph-div"></div>
<script type="text/javascript">
window.PLOTLYENV=window.PLOTLYENV || {};
var data = [
  {"name":"Coolabah Floating-Rate High Yield Fund (Assisted)","x":["2022-11-30","2022-12-31","2023-01-31","2023-02-28","2023-03-31"],"text":["Coolabah Floating-Rate High Yield Fund (Assisted)<br />2022-11-30: $100.00","Coolabah Floating-Rate High Yield Fund (Assisted)<br />2022-12-31: $100.52","Coolabah Floating-Rate High Yield Fund (Assisted)<br />2023-01-31: $101.04","Coolabah Floating-Rate High Yield Fund (Assisted)<br />2023-02-28: $101.56","Coolabah Floating-Rate High Yield Fund (Assisted)<br />2023-03-31: $102.08"],"type":"scatter"},
  {"name":"AusBond Credit FRN Index","x":["2022-11-30","2022-12-31","2023-01-31","2023-02-28","2023-03-31"],"text":["AusBond Credit FRN Index<br />2022-11-30: $100.00","AusBond Credit FRN Index<br />2022-12-31: $100.20","AusBond Credit FRN Index<br />2023-01-31: $100.40","AusBond Credit FRN Index<br />2023-02-28: $100.60","AusBond Credit FRN Index<br />2023-03-31: $100.80"],"type":"scatter"}
];
</script>
```

Save to `skills/tests/fixtures/frhy_assisted.html`.

- [ ] **Step 2: Create fixture B (`frhy_institutional.html`)**

Same structure but trace 1 name = `Coolabah Floating-Rate High Yield Fund (Institutional)`, NAVs 100.00->100.98->101.96->102.94->103.92 (institutional slightly higher per real data). Benchmark identical to A. Save to `skills/tests/fixtures/frhy_institutional.html`.

- [ ] **Step 3: Write failing test (`test_parse_plotly_nav.py`)**

```python
"""parse_plotly_nav_series tests: name-filter extraction, benchmark drop, zero/multi raise."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.extract import parse_plotly_nav_series

FIX = Path(__file__).parent / "fixtures"


def test_assisted_extracts_correct_trace_drops_benchmark():
    html = (FIX / "frhy_assisted.html").read_text(encoding="utf-8")
    series = parse_plotly_nav_series(html, "Assisted")
    assert len(series) == 5
    assert series[0] == ("2022-11-30", 100.00)
    assert series[-1] == ("2023-03-31", 102.08)
    # benchmark not included
    navs = [nav for _, nav in series]
    assert all(nav >= 100.0 for nav in navs)
    assert 100.80 not in navs  # last benchmark value


def test_institutional_extracts_correct_trace():
    html = (FIX / "frhy_institutional.html").read_text(encoding="utf-8")
    series = parse_plotly_nav_series(html, "Institutional")
    assert len(series) == 5
    assert series[0] == ("2022-11-30", 100.00)
    assert series[-1] == ("2023-03-31", 103.92)


def test_zero_match_raises():
    html = (FIX / "frhy_assisted.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="零匹配"):
        parse_plotly_nav_series(html, "NonexistentClass")


def test_multi_match_raises():
    # build html with two traces both matching "Coolabah"
    html = """
    {"name":"Coolabah Fund A","text":["Coolabah Fund A<br />2022-11-30: $100.00"]},
    {"name":"Coolabah Fund B","text":["Coolabah Fund B<br />2022-12-31: $101.00"]}
    """
    with pytest.raises(ValueError, match="多 trace 匹配"):
        parse_plotly_nav_series(html, "Coolabah")


def test_ascending_order():
    html = (FIX / "frhy_assisted.html").read_text(encoding="utf-8")
    series = parse_plotly_nav_series(html, "Assisted")
    dates = [d for d, _ in series]
    assert dates == sorted(dates)
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd skills && python3 -m pytest tests/test_parse_plotly_nav.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_plotly_nav_series'`

- [ ] **Step 5: Implement `parse_plotly_nav_series`**

Append to `skills/lib/extract.py` (after `gate_check_table`, ~line 698):

```python
def parse_plotly_nav_series(
    html: str,
    fund_name_pattern: str,
) -> list[tuple[str, float]]:
    """从 pandoc HTML 报告 Plotly hovertext 提取基金类 NAV 序列。

    按 fund_name_pattern 在 trace 的 name 字段过滤；name 含
    Benchmark/Index/AusBond 的 trace 自动丢弃（结构上 benchmark 不可能混入）。
    多 trace 匹配 pattern -> raise（防 agent 按 trace 顺序猜）。零匹配 -> raise
    （防 pattern 打错时空列表被当"无数据"跳过）。返回 [(date, nav), ...] 升序。
    """
    import re

    if not html or not fund_name_pattern:
        raise ValueError("parse_plotly_nav_series: html 与 fund_name_pattern 必填")

    # 提取所有 "text":[...] 数组（Plotly JSON 单行，DOTALL 跨行）
    text_arrays = re.findall(r'"text":\s*\[([^\]]+)\]', html, re.DOTALL)
    name_fields = re.findall(r'"name":\s*"([^"]+)"', html)

    if len(text_arrays) != len(name_fields):
        raise ValueError(
            f"parse_plotly_nav_series: text 数组数 {len(text_arrays)} != "
            f"name 字段数 {len(name_fields)}，HTML 结构异常"
        )

    # 过滤 benchmark trace，对剩余 trace 按 pattern 匹配
    benchmark_markers = ("benchmark", "index", "ausbond")
    matched: list[list[tuple[str, float]]] = []
    for name, text_arr in zip(name_fields, text_arrays):
        name_lower = name.lower()
        if any(m in name_lower for m in benchmark_markers):
            continue  # benchmark 自动丢弃
        if fund_name_pattern.lower() in name_lower:
            points = re.findall(
                r'"([^"]*?)<br />(\d{4}-\d{2}-\d{2}):\s*\$([\d,.]+)"',
                text_arr,
            )
            series = [
                (date, float(navier.replace(",", "")))
                for _trace_name, date, navier in points
            ]
            matched.append(series)

    if len(matched) == 0:
        raise ValueError(
            f"parse_plotly_nav_series: 零匹配 pattern={fund_name_pattern!r}"
            "（benchmark 已排除）"
        )
    if len(matched) > 1:
        raise ValueError(
            f"parse_plotly_nav_series: 多 trace 匹配 pattern="
            f"{fund_name_pattern!r}，匹配数={len(matched)}"
        )

    series = sorted(matched[0], key=lambda x: x[0])
    return series
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd skills && python3 -m pytest tests/test_parse_plotly_nav.py -v`
Expected: 5 PASS

- [ ] **Step 7: Commit**

```bash
cd skills && git add tests/fixtures/frhy_assisted.html tests/fixtures/frhy_institutional.html tests/test_parse_plotly_nav.py lib/extract.py
git commit -m "feat(extract): add parse_plotly_nav_series with name-filter + zero/multi raise"
```

---

### Task 2: consistency_check A-group (1/2/3/4) + multi-field fixture

**Files:**
- Create: `skills/tests/fixtures/pdf_multifield.json`
- Create: `skills/tests/test_consistency_check.py`
- Create: `skills/lib/consistency.py`

**Interfaces:**
- Consumes: `sqlite3.Connection` (from `lib.db.get_connection`), `list[tuple[str, float]]` records.
- Produces: `consistency_check(fund_id, records, conn, *, gross_records=None, benchmark_records=None, excess_records=None, growth_records=None, income_records=None, shareclass_prefix=None, rolling=None, corr_threshold=0.98, fee_diff_monthly_max=0.001) -> tuple[bool, list[str], list[str]]` returns `(pass, errors_block, errors_warn)`. Block-level (1/2/3/4/5/6 + compound) -> `errors_block`; warn-level (7) -> `errors_warn`. `pass = (len(errors_block) == 0)`.

- [ ] **Step 1: Create fixture `pdf_multifield.json`** (synthetic PDF-extracted multi-field monthly records, all self-consistent)

```json
{
  "fund_id": "test_multifield",
  "net": [["2024-01-31", 0.0050], ["2024-02-29", 0.0040], ["2024-03-31", 0.0060]],
  "gross": [["2024-01-31", 0.0060], ["2024-02-29", 0.0050], ["2024-03-31", 0.0070]],
  "benchmark": [["2024-01-31", 0.0020], ["2024-02-29", 0.0015], ["2024-03-31", 0.0025]],
  "excess": [["2024-01-31", 0.0030], ["2024-02-29", 0.0025], ["2024-03-31", 0.0035]],
  "growth": [["2024-01-31", 0.0030], ["2024-02-29", 0.0020], ["2024-03-31", 0.0040]],
  "income": [["2024-01-31", 0.0020], ["2024-02-29", 0.0020], ["2024-03-31", 0.0020]]
}
```

Check 1 (net excess ≈ net - benchmark): 0.0050-0.0020=0.0030 ✓ etc. Check 2 (gross excess ≈ gross - benchmark): 0.0060-0.0020=0.0040, but excess=0.0030... -- wait excess here is net-excess. Fix: split into `excess_net` and `excess_gross`. Revise fixture:

```json
{
  "fund_id": "test_multifield",
  "net": [["2024-01-31", 0.0050], ["2024-02-29", 0.0040], ["2024-03-31", 0.0060]],
  "gross": [["2024-01-31", 0.0060], ["2024-02-29", 0.0050], ["2024-03-31", 0.0070]],
  "benchmark": [["2024-01-31", 0.0020], ["2024-02-29", 0.0015], ["2024-03-31", 0.0025]],
  "excess_net": [["2024-01-31", 0.0030], ["2024-02-29", 0.0025], ["2024-03-31", 0.0035]],
  "excess_gross": [["2024-01-31", 0.0040], ["2024-02-29", 0.0035], ["2024-03-31", 0.0045]],
  "growth": [["2024-01-31", 0.0030], ["2024-02-29", 0.0020], ["2024-03-31", 0.0040]],
  "income": [["2024-01-31", 0.0020], ["2024-02-29", 0.0020], ["2024-03-31", 0.0020]]
}
```

Check 1: net-benchmark = excess_net (0.0030/0.0025/0.0035 ✓). Check 2: gross-benchmark = excess_gross (0.0040/0.0035/0.0045 ✓). Check 3: net < gross (✓). Check 4: growth+income = net? 0.0030+0.0020=0.0050=net ✓. Save to `skills/tests/fixtures/pdf_multifield.json`.

- [ ] **Step 2: Write failing test (A-group positive + each negative)**

```python
"""consistency_check A-group (1/2/3/4) tests."""
from __future__ import annotations

import json
from pathlib import Path

from lib.consistency import consistency_check

FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _to_dict(recs):
    return {d: r for d, r in recs}


def test_agroup_all_pass(db_conn):
    f = _load("pdf_multifield.json")
    # register a dummy fund row so fund_id exists (B-group queries need it)
    db_conn.execute(
        "INSERT INTO funds(fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES(?, ?, ?, ?, ?)",
        (f["fund_id"], f["fund_id"], "http://x", "pdf", "test"),
    )
    db_conn.commit()
    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn,
        gross_records=f["gross"], benchmark_records=f["benchmark"],
        excess_records=f["excess_net"], growth_records=f["growth"],
        income_records=f["income"],
    )
    assert ok, f"expected pass, block={block}"
    assert block == []


def test_check1_net_excess_mismatch_blocks(db_conn):
    f = _load("pdf_multifield.json")
    f["excess_net"][0][1] = 0.0099  # corrupt: net-benchmark=0.0030 != 0.0099
    db_conn.execute(
        "INSERT INTO funds(fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES(?, ?, ?, ?, ?)",
        (f["fund_id"], f["fund_id"], "http://x", "pdf", "test"),
    )
    db_conn.commit()
    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn,
        gross_records=f["gross"], benchmark_records=f["benchmark"],
        excess_records=f["excess_net"],
    )
    assert not ok
    assert any("Check 1" in e or "净超额" in e for e in block)


def test_check3_net_not_less_than_gross_blocks(db_conn):
    f = _load("pdf_multifield.json")
    f["net"][0][1] = 0.0099  # net > gross(0.0060) violates net < gross
    db_conn.execute(
        "INSERT INTO funds(fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES(?, ?, ?, ?, ?)",
        (f["fund_id"], f["fund_id"], "http://x", "pdf", "test"),
    )
    db_conn.commit()
    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn, gross_records=f["gross"],
    )
    assert not ok
    assert any("Check 3" in e or "net < gross" in e for e in block)


def test_check4_total_return_decomposition_blocks(db_conn):
    f = _load("pdf_multifield.json")
    f["growth"][0][1] = 0.0090  # growth+income=0.0110 != net=0.0050
    db_conn.execute(
        "INSERT INTO funds(fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES(?, ?, ?, ?, ?)",
        (f["fund_id"], f["fund_id"], "http://x", "pdf", "test"),
    )
    db_conn.commit()
    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn,
        growth_records=f["growth"], income_records=f["income"],
    )
    assert not ok
    assert any("Check 4" in e or "Total Return" in e for e in block)


def test_agroup_fields_missing_skips_not_fails(db_conn):
    f = _load("pdf_multifield.json")
    db_conn.execute(
        "INSERT INTO funds(fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES(?, ?, ?, ?, ?)",
        (f["fund_id"], f["fund_id"], "http://x", "pdf", "test"),
    )
    db_conn.commit()
    # only net provided, no gross/benchmark/excess/growth/income -> A-group skip
    ok, block, warn = consistency_check(f["fund_id"], f["net"], db_conn)
    assert ok
    assert block == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd skills && python3 -m pytest tests/test_consistency_check.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lib.consistency'`

- [ ] **Step 4: Create `lib/consistency.py` with A-group only (B-group stubbed)**

```python
"""写库前 self-consistency 强制关卡：7 条校验 + 复利验证。

A 组（文档内自证，无 DB 依赖）：1 净超额恒等 / 2 总超额恒等 / 3 net<gross /
4 总收益=growth+income。字段不全则跳过该条，不因此 fail。
B 组（跨序列，依赖 DB 兄弟）：5 同 family 份额类符号一致 / 6 份额类月度差值
<fee_diff_monthly_max / 7 新序列 vs DB 其他基金相关系数>warn。
复利验证（A 组替代，Plotly 源）：NAV 复利 vs PDF rolling 全窗口（1mo/1yr/inception）。

返回 (pass, errors_block, errors_warn)。block 级失败 -> gate_pass=False 不入库。
"""
from __future__ import annotations

import sqlite3
from typing import Optional


_TOL = 0.0005  # 0.05% 恒等式容差


def _to_dict(recs):
    return {d: r for d, r in recs} if recs else {}


def consistency_check(
    fund_id: str,
    records: list[tuple[str, float]],
    conn: sqlite3.Connection,
    *,
    gross_records: Optional[list[tuple[str, float]]] = None,
    benchmark_records: Optional[list[tuple[str, float]]] = None,
    excess_records: Optional[list[tuple[str, float]]] = None,
    excess_gross_records: Optional[list[tuple[str, float]]] = None,
    growth_records: Optional[list[tuple[str, float]]] = None,
    income_records: Optional[list[tuple[str, float]]] = None,
    shareclass_prefix: Optional[str] = None,
    rolling: Optional[dict] = None,
    corr_threshold: float = 0.98,
    fee_diff_monthly_max: float = 0.001,
) -> tuple[bool, list[str], list[str]]:
    """7 条校验 + 复利验证。返回 (pass, errors_block, errors_warn)。

    block 级（1/2/3/4/5/6 + 复利）-> errors_block；warn 级（7）-> errors_warn。
    pass = (errors_block 为空)。字段不全的 A 组条目跳过，不因此 fail。
    """
    errors_block: list[str] = []
    errors_warn: list[str] = []

    if not records:
        return (False, ["无数据"], [])

    net = _to_dict(records)
    gross = _to_dict(gross_records)
    benchmark = _to_dict(benchmark_records)
    excess_net = _to_dict(excess_records)
    excess_gross = _to_dict(excess_gross_records)
    growth = _to_dict(growth_records)
    income = _to_dict(income_records)

    # Check 1: 净超额 ≈ net - benchmark（net/benchmark/excess_net 都存在时）
    if net and benchmark and excess_net:
        for d in net:
            if d in benchmark and d in excess_net:
                expected = net[d] - benchmark[d]
                if abs(expected - excess_net[d]) > _TOL:
                    errors_block.append(
                        f"Check 1 净超额恒等失败 {d}: net {net[d]} - benchmark "
                        f"{benchmark[d]} = {expected:.6f} != excess "
                        f"{excess_net[d]:.6f}（容差 {_TOL}）"
                    )

    # Check 2: 总超额 ≈ gross - benchmark（gross/benchmark/excess_gross 都存在时）
    if gross and benchmark and excess_gross:
        for d in gross:
            if d in benchmark and d in excess_gross:
                expected = gross[d] - benchmark[d]
                if abs(expected - excess_gross[d]) > _TOL:
                    errors_block.append(
                        f"Check 2 总超额恒等失败 {d}: gross {gross[d]} - "
                        f"benchmark {benchmark[d]} = {expected:.6f} != excess "
                        f"{excess_gross[d]:.6f}"
                    )

    # Check 3: net < gross（两者都存在时，浮点 0.001 容差）
    if net and gross:
        for d in net:
            if d in gross and net[d] > gross[d] + 0.001:
                errors_block.append(
                    f"Check 3 net<gross 失败 {d}: net {net[d]} > gross "
                    f"{gross[d]}（除非 fee waiver，须人工确认）"
                )

    # Check 4: total return = growth + income（三者都存在时）
    if net and growth and income:
        for d in net:
            if d in growth and d in income:
                expected = growth[d] + income[d]
                if abs(expected - net[d]) > _TOL:
                    errors_block.append(
                        f"Check 4 总收益分解失败 {d}: growth {growth[d]} + income "
                        f"{income[d]} = {expected:.6f} != net {net[d]:.6f}"
                    )

    # 复利验证（A 组替代，Plotly 源）：rolling 提供时全窗口校验
    if rolling and not rolling.get("parse_error", True):
        _check_compound(records, rolling, errors_block)

    # B 组（跨序列）+ 7：Task 3 实现
    if shareclass_prefix:
        _check_bgroup(
            fund_id, records, conn, shareclass_prefix,
            fee_diff_monthly_max, errors_block,
        )
    _check_correlation(
        fund_id, records, conn, shareclass_prefix,
        corr_threshold, errors_warn,
    )

    return (len(errors_block) == 0, errors_block, errors_warn)


def _check_compound(
    records: list[tuple[str, float]],
    rolling: dict,
    errors_block: list[str],
) -> None:
    """NAV 复利 vs PDF rolling 全窗口（1mo/3mo/6mo/1yr/inception）。

    records 为月度收益序列；rolling 含 1mo/3mo/6mo/12mo/inception。
    对每个窗口：用 records 末 N 月复利 vs rolling 同期，误差 >0.5% 报 block。
    inception：全序列复利 vs rolling['inception']。
    """
    from lib.extract import verify_monthly_vs_rolling

    sorted_m = sorted(records, key=lambda x: x[0])
    rets = [r for _, r in sorted_m]
    # 短窗口（3/6/12mo）复用 extract.verify_monthly_vs_rolling
    short = verify_monthly_vs_rolling(records, rolling)
    # verify 用"至少一个窗口通过"逻辑，这里要全窗口严格：单独判
    for key, n in [("3mo", 3), ("6mo", 6), ("12mo", 12)]:
        rv = rolling.get(key)
        if rv is None or len(rets) < n:
            continue
        actual = 1.0
        for r in rets[-n:]:
            actual *= (1.0 + r)
        actual -= 1.0
        if abs(actual - rv) >= 0.005:
            errors_block.append(
                f"复利验证失败 {key}: 月度复利 {actual:.4f} vs rolling "
                f"{rv:.4f}，误差 {abs(actual-rv):.4f}（阈值 0.5%）"
            )
    # inception 全窗口
    rv = rolling.get("inception")
    if rv is not None and rets:
        actual = 1.0
        for r in rets:
            actual *= (1.0 + r)
        actual -= 1.0
        if abs(actual - rv) >= 0.005:
            errors_block.append(
                f"复利验证失败 inception: 全序列复利 {actual:.4f} vs rolling "
                f"{rv:.4f}，误差 {abs(actual-rv):.4f}（阈值 0.5%）"
            )


def _check_bgroup(
    fund_id: str,
    records: list[tuple[str, float]],
    conn: sqlite3.Connection,
    shareclass_prefix: str,
    fee_diff_monthly_max: float,
    errors_block: list[str],
) -> None:
    """B 组跨序列校验（Task 3 实现）。"""
    # 占位，Task 3 填充
    pass


def _check_correlation(
    fund_id: str,
    records: list[tuple[str, float]],
    conn: sqlite3.Connection,
    shareclass_prefix: Optional[str],
    corr_threshold: float,
    errors_warn: list[str],
) -> None:
    """Check 7 相关嫌疑（Task 3 实现）。"""
    # 占位，Task 3 填充
    pass
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd skills && python3 -m pytest tests/test_consistency_check.py -v`
Expected: 5 PASS

- [ ] **Step 6: Commit**

```bash
cd skills && git add tests/fixtures/pdf_multifield.json tests/test_consistency_check.py lib/consistency.py
git commit -m "feat(consistency): A-group self-consistency checks 1/2/3/4 + compound validation"
```

---

### Task 3: B-group (5/6 block) + Check 7 (warn) + fixture D (reproduce bug)

**Files:**
- Create: `skills/tests/fixtures/field_misaligned.json`
- Modify: `skills/lib/consistency.py` (`_check_bgroup`, `_check_correlation`)
- Modify: `skills/tests/test_consistency_check.py` (append D-scenario tests)

**Interfaces:**
- Consumes: `consistency_check` from Task 2.
- Produces: B-group 5 (same-family sign consistency, block), 6 (share-class monthly diff < fee_diff_monthly_max, block), 7 (correlation > corr_threshold over >=24 overlapping months, warn). DB query: `SELECT fund_id, date, net_return FROM monthly_returns WHERE fund_id LIKE ? AND fund_id != ?` for siblings; `SELECT fund_id, date, net_return FROM monthly_returns WHERE fund_id != ? AND fund_id NOT LIKE ?` for correlation.

- [ ] **Step 1: Create fixture D (`field_misaligned.json`)** -- reproduces the bug

AusBond benchmark series (pa ~5.14%) injected as Institutional `net`; correct Assisted sibling (pa ~8.78%) already in DB; PDF rolling inception = 9.05% (Institutional's true value). 6-month compact series.

```json
{
  "fund_id": "coolabah_frhy_institutional",
  "shareclass_prefix": "coolabah_frhy_",
  "net": [
    ["2025-01-31", 0.0040], ["2025-02-28", 0.0041], ["2025-03-31", 0.0042],
    ["2025-04-30", 0.0040], ["2025-05-31", 0.0041], ["2025-06-30", 0.0042]
  ],
  "rolling": {
    "1mo": 0.0042, "3mo": 0.0125, "6mo": 0.0250, "12mo": 0.0507,
    "inception": 0.0905, "parse_error": false
  },
  "sibling_assisted": {
    "fund_id": "coolabah_frhy_assisted",
    "rows": [
      ["2025-01-31", 0.0072], ["2025-02-28", 0.0073], ["2025-03-31", 0.0074],
      ["2025-04-30", 0.0072], ["2025-05-31", 0.0073], ["2025-06-30", 0.0074]
    ]
  }
}
```

Note: `net` is the AusBond series (pa ~5% -> ~0.42%/mo). Assisted sibling is ~0.73%/mo (pa ~8.78%). Monthly diff ~0.31% > fee_diff_monthly_max 0.001 -> Check 6 block. Compound: net inception复利 (6 mo only here, but inception rolling=0.0905) -- with 6 months compound ~0.0246 vs rolling inception 0.0905 -> huge mismatch -> compound block. Check 7: high correlation with assisted -> warn.

- [ ] **Step 2: Write failing test (D scenario)**

Append to `skills/tests/test_consistency_check.py`:

```python
def test_fixture_d_misaligned_blocks_on_compound_and_check6(db_conn):
    """复现 bug：AusBond 当 Institutional，compound + Check 6 拦截。"""
    f = _load("field_misaligned.json")
    # 先入库 sibling assisted（DB 兄弟）
    sib = f["sibling_assisted"]
    db_conn.execute(
        "INSERT INTO funds(fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES(?, ?, ?, ?, ?)",
        (sib["fund_id"], sib["fund_id"], "http://x", "html", "test"),
    )
    for d, r in sib["rows"]:
        db_conn.execute(
            "INSERT INTO monthly_returns(fund_id, date, net_return, nav) "
            "VALUES(?, ?, ?, 1.0)",
            (sib["fund_id"], d, r),
        )
    db_conn.commit()

    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn,
        shareclass_prefix=f["shareclass_prefix"],
        rolling=f["rolling"],
    )
    assert not ok, f"应 block，实际 block={block}"
    # Check 6：份额类月度差值超阈值
    assert any("Check 6" in e for e in block), f"Check 6 缺失，block={block}"
    # 复利验证：AusBond 6mo 复利 vs inception rolling 9.05%
    assert any("复利验证失败" in e for e in block), f"复利验证缺失，block={block}"
    # Check 7：高相关 -> warn（非 block）
    assert any("Check 7" in e for e in warn)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd skills && python3 -m pytest tests/test_consistency_check.py::test_fixture_d_misaligned_blocks_on_compound_and_check6 -v`
Expected: FAIL (B-group stubbed pass, no Check 6 / compound errors)

- [ ] **Step 4: Implement `_check_bgroup` (5/6)**

Replace the stub in `skills/lib/consistency.py`:

```python
def _check_bgroup(
    fund_id: str,
    records: list[tuple[str, float]],
    conn: sqlite3.Connection,
    shareclass_prefix: str,
    fee_diff_monthly_max: float,
    errors_block: list[str],
) -> None:
    """B 组跨序列校验（依赖 DB 同 family 兄弟）。

    Check 5：同 family 份额类月度收益符号一致（0 值除外，block）。
    Check 6：份额类月度差值 < fee_diff_monthly_max（默认 0.1%/月）。
    当前假设无 performance fee 差异；带 perf fee 基金二期加 funds.management_fee/
    performance_fee 动态容差。
    """
    pattern = f"{shareclass_prefix}%"
    rows = conn.execute(
        "SELECT fund_id, date, net_return FROM monthly_returns "
        "WHERE fund_id LIKE ? AND fund_id != ?",
        (pattern, fund_id),
    ).fetchall()
    if not rows:
        return  # 无兄弟，跳过

    # 按 fund_id 分组
    siblings: dict[str, dict[str, float]] = {}
    for r in rows:
        siblings.setdefault(r["fund_id"], {})[r["date"]] = r["net_return"]

    net = {d: r for d, r in records}
    for sib_id, sib_net in siblings.items():
        # Check 5：符号一致（同月）
        for d, r in net.items():
            if d in sib_net and r != 0.0 and sib_net[d] != 0.0:
                if (r > 0) != (sib_net[d] > 0):
                    errors_block.append(
                        f"Check 5 份额类符号不一致 {d}: 本基金 {r} vs "
                        f"{sib_id} {sib_net[d]}"
                    )
        # Check 6：月度差值
        for d, r in net.items():
            if d in sib_net:
                diff = abs(r - sib_net[d])
                if diff > fee_diff_monthly_max:
                    errors_block.append(
                        f"Check 6 份额类月度差值超阈值 {d}: 本基金 {r} vs "
                        f"{sib_id} {sib_net[d]}，差 {diff:.6f} > "
                        f"{fee_diff_monthly_max}（假设无 performance fee 差异）"
                    )
```

- [ ] **Step 5: Implement `_check_correlation` (7 warn)**

Replace the stub:

```python
def _check_correlation(
    fund_id: str,
    records: list[tuple[str, float]],
    conn: sqlite3.Connection,
    shareclass_prefix: Optional[str],
    corr_threshold: float,
    errors_warn: list[str],
) -> None:
    """Check 7：新序列 vs DB 其他基金（非同 family）相关系数 > 阈值 -> warn。

    须 >=24 月重叠才判。统计嫌疑非会计恒等式，warn + 人工确认，非 block。
    Coolabah 产品矩阵天然高相关（同 RBA 现金利率因子），0.98 阈值真实假阳性。
    """
    rows = conn.execute(
        "SELECT fund_id, date, net_return FROM monthly_returns WHERE fund_id != ?",
        (fund_id,),
    ).fetchall()
    if not rows:
        return

    other_funds: dict[str, dict[str, float]] = {}
    for r in rows:
        # 排除同 family（已在 B 组校验）
        if shareclass_prefix and r["fund_id"].startswith(shareclass_prefix):
            continue
        other_funds.setdefault(r["fund_id"], {})[r["date"]] = r["net_return"]

    net = {d: r for d, r in records}
    for other_id, other_net in other_funds.items():
        common = sorted(set(net) & set(other_net))
        if len(common) < 24:
            continue
        a = [net[d] for d in common]
        b = [other_net[d] for d in common]
        corr = _pearson(a, b)
        if corr is not None and abs(corr) > corr_threshold:
            errors_warn.append(
                f"Check 7 相关嫌疑: vs {other_id} 相关系数 {corr:.4f} > "
                f"{corr_threshold}（{len(common)} 月重叠，须人工确认）"
            )


def _pearson(a: list[float], b: list[float]) -> Optional[float]:
    """皮尔逊相关系数。常数序列返回 None。"""
    n = len(a)
    if n != len(b) or n == 0:
        return None
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = (sum((x - ma) ** 2 for x in a)) ** 0.5
    db = (sum((x - mb) ** 2 for x in b)) ** 0.5
    if da == 0 or db == 0:
        return None
    return num / (da * db)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd skills && python3 -m pytest tests/test_consistency_check.py -v`
Expected: all PASS (including D scenario)

- [ ] **Step 7: Commit**

```bash
cd skills && git add tests/fixtures/field_misaligned.json tests/test_consistency_check.py lib/consistency.py
git commit -m "feat(consistency): B-group cross-sequence checks 5/6 block + 7 correlation warn, repro bug fixture D"
```

---

### Task 4: Compound validation full-window + fixture E (first shareclass, DB empty)

**Files:**
- Create: `skills/tests/fixtures/first_shareclass.json`
- Create: `skills/tests/test_compound_validation.py`
- Modify: `skills/lib/consistency.py` (verify `_check_compound` handles short-series inception)

**Interfaces:**
- Consumes: `_check_compound` from Task 2, `consistency_check`.
- Produces: fixture E proves compound validation blocks even when DB has no siblings (A-group substitute works standalone).

- [ ] **Step 1: Create fixture E (`first_shareclass.json`)**

Institutional first to ingest, DB empty (no sibling), only PDF rolling provided. `net` = AusBond misaligned series (pa ~5%); rolling inception = 9.05% (true Institutional). Compound mismatch -> block. No sibling -> B-group skipped.

```json
{
  "fund_id": "coolabah_frhy_institutional",
  "shareclass_prefix": "coolabah_frhy_",
  "net": [
    ["2025-01-31", 0.0040], ["2025-02-28", 0.0041], ["2025-03-31", 0.0042],
    ["2025-04-30", 0.0040], ["2025-05-31", 0.0041], ["2025-06-30", 0.0042],
    ["2025-07-31", 0.0040], ["2025-08-31", 0.0041], ["2025-09-30", 0.0042],
    ["2025-10-31", 0.0040], ["2025-11-30", 0.0041], ["2025-12-31", 0.0042]
  ],
  "rolling": {
    "1mo": 0.0042, "3mo": 0.0125, "6mo": 0.0250, "12mo": 0.0507,
    "inception": 0.0905, "parse_error": false
  }
}
```

12 months of AusBond ~0.41%/mo -> 12mo compound ~0.0496 vs rolling 12mo 0.0507 (close, within 0.5%? 0.0496 vs 0.0507 = 0.0011 < 0.005 -> passes 12mo). inception: full 12-mo compound ~0.0496 vs rolling inception 0.0905 -> diff 0.0409 >> 0.005 -> inception block. Good.

- [ ] **Step 2: Write failing test (E scenario)**

```python
"""复利验证全窗口 + 第一次入库 DB 空场景（fixture E）。"""
from __future__ import annotations

import json
from pathlib import Path

from lib.consistency import consistency_check, _check_compound

FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_fixture_e_first_shareclass_compound_blocks(db_conn):
    """第一次入库 DB 空，无兄弟，复利验证独立拦截（不依赖 DB 兄弟）。"""
    f = _load("first_shareclass.json")
    # DB 空：无 sibling、无其他基金
    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn,
        shareclass_prefix=f["shareclass_prefix"],
        rolling=f["rolling"],
    )
    assert not ok, f"应 block，block={block}"
    # inception 复利失败（AusBond 12mo 复利 ~0.05 vs inception 0.0905）
    assert any("inception" in e and "复利" in e for e in block), \
        f"inception 复利缺失，block={block}"
    # 无兄弟 -> Check 5/6 不触发
    assert not any("Check 5" in e for e in block)
    assert not any("Check 6" in e for e in block)


def test_compound_passes_when_consistent(db_conn):
    """net 复利与 rolling 一致 -> compound 通过。"""
    f = _load("first_shareclass.json")
    # 构造 net 使 inception 复利 = rolling.inception 0.0905
    # 12 月均匀 r 使 (1+r)^12 - 1 = 0.0905 -> r = 0.0905^(1/12)... 用近似
    # 简单：直接用 rolling 12mo=0.0507 对应 12 月序列
    import math
    r12 = (1 + 0.0507) ** (1 / 12) - 1
    net = [[d, r12] for d, _ in f["net"]]
    rolling = {"1mo": r12, "3mo": (1 + r12) ** 3 - 1,
               "6mo": (1 + r12) ** 6 - 1, "12mo": 0.0507,
               "inception": 0.0507, "parse_error": False}
    ok, block, warn = consistency_check(
        f["fund_id"], net, db_conn, rolling=rolling,
    )
    assert ok, f"应 pass，block={block}"


def test_compound_missing_rolling_skips(db_conn):
    """rolling 缺失或 parse_error -> 复利验证跳过，不 fail。"""
    f = _load("first_shareclass.json")
    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn, rolling=None,
    )
    assert ok
    assert not any("复利" in e for e in block)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd skills && python3 -m pytest tests/test_compound_validation.py -v`
Expected: FAIL (fixture E not yet created -> FileNotFoundError, or compound logic gaps)

- [ ] **Step 4: Verify `_check_compound` inception logic (already in Task 2)**

The `_check_compound` from Task 2 already handles inception full-window. Verify the `parse_error` guard: `if rolling and not rolling.get("parse_error", True)`. If fixture E rolling has `parse_error: false`, compound runs. Confirm by re-reading `lib/consistency.py:_check_compound`. No code change needed if Task 2 impl correct; if inception check missing, add it.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd skills && python3 -m pytest tests/test_compound_validation.py -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
cd skills && git add tests/fixtures/first_shareclass.json tests/test_compound_validation.py lib/consistency.py
git commit -m "feat(consistency): full-window compound validation, fixture E first-shareclass DB-empty block"
```

---

### Task 5: Wire consistency into ingest + add_fund_from_plotly_html entry

**Files:**
- Modify: `skills/lib/ingest.py` (add `add_fund_from_plotly_html`, thread consistency into `add_fund`/`add_fund_from_html_table`)
- Modify: `skills/tests/test_ingest.py` (append plotly entry + consistency-block test)

**Interfaces:**
- Consumes: `consistency_check` (Task 2/3), `parse_plotly_nav_series` (Task 1), `gate_check`/`gate_check_table` (existing), `extract_perf_rolling` (existing).
- Produces: `add_fund_from_plotly_html(fund_id, name, plotly_html_path, *, confirmed_url, rolling_pdf_path, fund_name_pattern, shareclass_prefix=None, apir=None, url_type="plotly_report", fetch_method="html", verified_at=None, db_path=None) -> dict`. All three entry points: single-seq `gate_check` -> open conn -> `consistency_check` -> block on `errors_block` -> insert -> audit.

- [ ] **Step 1: Write failing test (plotly entry happy path + consistency block)**

Append to `skills/tests/test_ingest.py`:

```python
def test_add_fund_from_plotly_html_happy_path(tmp_path, monkeypatch):
    """Plotly HTML + PDF rolling -> 正确入库（compound 一致）。"""
    from lib.ingest import add_fund_from_plotly_html

    # 用 fixture A 的 HTML（5 月 Assisted）
    fix = Path(__file__).parent / "fixtures"
    html_path = tmp_path / "report.html"
    html_path.write_text((fix / "frhy_assisted.html").read_text(), encoding="utf-8")

    # 构造 PDF text 使 extract_perf_rolling 与 NAV 复利一致
    # Assisted NAV: 100->102.08 over 5 mo -> inception = 0.0208
    pdf_text = (
        "Performance\n1 month 3 months 6 months 12 months since inception\n"
        "0.0051 0.0153 0.0208 0.0208 0.0208\n"
    )
    pdf_path = tmp_path / "rolling.pdf"
    pdf_path.write_text(pdf_text, encoding="utf-8")

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FUND_DB_WRITE_TOKEN", "test")
    result = add_fund_from_plotly_html(
        "test_plotly_assisted", "Test Plotly Assisted", str(html_path),
        confirmed_url="http://x", rolling_pdf_path=str(pdf_path),
        fund_name_pattern="Assisted",
        shareclass_prefix="test_plotly_", db_path=str(db_path),
    )
    assert result["gate_pass"], result["errors"]
    assert result["months"] == 4  # 5 NAV -> 4 monthly returns


def test_add_fund_from_plotly_html_compound_mismatch_blocks(tmp_path, monkeypatch):
    """Plotly NAV 与 rolling 不一致 -> consistency block，不入库。"""
    from lib.ingest import add_fund_from_plotly_html

    fix = Path(__file__).parent / "fixtures"
    html_path = tmp_path / "report.html"
    html_path.write_text((fix / "frhy_assisted.html").read_text(), encoding="utf-8")

    # rolling inception 故意写 0.50（与 NAV 复利 0.0208 严重不符）
    pdf_text = (
        "Performance\n1 month 3 months 6 months 12 months since inception\n"
        "0.0051 0.0153 0.0208 0.0208 0.50\n"
    )
    pdf_path = tmp_path / "rolling.pdf"
    pdf_path.write_text(pdf_text, encoding="utf-8")

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FUND_DB_WRITE_TOKEN", "test")
    result = add_fund_from_plotly_html(
        "test_plotly_bad", "Test Plotly Bad", str(html_path),
        confirmed_url="http://x", rolling_pdf_path=str(pdf_path),
        fund_name_pattern="Assisted",
        shareclass_prefix="test_plotly_", db_path=str(db_path),
    )
    assert not result["gate_pass"]
    assert any("复利" in e for e in result["errors"])
    # DB 未写入
    from lib.db import get_connection, ensure_tables
    conn = get_connection(str(db_path)); ensure_tables(conn)
    cnt = conn.execute(
        "SELECT COUNT(*) FROM monthly_returns WHERE fund_id=?",
        ("test_plotly_bad",),
    ).fetchone()[0]
    conn.close()
    assert cnt == 0
```

Add `from pathlib import Path` at top of `test_ingest.py` if not present.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills && python3 -m pytest tests/test_ingest.py::test_add_fund_from_plotly_html_happy_path -v`
Expected: FAIL with `ImportError: cannot import name 'add_fund_from_plotly_html'`

- [ ] **Step 3: Implement `add_fund_from_plotly_html` + thread consistency into all entries**

Modify `skills/lib/ingest.py`. Add imports at top (after existing `from lib.extract import ...`):

```python
from lib.extract import (
    download_and_extract_parallel,
    extract_pdf_links_from_archive,
    extract_perf_rolling,
    gate_check,
    gate_check_table,
    get_last_day_of_month,
    parse_html_monthly_table,
    parse_pdf_text,
    parse_plotly_nav_series,
)
from lib.consistency import consistency_check
from lib.audit import audit_all_funds
```

Refactor `add_fund` and `add_fund_from_html_table`: after single-seq `gate_check` passes, open conn, run `consistency_check`, block on errors_block. In `add_fund` (replace step 5-6, lines ~100-123):

```python
    # 5. gate_check（单序列硬 gate）
    pass_ok, errors = gate_check(records, rolling_per_month)
    if not pass_ok:
        return {"months": len(records), "start": None, "end": None,
                "gaps": [], "gate_pass": False, "errors": errors,
                "failed_months": failed_months,
                "short_history_warning": len(records) < 36}

    # 5.5 consistency_check（跨序列 + 复利全窗口）
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)
        # 取最新月 rolling 供 consistency compound 用
        latest_ym = max(d[:7] for d, _ in records) if records else None
        latest_rolling = rolling_per_month.get(latest_ym, {}) if latest_ym else {}
        c_ok, c_block, c_warn = consistency_check(
            fund_id, records, conn, rolling=latest_rolling,
        )
        if not c_ok:
            return {"months": len(records), "start": None, "end": None,
                    "gaps": [], "gate_pass": False,
                    "errors": errors + c_block + [f"[warn] {w}" for w in c_warn],
                    "failed_months": failed_months,
                    "short_history_warning": len(records) < 36}
        create_fund(
            conn, fund_id=fund_id, fund_name=name,
            confirmed_url=confirmed_url, fetch_method=fetch_method,
            url_type=url_type, apir_code=apir, verified_at=verified_at,
        )
        for date, net_return in records:
            upsert_monthly_return(
                conn, fund_id=fund_id, date=date,
                net_return=net_return, commentary_truth=net_return,
            )
    finally:
        conn.close()
```

Apply the same `consistency_check` insertion to `add_fund_from_html_table` (after `gate_check_table`, before insert). For table source, `rolling=None` (no per-month rolling; YTD already validated in gate_check_table).

Add the new entry at end of `ingest.py` (before `_cli`):

```python
def add_fund_from_plotly_html(
    fund_id: str,
    name: str,
    plotly_html_path: str,
    *,
    confirmed_url: str,
    rolling_pdf_path: str,
    fund_name_pattern: str,
    shareclass_prefix: Optional[str] = None,
    apir: Optional[str] = None,
    url_type: str = "plotly_report",
    fetch_method: str = "html",
    verified_at: Optional[str] = None,
    db_path: Optional[str] = None,
) -> dict:
    """Plotly HTML 报告源全自动流水线（Coolabah 模式）。

    parse_plotly_nav_series 按 name 提基金类 NAV -> 月度收益 = nav_t/nav_{t-1}-1
    -> PDF rolling 复利全窗口验证 -> gate_check 单序列 -> consistency_check
    跨序列 -> 入库。NAV 是 net-of-fees total return（含分红再投）。
    """
    with open(plotly_html_path, "r", encoding="utf-8") as f:
        html = f.read()
    nav_series = parse_plotly_nav_series(html, fund_name_pattern)

    # NAV -> 月度收益
    nav_series.sort(key=lambda x: x[0])
    records: list[tuple[str, float]] = []
    for i in range(1, len(nav_series)):
        prev_d, prev_nav = nav_series[i - 1]
        d, nav = nav_series[i]
        r = nav / prev_nav - 1.0
        records.append((d, r))

    # PDF rolling
    pdf_text = parse_pdf_text(rolling_pdf_path)
    rolling = extract_perf_rolling(pdf_text)

    # 单序列 gate
    pass_ok, errors = gate_check(records, {})
    if not pass_ok:
        return {"months": len(records), "start": None, "end": None,
                "gaps": [], "gate_pass": False, "errors": errors,
                "failed_months": [], "short_history_warning": len(records) < 36}

    # consistency（含复利全窗口）
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)
        c_ok, c_block, c_warn = consistency_check(
            fund_id, records, conn,
            shareclass_prefix=shareclass_prefix, rolling=rolling,
        )
        if not c_ok:
            return {"months": len(records), "start": None, "end": None,
                    "gaps": [], "gate_pass": False,
                    "errors": errors + c_block + [f"[warn] {w}" for w in c_warn],
                    "failed_months": [],
                    "short_history_warning": len(records) < 36}
        create_fund(
            conn, fund_id=fund_id, fund_name=name,
            confirmed_url=confirmed_url, fetch_method=fetch_method,
            url_type=url_type, apir_code=apir, verified_at=verified_at,
        )
        for date, net_return in records:
            upsert_monthly_return(
                conn, fund_id=fund_id, date=date,
                net_return=net_return, commentary_truth=net_return,
            )
        # 入库后自动 audit
        audit_all_funds(conn)
    finally:
        conn.close()

    records_sorted = sorted(records, key=lambda x: x[0])
    return {
        "months": len(records_sorted),
        "start": records_sorted[0][0] if records_sorted else None,
        "end": records_sorted[-1][0] if records_sorted else None,
        "gaps": [], "gate_pass": True, "errors": [],
        "failed_months": [], "short_history_warning": len(records_sorted) < 36,
    }
```

Also add `audit_all_funds(conn)` call at end of `add_fund` and `add_fund_from_html_table` insert blocks (before `finally: conn.close()`).

- [ ] **Step 4: Add CLI subcommand `add-plotly`**

In `_cli()`, add parser:

```python
    pl_p = sub.add_parser("add-plotly", help="新增基金入库（Plotly HTML + PDF rolling，Coolabah 模式）")
    pl_p.add_argument("--fund-id", required=True)
    pl_p.add_argument("--name", required=True)
    pl_p.add_argument("--plotly-html", required=True, help="Plotly HTML 报告路径")
    pl_p.add_argument("--rolling-pdf", required=True, help="PDF rolling 报告路径")
    pl_p.add_argument("--fund-name-pattern", required=True, help="份额类 name 过滤模式")
    pl_p.add_argument("--shareclass-prefix", default=None)
    pl_p.add_argument("--confirmed-url", required=True)
    pl_p.add_argument("--apir", default=None)
    pl_p.add_argument("--verified-at", default=None)
```

And handler:
```python
    if args.command == "add-plotly":
        result = add_fund_from_plotly_html(
            args.fund_id, args.name, args.plotly_html,
            confirmed_url=args.confirmed_url,
            rolling_pdf_path=args.rolling_pdf,
            fund_name_pattern=args.fund_name_pattern,
            shareclass_prefix=args.shareclass_prefix,
            apir=args.apir, verified_at=args.verified_at,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["gate_pass"] else 1
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd skills && python3 -m pytest tests/test_ingest.py -v`
Expected: PASS (plotly happy path + compound mismatch block). Note: `lib.audit` import -- audit is Task 7; to avoid forward dep, create a minimal `lib/audit.py` stub now (returns `{}`), Task 7 fills it.

Create `skills/lib/audit.py` stub:
```python
"""批量一致性审计（Task 7 实现）。"""
from __future__ import annotations
import sqlite3


def audit_all_funds(conn: sqlite3.Connection) -> dict:
    """占位，Task 7 填充。"""
    return {}
```

- [ ] **Step 6: Commit**

```bash
cd skills && git add lib/ingest.py lib/audit.py tests/test_ingest.py
git commit -m "feat(ingest): add_fund_from_plotly_html + thread consistency_check into all entries"
```

---

### Task 6: DB write token soft isolation

**Files:**
- Modify: `skills/lib/db.py` (`upsert_monthly_return`, `create_fund`, any other write ops)
- Modify: `skills/lib/ingest.py` (`_cli` sets token from `--write-token` arg or env)
- Create: `skills/tests/test_db_write_token.py`

**Interfaces:**
- Produces: `lib.db` write ops raise `PermissionError` if `FUND_DB_WRITE_TOKEN` env unset. `lib.ingest._cli` accepts `--write-token` and sets env before calling entry points. Read ops (`get_connection`, `ensure_tables`, `get_fund`, `recompute_nav` -- recompute is a write, gate it too) unaffected; only INSERT/UPDATE/DELETE gated.

- [ ] **Step 1: Write failing test**

```python
"""lib.db 写操作 token 软隔离测试。"""
from __future__ import annotations

import pytest

from lib.db import create_fund, ensure_tables, get_connection, upsert_monthly_return


def test_write_without_token_raises(db_conn, monkeypatch):
    monkeypatch.delenv("FUND_DB_WRITE_TOKEN", raising=False)
    with pytest.raises(PermissionError, match="FUND_DB_WRITE_TOKEN"):
        create_fund(
            db_conn, fund_id="x", fund_name="X",
            confirmed_url="http://x", fetch_method="pdf", url_type="t",
        )


def test_write_with_token_succeeds(db_conn, monkeypatch):
    monkeypatch.setenv("FUND_DB_WRITE_TOKEN", "test")
    create_fund(
        db_conn, fund_id="x", fund_name="X",
        confirmed_url="http://x", fetch_method="pdf", url_type="t",
    )
    row = db_conn.execute("SELECT fund_id FROM funds WHERE fund_id=?", ("x",)).fetchone()
    assert row["fund_id"] == "x"


def test_upsert_without_token_raises(db_conn, monkeypatch):
    monkeypatch.setenv("FUND_DB_WRITE_TOKEN", "test")
    create_fund(
        db_conn, fund_id="x", fund_name="X",
        confirmed_url="http://x", fetch_method="pdf", url_type="t",
    )
    monkeypatch.delenv("FUND_DB_WRITE_TOKEN", raising=False)
    with pytest.raises(PermissionError):
        upsert_monthly_return(
            db_conn, fund_id="x", date="2024-01-31", net_return=0.01,
        )


def test_read_ops_not_gated(db_conn, monkeypatch):
    """get_connection / ensure_tables 不需 token。"""
    monkeypatch.delenv("FUND_DB_WRITE_TOKEN", raising=False)
    conn = get_connection()
    ensure_tables(conn)  # CREATE TABLE IF NOT EXISTS，幂等读性质，不 gate
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills && python3 -m pytest tests/test_db_write_token.py -v`
Expected: FAIL (no token gate yet)

- [ ] **Step 3: Add token gate to `lib/db.py`**

At top of `lib/db.py` (after imports), add:

```python
import os


def _require_write_token() -> None:
    """写操作凭证检查：FUND_DB_WRITE_TOKEN 未设 -> PermissionError。

    软隔离：提高越权门槛（agent bash 内联无 token 失败）。绝对隔离须 harness
    sandbox（agent bash 与主对话 DB 写权限完全隔离），超出 skills 代码层。
    """
    if not os.environ.get("FUND_DB_WRITE_TOKEN"):
        raise PermissionError(
            "DB 写操作需要 FUND_DB_WRITE_TOKEN 环境变量（越权防护软隔离）。"
            "主对话跑 ingest.py 时由 _cli 注入；探测 agent bash 不继承。"
        )
```

Add `_require_write_token()` as first line inside `create_fund`, `upsert_monthly_return`, `recompute_nav`. Do NOT add to `get_connection`/`ensure_tables`/`get_fund` (reads).

- [ ] **Step 4: Add `--write-token` to `_cli` and inject env**

In `lib/ingest.py::_cli`, add to top-level parser (before subparsers):
```python
    parser.add_argument("--write-token", default=None,
                        help="DB 写凭证（或从 FUND_DB_WRITE_TOKEN 环境读）")
```
After `args = parser.parse_args()`, add:
```python
    if args.write_token:
        os.environ["FUND_DB_WRITE_TOKEN"] = args.write_token
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd skills && python3 -m pytest tests/test_db_write_token.py -v`
Expected: 4 PASS. Then run full suite to confirm no regression: `cd skills && python3 -m pytest tests/ -v` -- existing tests that call `create_fund`/`upsert_monthly_return` directly will now need token. Audit existing tests: `test_db.py`, `test_ingest.py` call write ops -- add `monkeypatch.setenv("FUND_DB_WRITE_TOKEN", "test")` to those fixtures, or add a session-scoped autouse fixture in `conftest.py`:

In `conftest.py`, add:
```python
@pytest.fixture(autouse=True)
def _write_token(monkeypatch):
    """所有测试默认带写凭证（避免每个测试显式 setenv）。"""
    monkeypatch.setenv("FUND_DB_WRITE_TOKEN", "test")
```

Then `test_db_write_token.py` tests that need token-absent use `monkeypatch.delenv` (already do).

- [ ] **Step 6: Run full suite**

Run: `cd skills && python3 -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
cd skills && git add lib/db.py lib/ingest.py tests/test_db_write_token.py tests/conftest.py
git commit -m "feat(db): FUND_DB_WRITE_TOKEN soft isolation on write ops + conftest autouse fixture"
```

---

### Task 7: lib/audit.py batch scan + post-ingest auto-trigger

**Files:**
- Modify: `skills/lib/audit.py` (replace stub)
- Create: `skills/tests/test_audit.py`

**Interfaces:**
- Consumes: `consistency_check` (Task 2/3), DB rows.
- Produces: `audit_all_funds(conn) -> dict` returns `{"fund_id": {"errors_block": [...], "errors_warn": [...]}, "suspect_pairs": [...]}`. Scans every fund; for each, re-runs consistency_check against its stored records + siblings. Writes report to `docs/superpowers/audits/YYYY-MM-DD-consistency-audit.md`.

- [ ] **Step 1: Write failing test**

```python
"""lib.audit 批量扫描 + 入库后自动触发测试。"""
from __future__ import annotations

from lib.audit import audit_all_funds
from lib.db import create_fund, upsert_monthly_return


def _seed(conn, fid, rets, name=None):
    create_fund(conn, fund_id=fund, fund_name=name or fid,
                confirmed_url="http://x", fetch_method="pdf", url_type="t")
    for d, r in rets:
        upsert_monthly_return(conn, fund_id=fid, date=d, net_return=r)


def test_audit_clean_db_returns_empty(db_conn):
    report = audit_all_funds(db_conn)
    assert report["fund_reports"] == {}
    assert report["suspect_pairs"] == []


def test_audit_detects_misaligned_via_sibling(db_conn):
    """两 share class 差值大 -> audit 报 Check 6。"""
    _seed(db_conn, "coolabah_frhy_assisted",
          [["2025-01-31", 0.0072], ["2025-02-28", 0.0073]], name="A")
    _seed(db_conn, "coolabah_frhy_institutional",
          [["2025-01-31", 0.0040], ["2025-02-28", 0.0041]], name="I")
    report = audit_all_funds(db_conn)
    inst = report["fund_reports"].get("coolabah_frhy_institutional", {})
    assert any("Check 6" in e for e in inst.get("errors_block", []))


def test_audit_writes_report_file(db_conn, tmp_path, monkeypatch):
    _seed(db_conn, "solo_fund", [["2025-01-31", 0.005]], name="S")
    monkeypatch.setenv("FUND_AUDIT_DIR", str(tmp_path))
    audit_all_funds(db_conn)
    import os
    files = list(tmp_path.glob("*-consistency-audit.md"))
    assert len(files) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills && python3 -m pytest tests/test_audit.py -v`
Expected: FAIL (stub returns `{}`)

- [ ] **Step 3: Implement `audit_all_funds`**

Replace `skills/lib/audit.py`:

```python
"""批量一致性审计：扫所有已入库基金，找静默字段错位存量。

入库后自动触发（ingest 入口末尾调 audit_all_funds）。跨份额类（5/6）按 fund_id
前缀分组组内互校验；7 所有基金两两相关 >0.98 报嫌疑对（warn）；复利验证需
rolling（audit 无 rolling 时跳过 compound）。报告写 docs/superpowers/audits/。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from lib.consistency import consistency_check


def _fund_prefix(fund_id: str) -> Optional[str]:
    """coolabah_frhy_assisted -> coolabah_frhy_；无下划线 -> None。"""
    idx = fund_id.rfind("_")
    if idx < 0:
        return None
    return fund_id[: idx + 1]


def audit_all_funds(conn: sqlite3.Connection) -> dict:
    """批量跑 consistency_check 扫所有基金。

    返回 {"fund_reports": {fund_id: {"errors_block":[...],"errors_warn":[...]}},
    "suspect_pairs": [(f1, f2, corr), ...]}。
    """
    fund_rows = conn.execute(
        "SELECT DISTINCT fund_id FROM monthly_returns ORDER BY fund_id"
    ).fetchall()
    fund_ids = [r["fund_id"] for r in fund_rows]

    fund_reports: dict = {}
    for fid in fund_ids:
        rec_rows = conn.execute(
            "SELECT date, net_return FROM monthly_returns WHERE fund_id=? "
            "ORDER BY date",
            (fid,),
        ).fetchall()
        records = [(r["date"], r["net_return"]) for r in rec_rows]
        prefix = _fund_prefix(fid)
        ok, block, warn = consistency_check(
            fid, records, conn, shareclass_prefix=prefix,
        )
        if block or warn:
            fund_reports[fid] = {
                "errors_block": block, "errors_warn": warn,
            }

    suspect_pairs = _suspect_correlation_pairs(conn, fund_ids)
    _write_report(conn, fund_ids, fund_reports, suspect_pairs)
    return {"fund_reports": fund_reports, "suspect_pairs": suspect_pairs}


def _suspect_correlation_pairs(
    conn: sqlite3.Connection, fund_ids: list[str]
) -> list[tuple]:
    """所有基金两两相关 >0.98（>=24 月重叠）报嫌疑对。"""
    series = {}
    for fid in fund_ids:
        rows = conn.execute(
            "SELECT date, net_return FROM monthly_returns WHERE fund_id=?",
            (fid,),
        ).fetchall()
        series[fid] = {r["date"]: r["net_return"] for r in rows}

    pairs = []
    for i, f1 in enumerate(fund_ids):
        for f2 in fund_ids[i + 1:]:
            common = sorted(set(series[f1]) & set(series[f2]))
            if len(common) < 24:
                continue
            a = [series[f1][d] for d in common]
            b = [series[f2][d] for d in common]
            corr = _pearson(a, b)
            if corr is not None and abs(corr) > 0.98:
                pairs.append((f1, f2, round(corr, 4)))
    return pairs


def _pearson(a: list[float], b: list[float]) -> Optional[float]:
    n = len(a)
    if n != len(b) or n == 0:
        return None
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = (sum((x - ma) ** 2 for x in a)) ** 0.5
    db = (sum((x - mb) ** 2 for x in b)) ** 0.5
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def _write_report(
    conn: sqlite3.Connection,
    fund_ids: list[str],
    fund_reports: dict,
    suspect_pairs: list[tuple],
) -> None:
    audit_dir = os.environ.get("FUND_AUDIT_DIR")
    if audit_dir is None:
        # 默认仓库根 docs/superpowers/audits/
        here = Path(__file__).resolve().parent.parent
        audit_dir = here.parent / "docs" / "superpowers" / "audits"
    audit_dir = Path(audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    # 用最后一个基金的 max date 拼时间戳（避免 Date.now）
    today = datetime.utcnow().strftime("%Y-%m-%d")
    path = audit_dir / f"{today}-consistency-audit.md"

    lines = [f"# Consistency Audit {today}", ""]
    lines.append(f"## 扫描基金数: {len(fund_ids)}")
    lines.append("")
    if not fund_reports and not suspect_pairs:
        lines.append("无 block/warn 问题。")
    else:
        for fid, rep in fund_reports.items():
            lines.append(f"### {fid}")
            for e in rep["errors_block"]:
                lines.append(f"- [block] {e}")
            for e in rep["errors_warn"]:
                lines.append(f"- [warn] {e}")
            lines.append("")
        if suspect_pairs:
            lines.append("## 嫌疑相关对（warn）")
            for f1, f2, c in suspect_pairs:
                lines.append(f"- {f1} vs {f2}: corr={c}")
    path.write_text("\n".join(lines), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd skills && python3 -m pytest tests/test_audit.py -v`
Expected: 3 PASS

- [ ] **Step 5: Run real DB audit to confirm no existing misalignment**

Run: `cd skills && FUND_DB_WRITE_TOKEN=audit python3 -c "
from lib.db import get_connection, ensure_tables
from lib.audit import audit_all_funds
conn = get_connection(); ensure_tables(conn)
print(audit_all_funds(conn))
conn.close()
"`
Expected: `{'fund_reports': {}, 'suspect_pairs': [...]}` -- coolabah_frhy 两类已修正，无 block；可能 warn 嫌疑对（FRHY 两类高相关，预期 warn 非 block）。Confirm no `errors_block`.

- [ ] **Step 6: Commit**

```bash
cd skills && git add lib/audit.py tests/test_audit.py
git commit -m "feat(audit): batch consistency scan all funds + post-ingest auto-trigger + report"
```

---

### Task 8: Header-parsing regression fixture C (column shuffle)

**Files:**
- Create: `skills/tests/fixtures/columns_shuffled.md`
- Create: `skills/tests/test_header_parsing.py`

**Interfaces:**
- Consumes: `parse_html_monthly_table` (existing), `extract_perf_rolling` (existing).
- Produces: regression test proving header-text localization (not column index) survives column reorder. Position-index fallback emits explicit warning.

- [ ] **Step 1: Create fixture C (`columns_shuffled.md`)**

Synthetic fundmonitors-style table with YTD column moved before Jan (shuffled vs normal `| Year | Jan | ... | Dec | YTD |`):

```markdown
# Fund Profile

## Historical Performance

| Year | YTD | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|------|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|
| 2024 | 1.50% | 0.40% | 0.30% | 0.50% | 0.20% | 0.10% | 0.00% | -0.10% | 0.20% | 0.30% | 0.40% | 0.10% | 0.00% |
| 2025 | 0.90% | 0.50% | 0.40% | 0.00% | N/R | N/R | N/R | N/R | N/R | N/R | N/R | N/R | N/R |

## Historical Financial Year Performance

| Year | YTD | Jul | Aug | Sep |
|------|-----|-----|-----|-----|
| 2024 | 0.50% | 0.10% | 0.20% | 0.20% |
```

- [ ] **Step 2: Write failing-then-passing regression test**

```python
"""表头文字定位回归：列序打乱不影响解析（非位置索引）。"""
from __future__ import annotations

from pathlib import Path

from lib.extract import parse_html_monthly_table

FIX = Path(__file__).parent / "fixtures"


def test_shuffled_columns_parsed_by_header_not_position():
    md = (FIX / "columns_shuffled.md").read_text(encoding="utf-8")
    records, ytd_map = parse_html_monthly_table(md)
    # 2024 全 12 月 + 2025 前 3 月
    assert len(records) == 15
    # 2024-01 = 0.40%
    jan = [r for d, r in records if d.startswith("2024-01")]
    assert len(jan) == 1
    assert abs(jan[0] - 0.0040) < 1e-9
    # 负号捕获：2024-07 = -0.10%
    jul = [r for d, r in records if d.startswith("2024-07")]
    assert abs(jul[0] - (-0.0010)) < 1e-9
    # YTD map 正确（YTD 列在 Jan 前，靠表头定位仍取对）
    assert abs(ytd_map["2024"] - 0.0150) < 1e-9
    assert abs(ytd_map["2025"] - 0.0090) < 1e-9


def test_negative_sign_captured():
    md = (FIX / "columns_shuffled.md").read_text(encoding="utf-8")
    records, _ = parse_html_monthly_table(md)
    negs = [r for _, r in records if r < 0]
    assert negs == [-0.0010]


def test_nr_skipped_not_gap():
    md = (FIX / "columns_shuffled.md").read_text(encoding="utf-8")
    records, _ = parse_html_monthly_table(md)
    # 2025-04 起 N/R 跳过，2025 只 3 月
    y2025 = [d for d, _ in records if d.startswith("2025")]
    assert len(y2025) == 3
```

- [ ] **Step 3: Run test**

Run: `cd skills && python3 -m pytest tests/test_header_parsing.py -v`

If PASS (existing `parse_html_monthly_table` already header-based): commit. If FAIL (positional bug surfaces): fix `parse_html_monthly_table` to localize columns by header text (`Jan`/`Feb`/.../`Dec`/`YTD`), not by index. Expected: PASS (spec confirms already header-based; this is regression lock).

- [ ] **Step 4: Commit**

```bash
cd skills && git add tests/fixtures/columns_shuffled.md tests/test_header_parsing.py
git commit -m "test(extract): lock header-text column localization against shuffle regression (fixture C)"
```

---

### Task 9: add_fixed_fund skill probe step -> read-only agent

**Files:**
- Modify: `skills/.claude/skills/add_fixed_fund.md` (probe step wording)

**Interfaces:**
- Produces: skill doc mandates probe subagent use `cavecrew-investigator` (read-only: Read/Grep/Glob/Bash, no Write/Edit), returns JSON discovery result, must NOT call `lib.db`/`lib.ingest` write ops. Main thread verifies DB row count unchanged before/after probe.

- [ ] **Step 1: Edit skill doc**

In `skills/.claude/skills/add_fixed_fund.md`, section "### 3. 主会话顺序探测" -- the heading already says "不委派子 agent". Spec requires probe step use read-only agent. Reconcile: the current design has main session do probing directly. Spec layer-2 wants read-only agent. Add a note that IF a probe subagent is dispatched (e.g. for parallel source exploration), it MUST be `cavecrew-investigator` (read-only) and main thread verifies DB row count before/after.

Append to section 3 (after "### 3. 主会话顺序探测 + 抓取" heading block, before "步骤 1"):

```markdown
**探测子 agent 越权防护（2026-07-13 字段错位教训）**：
- 若主会话派探测子 agent（并行探多源时），**必须**用 `cavecrew-investigator`（read-only：Read/Grep/Glob/Bash，无 Write/Edit），禁止用有写权限的 agent 类型。
- 子 agent prompt 必须明确：仅返回 JSON 探测结果（候选 URL、PDF 是否含逐月表、付费墙状态），**禁止**调 `lib.db`/`lib.ingest` 任何写操作（`create_fund`/`upsert_monthly_return`/`add_fund*`），**禁止**写 .py 脚本调 lib.db。
- 主会话派子 agent 前后各跑一次 `SELECT COUNT(*) FROM funds` + `SELECT COUNT(*) FROM monthly_returns`，行数变了说明子 agent 越权写库，立即回滚并报错。
- 入库一律由主会话跑 `python3 -m lib.ingest`（带 `FUND_DB_WRITE_TOKEN`），探测 agent bash 不继承 token，越权写库 raise PermissionError。
```

- [ ] **Step 2: Commit**

```bash
cd skills && git add .claude/skills/add_fixed_fund.md
git commit -m "docs(skill): probe subagent read-only enforcement + DB row-count verify (field-misalignment guard)"
```

---

## Self-Review (run after writing plan)

**1. Spec coverage:**
- parse_plotly_nav_series (zero/multi raise, benchmark drop) -> Task 1 ✓
- A-group 1/2/3/4 -> Task 2 ✓
- B-group 5/6 block + 7 warn -> Task 3 ✓
- Compound validation full-window (1mo/1yr/inception) -> Task 2 `_check_compound` + Task 4 fixture E ✓
- fixture A/B (Plotly) -> Task 1 ✓
- fixture C (column shuffle) -> Task 8 ✓
- fixture D (reproduce bug) -> Task 3 ✓
- fixture E (first shareclass DB empty) -> Task 4 ✓
- consistency_check signature (pass, errors_block, errors_warn) -> Task 2 ✓
- gate_check/gate_check_table 末尾调 consistency (wired in ingest entry points) -> Task 5 ✓
- token soft isolation (FUND_DB_WRITE_TOKEN) -> Task 6 ✓
- read-only probe agent -> Task 9 ✓
- audit auto-run post-ingest -> Task 5 (call) + Task 7 (impl) ✓
- add_fund_from_plotly_html -> Task 5 ✓
- no `if fund_id ==` special-case -> all tasks generic ✓
- Check 7 warn not block -> Task 3 ✓
- Check 6 performance-fee doc -> Task 3 docstring ✓

**2. Placeholder scan:** No TBD/TODO. All code blocks complete. fixture D/E JSON values concrete. Audit report path uses env override.

**3. Type consistency:**
- `consistency_check` returns `tuple[bool, list[str], list[str]]` everywhere (Task 2 def, Task 3/4/7 callers) ✓
- `parse_plotly_nav_series` returns `list[tuple[str, float]]` (Task 1 def, Task 5 caller) ✓
- `audit_all_funds(conn) -> dict` (Task 5 caller matches Task 7 impl) ✓
- `add_fund_from_plotly_html` signature (Task 5 def + CLI handler) ✓
- `_check_compound`/`_check_bgroup`/`_check_correlation` internal helpers (Task 2 stubs -> Task 3 impl) ✓

No gaps found. Plan complete.
