# Task 4 Report: 【漏洞 10.3】`discovered_pdfs` 只回传与基金名匹配的 PDF

(Note: this filename previously held a stale report from an unrelated older plan
about `calculate_omega_ratio` etc. — overwritten here with the correct Task 4
for the current `feat/grok-search-engine` / Spec G plan.)

## What I Implemented

1. Added `_best_match_pdfs()` to `llm_ingest/discover2.py`, right after
   `_pdf_slug_match_count` (around line 155). It scores every PDF on the page
   with `_pdf_slug_match_count`, takes the max score, and returns only the
   URLs tied for that max (empty page or all-zero-score page -> `[]`).
2. Replaced both `discovered_pdfs=list(cand["pdf_urls"])` call sites in
   `find_archive_v2` with `discovered_pdfs=_best_match_pdfs(cand["pdf_urls"], fund_name)`:
   - Step 5 (strong-candidate archive-page match), now at line 479.
   - Step 6 (single-PDF fallback, `no_archive=True`), now at line 501.
   Both diffs, including the Chinese comments, match the brief verbatim.
3. Added `TestDiscoveredPdfsExcludeSiblingFunds::test_sibling_fund_pdfs_not_returned`
   to `tests/test_discover2.py`, verbatim from the brief.

Code was copied verbatim from the brief; no deviation was needed to reach GREEN
(unlike Task 3, where the literal diff alone did not close the vulnerability).

## RED

Command:
```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover2.py::TestDiscoveredPdfsExcludeSiblingFunds','-v']))"
```

Result: `FAILED` with
```
AssertionError: 兄弟基金 Yarra Australian Income 的 PDF 被带回了 -- 下游 probe_l1_official 不做基金名匹配, 会直接入库
assert 'https://yarracm.com/docs/yarra-australian-income-jun-2026.pdf' not in [... all 3 pdf_urls unfiltered ...]
```
Matches expectation: before the fix, `discovered_pdfs` is `list(cand["pdf_urls"])`
verbatim, so all 3 PDFs (target + 2 sibling-fund PDFs) come back.

## GREEN

Command:
```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover2.py','-v']))"
```
Result: `3 passed, 6 warnings in 0.09s` — new test passes, both pre-existing tests
in `test_discover2.py` (`test_strong_candidate_tries_next_pdf_when_first_is_not_monthly_report`,
`test_strong_candidate_skips_page_when_all_pdfs_unrelated`) still pass.

Also ran combined with `test_discover.py` (which imports `_pdf_slug_match_count`
and `_rank_pdfs_by_name_match` from `discover2.py` for its own L1.5 navigate
fallback) to check for cross-module regressions:
```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover2.py','tests/test_discover.py','-v']))"
```
Result: `57 passed, 6 warnings in 0.11s` (3 + 54, no new failures).

## Files Changed

- `llm_ingest/discover2.py`: `+_best_match_pdfs()` helper (~19 lines), 2 call-site
  replacements at (now) lines 479 and 501.
- `tests/test_discover2.py`: `+TestDiscoveredPdfsExcludeSiblingFunds` class.

Commit: `5fbace8` — `fix(discover2): discovered_pdfs 只回传匹配度最高的 PDF (Spec G 10.3)`

## Self-Review Findings

- `_best_match_pdfs` matches the brief's exact logic: relative (tied-for-max)
  threshold, not absolute (`>0`) threshold; empty list -> `[]`; all-zero-score
  page -> `[]`; order preserved via list comprehension over `scored`.
- Both call sites (step 5 line 479, step 6 line 501) updated exactly as specified.
- `_pdf_slug_match_count` is **not** dead code — confirmed via grep it is still
  used at 3 other call sites inside `discover2.py` itself (line 449's
  `matched_pdfs` early-skip-empty-page optimization, and lines 528/560 in the
  step-6.5 navigate fallback), plus it is imported and used cross-module by
  `llm_ingest/discover.py:829` for its own L1.5 navigate-fallback logic. It
  stays exactly as-is, untouched, per the brief.
- New test exercises the real `find_archive_v2` code path end-to-end (mocks
  only `multi_query_search`, `rank_urls`, `probe_urls`, `confirm_pdf_is_monthly_report`
  — the boundary/IO functions — not `_best_match_pdfs` or `find_archive_v2`
  itself), so it is a genuine regression test, not a trivial mock-everything pass.

## Concerns (out-of-scope gap, flagged not fixed)

`find_archive_v2`'s step 6.5 navigate-fallback branches (Spec C1, originally
~lines 474-549, now ~502-579) have the **same unfiltered-list bug pattern** at
two more `discovered_pdfs=` sites that the brief does not mention:

- `discovered_pdfs=list(next_pdfs)` (now line 543)
- `discovered_pdfs=list(next_pdfs2)` (now line 577)

Both take a PDF list returned by `navigate_one_hop` off some navigated page,
verify only `first_pdf`/`first_pdf2` via `confirm_pdf_is_monthly_report`, then
return the **entire unfiltered** `next_pdfs`/`next_pdfs2` list as
`discovered_pdfs` — structurally the identical Spec G 10.3 vulnerability, just
reached via the Stake-style "navigate one hop then land on a multi-fund page"
path instead of the direct-archive-page path.

