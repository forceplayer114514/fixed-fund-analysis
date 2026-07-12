# Task 3 Report: lib/ingest.py 全自动流水线 + CLI + 测试

## 新增文件
- `/Users/chong/Desktop/fixed_fund_analysis/skills/lib/ingest.py`（162 行）
  - `add_fund(fund_id, name, archive_html_path, *, confirmed_url, apir=None, url_type='archive_page', fetch_method='pdf', verified_at=None, db_path=None, max_workers=None) -> dict`
  - `_ym_to_month_end(ym)`: '2025-03' -> '2025-03-31'
  - `_cli()`: argparse 子命令 `add`，gate fail 退出码 1
- `/Users/chong/Desktop/fixed_fund_analysis/skills/tests/test_ingest.py`（153 行）

## 测试用例数
4 个（均 `@pytest.mark.unit`）：
1. `test_add_fund_success` — 3 月数据复利验证通过、入库，校验 `commentary_truth == net_return`、`confirmed_url`、`verified_at`、`short_history_warning=True`（3<36）
2. `test_add_fund_gate_fail_not_ingested` — 缺口 04 月 gate fail，不入库（funds/monthly_returns 无该 fund_id）
3. `test_add_fund_no_pdf_links` — 归档页无 PDF 链接 -> gate_fail，months=0
4. `test_add_fund_extraction_failure_isolation` — 单 PDF 提取失败（commentary=None）被排除致缺口 -> gate_fail，`failed_months` 含 '2025-04'，不入库

## pytest 摘要
- `tests/test_ingest.py`: **4 passed**
- `tests/`（全量）: **52 passed, 10 warnings**（48 原有 + 4 新增，无回归）
- 警告均为无关项（`PytestUnknownMarkWarning: unit` 未注册 marker、urllib3/swig DeprecationWarning），不影响通过

## CLI --help 验证
`python3 -m lib.ingest add --help` 正常显示：
```
usage: ingest.py add [-h] --fund-id FUND_ID --name NAME --archive-html
                     ARCHIVE_HTML --confirmed-url CONFIRMED_URL [--apir APIR]
                     [--verified-at VERIFIED_AT] [--max-workers MAX_WORKERS]
```
含 `--fund-id --name --archive-html --confirmed-url`（required）及 `--apir --verified-at --max-workers`（optional），帮助文案中文正常。

## 数据完整性硬约束落实
1. gate_check 硬 gate：`add_fund` 中 `pass_ok, errors = gate_check(...)`，`not pass_ok` 时直接 return，**不调** `get_connection/ensure_tables/create_fund/upsert`；CLI `return 0 if result["gate_pass"] else 1`
2. `commentary_truth=net_return`：`upsert_monthly_return(..., net_return=net_return, commentary_truth=net_return)`
3. `confirmed_url` 必传：`add_fund` 签名为 keyword-only required，`create_fund` 写入 funds.confirmed_url（NOT NULL）
4. 序列起点=第一份真实研报日期：records 来自 `download_and_extract_parallel` 真实提取结果，不反推捏造
5. APIR 可选：`apir: Optional[str] = None`，默认 None
6. `short_history_warning`：`len(records_sorted) < 36`，仅提示不阻止入库

## commit hash
`e6aeb456511d10b61ed4ac372ff50b8cafdbb562`
分支：`fix/audit-p0-p3`
提交仅含 `skills/lib/ingest.py` + `skills/tests/test_ingest.py`（2 files, 315 insertions），未触碰其他文件。

## 与 brief 的偏差（1 处，测试数据修正）

**偏差**：brief 给的 2 个失败测试的归档页 URL 缺年份，导致 `extract_pdf_links_from_archive` 返回空、`add_fund` 在调用 mock 前短路。

**根因**：`extract_month_prefix`（lib/extract.py:70）策略 3「月份名+年」要求 URL 中同时出现月份名与 4 位年份（正则 `(month)[-_]*(\d{4})` 或 `(\d{4})[-_]*(month)`）。brief 测试数据中：
- `test_add_fund_gate_fail_not_ingested` 用 `mar.pdf` / `may.pdf`
- `test_add_fund_extraction_failure_isolation` 用 `mar.pdf` / `apr.pdf` / `may.pdf`

这些 URL 无年份 -> `extract_month_prefix` 返回 None -> `extract_pdf_links_from_archive` 返回 `[]` -> `add_fund` 命中 `if not links: return {..., "errors": ["归档页无 PDF 链接"], ...}` 早退，**mock `download_and_extract_parallel` 从未被调用**。因此：
- test 2 期望 errors 含「缺口」，实际得到「归档页无 PDF 链接」-> `assert False`
- test 4 期望 `failed_months` 含 '2025-04'，实际 `failed_months=[]`（早退时为空）-> `assert '2025-04' in []` 失败

**这不是抄写错误**（ingest.py 与 test_ingest.py 均逐字照抄 brief），而是 brief 测试数据本身的第 4 处 bug（brief 已修的 3 处：`_rolling` 参数名、db_path 自管理 conn、gate_fail 验证前 ensure_tables 均已落实，未回退）。

**修正**：将两个测试的归档页 URL 改为带年份的形式（与已通过的 `test_add_fund_success` 一致）：
- `mar.pdf` -> `mar-2025.pdf`、`may.pdf` -> `may-2025.pdf`、`apr.pdf` -> `apr-2025.pdf`

修正后测试意图不变（test 2 仍是缺口场景、test 4 仍是提取失败隔离场景），mock 被正常调用，4 用例全部通过。`lib/ingest.py` 实现代码未做任何改动。

## 验收标准核对
1. ✅ `tests/test_ingest.py` 4 用例全部 PASS
2. ✅ `tests/` 全部 PASS 无回归（52）
3. ✅ `python3 -m lib.ingest add --help` 正常显示
4. ✅ gate_fail 时不入库（test 2/4 验证 funds/monthly_returns 表无该 fund_id）
5. ✅ 成功时 `commentary_truth == net_return`（test 1 验证）
6. ✅ 已提交（e6aeb45）
