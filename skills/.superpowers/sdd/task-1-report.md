# Task 1 Report: extract.py 新增 PDF 提取函数 + 测试

**状态**: 完成
**Commit**: `ac3031c`
**分支**: `fix/audit-p0-p3`

## 新增函数（lib/extract.py，4 个 + 1 个内部 helper）

1. `extract_commentary_return(text: str) -> Optional[float]`
   - Commentary 当月收益，正则 `returned\s+([+-]?\d+\.\d+)%`，负号强制捕获
   - 取第一个匹配（当月声明），无匹配返回 None
2. `extract_perf_rolling(text: str) -> dict`
   - 按列标题对应值（不靠位置），处理 `-` 空列 -> None
   - 返回 `{1mo,3mo,6mo,12mo,inception,parse_error}`，Nov 2025 错位 -> `parse_error=True` 部分填充
3. `extract_pdf_one(pdf_path, max_pages=None) -> tuple[Optional[float], dict]`
   - 顶层纯函数：parse_pdf_text -> commentary + rolling，可切 ProcessPool
4. `extract_pdf_links_from_archive(markdown: str) -> list[tuple[str, str]]`
   - 归档页提 PDF 链接，用 extract_month_prefix 识别月份，去重保持顺序

内部 helper: `_pct_to_decimal(num_str)` —— Decimal 精确十进制除法转 float（见偏差说明）。

## 测试用例

- `tests/test_extract.py` 新增 **16** 个用例（编号 11-14）
  - extract_commentary_return: 5 个（正数/负数/显式正号/无匹配/首匹配）
  - extract_perf_rolling: 5 个（正常/含-/错位/无表/负值）
  - extract_pdf_one: 2 个（mock parse_pdf_text，含 Commentary 缺失）
  - extract_pdf_links_from_archive: 4 个（markdown 链接/裸 URL/空/去重）

## pytest 输出摘要

```
tests/test_extract.py: 27 passed
tests/ (全部):          38 passed
```

- Step 2（实现前）: ImportError，符合预期
- Step 4（实现后）: 27 passed
- Step 5（全量回归）: 38 passed，无回归

## 与 brief 的偏差（调试修复，均为通过 brief 自带测试所必需）

brief 代码照抄后 4 个用例失败，根因是 brief 实现代码与其测试用例存在两处冲突，已按下述方式修复：

### 偏差 1: 浮点表示误差 —— 用 Decimal 替代 `/ 100.0`

- **现象**: `float("5.89") / 100.0 = 0.058899999999999994 != 0.0589`（字面量），导致 `test_extract_perf_rolling_normal` / `misaligned` 的 `assert r["12mo"] == 0.0589` 失败。`0.53/100`、`0.26/100` 等恰好可整除的值不受影响，故 commentary 测试碰巧没暴露。
- **修复**: 新增 `_pct_to_decimal` helper，用 `Decimal(num_str) / Decimal(100)` 精确十进制移位再转 float。`extract_commentary_return` 与 `extract_perf_rolling` 均改用之。
- **数据完整性论证**: 这不是"合理性纠正"。输入 "5.89%" 在十进制下必然对应 0.0589，Decimal 移位无损还原该真值；而 `float/100.0` 引入额外二进制舍入反而偏离原始提取值。Decimal 是更忠实的映射。

### 偏差 2: 表头与数据行合并到同一行 —— 从 header_idx 起搜索 + 按 "Class A" 切片

- **现象**: `test_extract_pdf_one` / `test_extract_pdf_one_no_commentary` 的 `fake_text` 把 Commentary 句子、表头、Class A 数据全放在一行（PDF 文本提取常见）。brief 的 `lines[header_idx + 1:]` 只在表头行之后找 Class A，单行场景下找不到 -> `parse_error=True`，值全 None。且 brief 的 `re.sub(r"class\s*a", " ", ...)` 会把行首 "returned 0.53%" 也一并捕获，产生 6 个 token。
- **修复**:
  1. 搜索范围 `lines[header_idx + 1:]` -> `lines[header_idx:]`（含表头行本身，因数据可能与之同行）
  2. token 提取由 `re.sub` 改为 `re.search(r"class\s*a")` 定位后取 `data_line[ca_match.end():]`，只捕获 "Class A" 标记之后的百分比，排除行首 Commentary 的 "returned X%"。
- **回归安全**: 多行场景（normal/with_dash/misaligned/negative_value）表头行均不含 "class a"，仍正确命中后续数据行；切片后 token 数与原 `re.sub` 一致。

## 验收标准核对

1. ✅ tests/test_extract.py 全部 PASS（27 用例，含新增 16）
2. ✅ tests/ 全部 PASS 无回归（38 用例）
3. ✅ lib/extract.py 末尾 4 个新函数，签名与 brief Interfaces 一致
4. ✅ extract_commentary_return 捕获负号（-0.26% -> -0.0026）
5. ✅ extract_perf_rolling Nov 2025 错位用例 parse_error=True 且部分填充
6. ✅ 已提交（commit message 含 Co-Authored-By）

## 仅修改的文件

- `skills/lib/extract.py`
- `skills/tests/test_extract.py`

未触碰 webapp、data/fund_analysis.db 或其他文件。