I did **not** fix this, because:
1. Unlike Task 3 (where the brief's own literal diff failed the brief's own
   test), the brief's own test here passes fully with the exact scoped fix —
   there is no GREEN-blocking gap forcing an in-scope fix.
2. The brief's "Files: Modify" list explicitly names only
   `discover2.py:450` and `discover2.py:471` (now 479/501); the navigate
   fallback is a separate code path (Spec C1) not described anywhere in the
   Spec G 10.3 background text quoted in the brief.
3. No existing or brief-supplied test exercises this path for this bug, so I
   have no repro to validate a fix against without inventing scope myself.

This mirrors how Task 3's implementer declined to fix `_pick_issuer_domain`'s
design flaw and instead flagged it for a follow-up task. Recommend a follow-up
task applying the same `_best_match_pdfs(next_pdfs, fund_name)` /
`_best_match_pdfs(next_pdfs2, fund_name)` treatment to lines 543 and 577.

## Fix: 步6.5 导航兜底路径补漏

Follow-up to the concern flagged above (Task 4 review caught the same gap).
Applied `_best_match_pdfs` to the 2 remaining `discovered_pdfs=` sites in
`find_archive_v2`'s 步 6.5 (Spec C1) 自主导航兜底分支.

### What Changed

`llm_ingest/discover2.py`:
- Line ~547 (步 6.5.a, 首跳导航命中): `discovered_pdfs=list(next_pdfs)` ->
  `discovered_pdfs=_best_match_pdfs(next_pdfs, fund_name)`, with a Chinese
  comment matching the style of the existing 步5/步6 Spec G 10.3 comments,
  explaining `navigate_one_hop` also抓的是整页 PDF 无基金名过滤.
- Line ~584 (步 6.5.b, 主页重试导航命中): `discovered_pdfs=list(next_pdfs2)` ->
  `discovered_pdfs=_best_match_pdfs(next_pdfs2, fund_name)`, same comment
  pattern ("同上").

No new helper needed — `_best_match_pdfs` already existed from the original
Task 4 commit (5fbace8).

### Test Added

`tests/test_discover2.py::TestDiscoveredPdfsExcludeSiblingFunds::test_navigate_fallback_excludes_sibling_fund_pdfs`

Drives `find_archive_v2` end-to-end so that `probe_urls` returns a page with
zero direct PDF links (forcing strong_candidates/single_pdfs to stay empty
and fall through to 步 6.5), then mocks `llm_ingest.navigate.navigate_one_hop`
to simulate landing on a multi-fund page with 2 PDFs: a target-fund PDF
(`stake-accumulate-report-jun-2026.pdf`, token overlap 2 with "Stake
Accumulate Fund") and a sibling-fund PDF
(`stake-growth-report-jun-2026.pdf`, token overlap 1). `_fetch` is mocked to
return non-empty HTML for the 步 6.5.a full-page re-fetch, and
`confirm_pdf_is_monthly_report` is mocked to always pass so the first-branch
(步 6.5.a) return path is taken. Asserts the target PDF is in
`discovered_pdfs` and the sibling PDF is not. This exercises the 步 6.5.a
site (line ~547); the 步 6.5.b site (line ~584) shares the identical
`_best_match_pdfs` call pattern and is not separately tested, consistent with
how the original Task 4 test covered only one of the two 步5/步6 sites.

### RED

Command:
```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover2.py::TestDiscoveredPdfsExcludeSiblingFunds::test_navigate_fallback_excludes_sibling_fund_pdfs','-v']))"
```
Result (against unfixed code, `discovered_pdfs=list(next_pdfs)`): `FAILED`
```
AssertionError: 兄弟基金 Stake Growth 的 PDF 被步 6.5 导航兜底路径带回了 -- 下游 probe_l1_official 不做基金名匹配, 会直接入库
assert 'https://hellostake.com/docs/stake-growth-report-jun-2026.pdf' not in ['https://hellostake.com/docs/stake-accumulate-report-jun-2026.pdf', 'https://hellostake.com/docs/stake-growth-report-jun-2026.pdf']
```
Confirms the vulnerability reproduces exactly as the review described: both
target and sibling PDFs come back unfiltered from the navigate-fallback path.

### GREEN

Command:
```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover2.py', '-v']))"
```
Result: `4 passed, 6 warnings in 0.09s` — new test passes; all 3 pre-existing
tests in this file (including the original `test_sibling_fund_pdfs_not_returned`
for 步5/步6) still pass, confirming no regression to the direct-probe paths.

Combined cross-module check:
```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover2.py', 'tests/test_discover.py', '-v']))"
```
Result: `58 passed, 6 warnings in 0.11s` (4 + 54, no new failures).

### Files Changed

- `llm_ingest/discover2.py`: 2 call-site replacements at (now) lines ~547 and
  ~584, each with a Chinese comment matching the existing 步5/步6 Spec G 10.3
  comment style.
- `tests/test_discover2.py`: `+test_navigate_fallback_excludes_sibling_fund_pdfs`
  method on the existing `TestDiscoveredPdfsExcludeSiblingFunds` class.

Commit: `53b6528` — `fix(discover2): 步6.5 导航兜底路径同样只回传匹配度最高的 PDF (Spec G 10.3 补漏)`
