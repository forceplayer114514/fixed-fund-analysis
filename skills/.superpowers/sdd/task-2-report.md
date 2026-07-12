# Task 2 报告：extract.py 并发 pipeline + gate_check

**阶段 1-B**（优化计划阶段 1 后半）。依赖 Task 1 的 `extract_pdf_one`，已就绪。

## 新增函数（3 个，位于 `lib/extract.py` 末尾）

### 1. `download_and_extract_parallel(links, dest_dir, max_workers=None) -> list[tuple[str, Optional[float], dict]]`
- 单一 `ThreadPoolExecutor`，`max_workers` 默认 `min(16, os.cpu_count() or 8)`（M5 满核）。
- 每 worker = `download_file` + `extract_pdf_one`（IO 下载与 CPU 提取重叠，无 barrier）。
- `as_completed` 收集，结果按 ym 升序排序（保持时序）。
- **失败隔离**：单 PDF 下载/提取失败不中断其他，失败项返回 `(ym, None, {'parse_error':True, ...})`，通过 `try/except Exception` 包裹 `fut.result()` 实现。
- 复用 `download_file` + `extract_pdf_one`，未重复实现下载/提取逻辑。

### 2. `verify_monthly_vs_rolling(monthly, rolling) -> dict`
- 复利交叉验证：用 monthly 复利算最近 N 月累计（`prod(1+r) - 1`），对比 rolling 同期值。
- 阈值：绝对误差 `< 0.005`（0.5%，容忍 PDF 四舍五入）。
- **不捏造不插值**：rolling 缺列（None）或 monthly 不足 N 月时跳过该窗口（`pass=False`），不补值。
- 至少一个窗口通过 -> `pass=True`。
- 窗口：3mo / 6mo / 12mo。

### 3. `gate_check(records, rolling_per_month) -> tuple[bool, list[str]]`
- 入库前硬 gate（数据完整性兜底），组合 4 项校验：
  1. `check_gaps`（缺口零容忍）
  2. ANTI-FABRICATION：连续 >= 3 个相同非零浮点数（参考教训 213bdd：3 月 0.00657 硬编码）
  3. 字段类型校验：`|net_return| >= 0.5`（50%）视为字段类型错误（结构性缺陷，不参与计算）
  4. `verify_monthly_vs_rolling`：用最近月份 rolling，至少一个窗口通过；rolling `parse_error=True` 时跳过不因此 fail
- 任一失败即 `pass=False`，调用方必须停止入库。

## 顶层 import 变更
- 新增 `import concurrent.futures`（置于 stdlib import 区首位，alphabetical）。

## 新增测试用例（10 个，位于 `tests/test_extract.py` 末尾）

| # | 测试名 | 覆盖点 |
|---|--------|--------|
| 15a | `test_verify_monthly_vs_rolling_pass` | 3 月窗口复利验证通过（Stake 2026-03~05 真实数据，误差 ~0.001%） |
| 15b | `test_verify_monthly_vs_rolling_fail` | 复利不匹配 rolling（0.1576 vs 0.01）-> pass=False |
| 15c | `test_verify_monthly_vs_rolling_skip_missing_window` | monthly 不足 N 月 -> 跳过窗口，pass=False |
| 16a | `test_gate_check_pass` | 完整通过流程（无缺口/捏造/字段异常，复利通过） |
| 16b | `test_gate_check_gap_fail` | 缺口失败（2025-02 缺失） |
| 16c | `test_gate_check_fabrication_fail` | 连续 3 月 0.00657 相同值（213bdd 教训）-> ANTI-FABRICATION |
| 16d | `test_gate_check_field_range_fail` | 单月 0.6（60%）>= 0.5 -> 字段异常 |
| 16e | `test_gate_check_rolling_parse_error_skipped` | rolling parse_error=True 跳过复利验证，不因此 fail |
| 17a | `test_download_and_extract_parallel_success` | 并发下载+提取成功，结果按 ym 升序，3 url 全下载 |
| 17b | `test_download_and_extract_parallel_failure_isolation` | 单 PDF 下载失败不中断其他（失败隔离） |

测试用 monkeypatch mock `download_file` + `extract_pdf_one`，不触网。

## pytest 摘要

### Step 3（实现前，验证失败）
```
ImportError: cannot import name 'download_and_extract_parallel' from 'lib.extract'
1 error during collection
```
符合预期（TDD：先写测试见红）。

### Step 5（实现后，test_extract.py）
```
37 passed, 6 warnings in 0.15s
```
含 10 个新增用例 + 27 个既有用例，全部 PASS。

### Step 6（全部测试，无回归）
```
48 passed, 6 warnings in 0.12s
```
`tests/test_extract.py`（37）+ `tests/test_db.py`（11）全部 PASS，无回归。

## 验收标准核对
1. ✅ `tests/test_extract.py` 全部 PASS（含新增 10 用例）
2. ✅ `tests/` 全部 PASS 无回归（48 passed）
3. ✅ `lib/extract.py` 顶层有 `import concurrent.futures`，末尾 3 个新函数
4. ✅ download_and_extract_parallel 失败隔离生效（test 17b 验证：失败项 parse_error=True，其他成功）
5. ✅ download_and_extract_parallel 结果按 ym 升序（test 17a 验证）
6. ✅ gate_check 的 ANTI-FABRICATION 检测连续 3 月相同非零值（test 16c 验证）
7. ✅ 已提交

## Commit
- Hash：`82b18529e0750e38f7fcd7b28cd9a76746381aea`（短：`82b1852`）
- Branch：`fix/audit-p0-p3`
- 文件：`skills/lib/extract.py`（+143）、`skills/tests/test_extract.py`（+187），共 330 insertions
- Message 含 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## 与 brief 的偏差
**无偏差**。完全按 brief Steps 1-7 执行：
- import 位置：brief 说"在 `import re` 附近"，实际放在 stdlib import 区首位（`concurrent.futures` 字母序在 `datetime` 前），更符合 PEP 8，功能等价。
- 3 函数实现与 brief 给定代码逐字一致（含阈值 `0.005`、`|r| >= 0.5`、连续 3 月检测逻辑）。
- 10 个测试用例与 brief 给定代码逐字一致。
- commit message 与 brief 给定文本一致。

## 数据完整性硬约束落实
1. ✅ 失败隔离：`try/except Exception` 包裹 `fut.result()`，失败项 `(ym, None, {'parse_error':True})`
2. ✅ 复利交叉验证：`error < 0.005`（0.5%），至少一个窗口通过
3. ✅ ANTI-FABRICATION：连续 >= 3 个相同非零浮点数检测（防 213bdd）
4. ✅ gate_check 硬 gate：缺口/捏造/复利失败/字段类型（|r|>=0.5）任一失败即 pass=False
5. ✅ 不捏造不插值：rolling 缺列或 monthly 不足跳过该窗口（不补值）
