# Task 5 Report: 【漏洞 10.2】Wayback 入口收窄 —— 不再按整个发行商域名无差别抓

(Note: this filename previously held a stale report from an unrelated older
plan about `calculate_autocorrelation`/Geltner unsmoothing — overwritten
here with the correct Task 5 for the current `feat/grok-search-engine` /
Spec G plan, same situation task-4-report.md flagged for its own file.)

## Note on session start state

When I started this task, `llm_ingest/discover.py` and `tests/test_discover.py`
already had uncommitted working-tree changes matching the brief's Step 1-4
verbatim (visible in `git status`/`git diff` before I made any edit myself —
presumably from an interrupted prior session). I verified the existing diff
against the brief line-by-line rather than reapplying blindly, confirmed it
matched exactly (signature, both filter passes, call-site update, and the
fixes to the two pre-existing `TestProbeL2` tests + `TestRunDiscovery`'s
`fake_probe_l2` signature), ran the full TDD verification loop myself, and
committed.

## What I Implemented / Verified

1. `probe_l2_wayback(issuer_domain, gap_set, fund_name)` in
   `llm_ingest/discover.py` (~line 682-758): added required third parameter
   `fund_name`. Rewrote the filter to a two-pass design:
   - Pass 1: collect candidates that pass `_NON_MONTHLY_HINTS.search(fname)`
     (excludes PDS/TMD/FSG/research-report filenames) and whose parsed `ym`
     falls in `gap_set`.
   - Pass 2: `keep = set(_best_match_pdfs([...], fund_name))` (imported from
     `.discover2`, the Task 4 helper) — relative match-score filtering
     (tied-for-max token overlap with `fund_name`), not an absolute
     `_pdf_slug_match_count > 0` check. This matters because absolute
     token-intersection > 0 does not distinguish sibling funds sharing
     tokens (e.g. "Yarra Enhanced Income" vs "Yarra Australian Income" both
     score > 0 against overlapping tokens like "yarra"/"income"; only the
     relative max separates them: 3 vs 2, so only the target survives).
   - `CDX_SNAPSHOTS_PER_MONTH` cap and `_dedup_links` applied after both
     filters, on the kept set only.
2. Call site in `run_discovery` (~line 924): `probe_l2_wayback(dom_clean,
   gap_set, fund_name)` — `fund_name` was already a parameter of the
   enclosing `run_discovery` function, so no additional plumbing needed.
3. `tests/test_discover.py`: added `TestWaybackNarrowing` with the brief's
   two tests verbatim (`test_sibling_fund_pdf_not_used_to_fill_gap`,
   `test_pds_tmd_not_used_to_fill_gap`). Also updated the two pre-existing
   `TestProbeL2` short-circuit tests (`test_no_gap_returns_empty`,
   `test_no_domain_returns_empty`) to pass a `fund_name` positional arg, and
   `TestRunDiscovery`'s `fake_probe_l2(domain, gap_set)` monkeypatch stub to
   `fake_probe_l2(domain, gap_set, fund_name)` to match the new 3-arg
   signature.

## Call-site audit (brief only named ~885 行; checked for others)

```
grep -rn "probe_l2_wayback" --include="*.py" .
```
Only one production call site: `llm_ingest/discover.py:924` (inside
`run_discovery`), already updated. All other hits are in
`tests/test_discover.py` (import, 2 short-circuit tests, 3
`monkeypatch.setattr` stubs in `TestRunDiscovery`, 2 direct calls in the new
`TestWaybackNarrowing`). No additional production call site exists outside
`discover.py`; no other file needed touching.

## RED

Not separately reproduced live since the fix was already present at session
start; the brief's documented RED (`TypeError: probe_l2_wayback() takes 2
positional arguments but 3 were given`) is self-evidently what the old 2-arg
signature would have produced against the new 3-arg test calls — confirmed
via `git diff` that the pre-change code had no third parameter and the old
call site passed only 2 args.

## GREEN

```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover.py', '-v']))"
```
Result: `56 passed, 6 warnings in 0.13s` — includes both new
`TestWaybackNarrowing` tests and both updated `TestProbeL2` tests, plus all
other pre-existing tests in the file (`TestRunDiscovery`, etc.) still pass.

```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/', '-q', '--no-header']))"
```
Result: `2 failed, 357 passed, 6 warnings in 152.53s` — the 2 failures are
exactly the two known pre-existing failures unrelated to this change:
- `tests/test_extract_html.py::test_plotly_shrink_hovertext_anchor_takes_narrow_window`
- `tests/test_spec_b_wipe_script.py::test_dry_run_lists_targets`

No new regressions.

## Files Changed

- `llm_ingest/discover.py`: `probe_l2_wayback` signature + two-pass filter
  body (~lines 682-758); call site update in `run_discovery` (~line 924).
- `tests/test_discover.py`: `+TestWaybackNarrowing` class; updated 2
  `TestProbeL2` calls and `TestRunDiscovery.fake_probe_l2` for the new
  3-arg signature.

Commit: `b74007d` — `fix(discover): Wayback 补缺口加基金名与文档类型过滤 (Spec G 10.2)`

## Self-Review Findings

- `probe_l2_wayback` requires `fund_name` as its third positional parameter
  — confirmed in signature and at the sole call site.
- Both filters are applied and in the documented order: (a)
  `_NON_MONTHLY_HINTS` during candidate collection (pass 1), (b)
  `_best_match_pdfs` (relative, tied-for-max — not absolute
  `_pdf_slug_match_count > 0`) during the keep-set computation (pass 2).
  Confirmed `_best_match_pdfs` in `llm_ingest/discover2.py:155-174` uses
  `best = max(...)` then `s == best`, i.e. genuinely relative.
- Call site at `run_discovery` (~line 924) updated; `fund_name` was already
  in scope as a `run_discovery` parameter, no additional plumbing required.
- Grepped for other callers beyond the named one — none found in production
  code; only test file references (already accounted for).
- Existing wayback-adjacent tests (`TestProbeL2`, `TestRunDiscovery`) fixed
  for the new signature and pass.
- Full suite shows exactly the 2 known pre-existing failures, no new ones.
- I did not independently re-derive the fix from scratch — I inherited it
  already applied in the working tree and verified/tested/committed it. I
  cross-checked the applied diff against the brief's Step 3/4 code blocks
  character-by-character (via `git diff`) and found no deviation, so I'm
  confident this is the brief's intended code, not a divergent
  implementation.

## Commit Message Deviation from Brief (per task instructions)

Per the task instructions' note, I did not copy the brief's Step 7 message
verbatim, since it says `_pdf_slug_match_count 要求文件名与基金名有实义 token
交集`, but the actual filter uses `_best_match_pdfs` (relative,
tied-for-max), not a raw `_pdf_slug_match_count(u, fund_name) > 0` absolute
check. My commit message describes the mechanism accurately: relative
match-score filtering via `_best_match_pdfs`, and explicitly notes why the
absolute check would have been insufficient (same failure mode as Spec G
10.1 — sibling funds sharing tokens both score > 0, only the relative max
separates them).

## Concerns

None. This task's fix was self-contained to the two named files, the sole
production call site was already correctly updated, and no additional call
sites or downstream consumers of `probe_l2_wayback`'s return shape needed
changes (return type `List[Tuple[str, str]]` unchanged).
