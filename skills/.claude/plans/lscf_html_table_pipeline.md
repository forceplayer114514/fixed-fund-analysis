# 方案：Smarter Money LSCF 入库 + HTML 表格归档流水线 + 搜索流程优化

## 一、背景与发现

**目标基金**：Smarter Money Long-Short Credit Fund（用户输入 "Smarter Money Long - Short Cred"）
- APIR：SLT2562AU（Direct Investor Class，1.00% 管理费）/ SLT3458AU（Institutional，0.75%）
- Inception：2017-08-31（首份月数据 2017-09）
- 管理人：Coolabah Capital Investments；RE：Equity Trustees

**数据源探测结论**（关键）：
| 候选源 | 结果 |
|---|---|
| Coolabah 官方 HTML 业绩报告 | 单一实时页（仅最新月 2026-05 + 滚动收益），无逐月历史表 ❌ |
| Coolabah Download Centre | 仅季度 Distributions + 单一实时 PDF，无月度归档 ❌ |
| Coolabah 官方 PDF（下载解析） | 同 HTML，无逐月历史表 ❌ |
| Wayback Machine（HTML/PDF） | 各仅 6-7 个稀疏快照，不足以重建月度序列 ❌ |
| fundmonitors Full Fund Profile（AJAX） | **完整 Year×Month 逐月表，2017-09 ~ 2026-06，105 月无缺口，net of fees** ✅ |

**数据已交叉验证**：
- YTD 复利内部一致：2026=2.90✓ 2025=6.36✓ 2024=9.56≈9.57✓ 2020=6.41✓ 2022=-3.88✓ 2017(Sep-Dec)=1.99≈2.00✓
- 与 Coolabah 官方交叉：机构类 May2026=0.61% vs Direct May2026=0.56%（差额=份额类费用差）✓；inception 5.36%(机构) vs 5.19%(Direct)✓

**结论**：该基金的完整月度历史**只存在于 fundmonitors HTML 表格**。现有 `ingest.py` 流水线（期望归档页+逐月 PDF）不兼容，需新增 HTML 表格归档路径。

---

## 二、搜索耗时反思与优化（Part 2）

### 为什么耗时长
1. 该基金无自有 PDF 归档（Coolabah 仅单一实时报告），与 Stake 模式不同——但初未识别这点
2. 走了 4 条死路（Coolabah HTML → Download Centre → Coolabah PDF → Wayback）才试 fundmonitors Full Profile
3. 首次搜到 fundmonitors 只拿到 **fact sheet 摘要页**（无逐月表），未意识到 **Full Fund Profile AJAX 标签页**才含逐月表

### 优化措施
1. **写入 memory**（`reference` 类）：澳洲基金若官方无 PDF 归档，fundmonitors Full Fund Profile AJAX（`fund-profile.php?FundID=XXX&IsAjax=1`）含完整逐月收益表；fact sheet 摘要页（`fund-factsheet.php`）无逐月表。FundID 通过 `site:fundmonitors.com <基金名>` 搜索或从事实单 URL 提取。
2. **skill `add_fixed_fund.md` 增加数据源探测决策树**：
   - 先查官方月度 PDF 归档（Stake 模式）
   - 官方仅有实时单页/无归档 → 直接查 fundmonitors Full Fund Profile AJAX / Morningstar 聚合站逐月表
   - 仍无 → Wayback 快照（最后手段）
3. 记录 fundmonitors 表格结构特征（Year×Month grid + YTD 列，N/R=未报告），供后续表格解析复用。

---

## 三、HTML 表格归档流水线设计（Part 1）

### 3.1 `lib/extract.py` 新增纯函数

**`parse_html_monthly_table(markdown: str) -> tuple[list[tuple[str, float]], dict[str, float]]`**
- 定位 "Historical Performance" 区块（在 "Historical Financial Year Performance" 之前，避免误解析 FY 表）
- 解析 markdown 表格：`| Year | Jan % | ... | Dec % | YTD % |`
- 每 cell：`N/R`/`N/A`/空 → 跳过（pre-inception/future，非缺口）；数值（含负号 `-0.19`）→ `_pct_to_decimal` → `(YYYY-MM-月末, decimal)`
- 年份从 `**2026**` 提取（去 markdown bold）
- 返回 `(records, ytd_map)`：records 升序 `[(date_str, net_return)]`；ytd_map `{"2026": 0.029, ...}`
- 复用现有 `get_last_day_of_month`、`_pct_to_decimal`、`clean_spacing`

**`gate_check_table(records, ytd_map) -> tuple[bool, list[str]]`**
- 独立函数（不改动现有 `gate_check`，避免破坏既有测试）
- 4 项校验：
  1. `check_gaps(dates)`（复用现有，缺口零容忍；N/R 在首尾外天然不影响）
  2. ANTI-FABRICATION（连续≥3 相同非零值）
  3. 字段类型 `|r| < 0.5`（50%）
  4. **YTD 复利验证**（替代 rolling 交叉验证）：对每年 ≥3 月 reported 的，compound 月度 vs ytd_map[year]，绝对误差<0.5%
- 返回 `(pass, errors)`，与 `gate_check` 同契约

### 3.2 `lib/ingest.py` 新增 `add_fund_from_html_table()`

```python
def add_fund_from_html_table(fund_id, name, table_html_path, *,
    confirmed_url, apir=None, url_type="fact_sheet_profile",
    fetch_method="html", verified_at=None, db_path=None) -> dict
```
- 读 markdown 文件 → `parse_html_monthly_table` → records + ytd_map
- `gate_check_table` → pass/fail
- pass：`create_fund` + `upsert_monthly_return`（复用 db.py；`commentary_truth=net_return`，NAV 自动重算）
- 返回 dict 与 `add_fund` 同结构（`{months, start, end, gaps, gate_pass, errors, failed_months, short_history_warning}`）
- gate_fail 不入库，退出码 1

### 3.3 CLI 扩展：`ingest.py add-table` 子命令
```bash
python3 -m lib.ingest add-table \
  --fund-id smarter_money_lscf --name "Smarter Money Long-Short Credit Fund" \
  --table-html /tmp/smarter_money_lscf_profile.md \
  --confirmed-url <fundmonitors URL> --verified-at 2026-07-13 --apir SLT2562AU
```

### 3.4 测试（CLAUDE.md 3.2 要求先写测试）
**`tests/test_extract_table.py`（新）**，`@pytest.mark.unit`：
- `parse_html_monthly_table`：用真实抓取的 fundmonitors markdown 片段做 fixture
  - 解析出 105 条记录
  - 起点 `2017-09-30`，终点 `2026-06-30`
  - N/R 跳过（2017 Jan-Aug、2026 Jul-Dec 不出现）
  - 负号捕获（2020-03 = -0.0689，2022-06 = -0.0342）
  - YTD map 正确（2026=0.029）
  - 不误解析 "Historical Financial Year Performance" 表
- `gate_check_table`：
  - YTD 复利通过（真实数据）
  - YTD 复利失败（篡改一个月值，误差>0.5% → fail）
  - 缺口检测（删一个中间月 → fail）
  - ANTI-FABRICATION（连续3月相同非零 → fail）
  - 字段类型（插入 0.6 → fail）

**`tests/test_ingest.py`（扩展）**：
- `add_fund_from_html_table` 端到端：tmp_path DB，写真实 markdown fixture，验证入库 105 条、NAV 复利正确（1.0 起点）、gate_pass=True、fund 记录 confirmed_url/fetch_method=html/apir 正确

### 3.5 skill 文档更新
`add_fixed_fund.md` 增加：
- 数据源探测决策树（见 Part 2）
- HTML 表格源分支：`ingest.py add-table` 用法
- fundmonitors 表格源说明

---

## 四、本次入库执行步骤

1. 用 `stealthy_fetch` 抓 fundmonitors Full Fund Profile AJAX，存 `/tmp/smarter_money_lscf_profile.md`（**委派子 agent**，遵守 CLAUDE.md 委派规则）
2. 主对话核对 markdown 含逐月表（数值/格式异常则重新委派）
3. 写 `lib/extract.py` 两个新函数 + `lib/ingest.py` 新函数 + CLI
4. 写 `tests/test_extract_table.py` + 扩展 `tests/test_ingest.py`
5. 跑 `pytest tests/test_extract_table.py tests/test_ingest.py`（单元测试全绿）
6. 跑 `python3 -m lib.ingest add-table ...` 端到端入库
7. 写 memory（fundmonitors 经验）+ 更新 `add_fixed_fund.md`
8. 输出入库结果，提示用户在 webapp 触发 `POST /api/funds/smarter_money_lscf/recompute`

**基金注册字段**：
- `fund_id` = `smarter_money_lscf`
- `fund_name` = `Smarter Money Long-Short Credit Fund`
- `apir_code` = `SLT2562AU`（Direct Investor Class，与 fundmonitors 数据口径一致）
- `confirmed_url` = `https://fundmonitors.com/fund-profile.php?FundID=2332&AccCode=fxuvryjo6`
- `fetch_method` = `html`，`url_type` = `fact_sheet_profile`，`verified_at` = `2026-07-13`

---

## 五、数据完整性保证（对照 CLAUDE.md）

| 约束 | 本方案如何满足 |
|---|---|
| 禁止捏造 | 数据全部来自 fundmonitors 真实抓取（URL+verified_at 可追溯），无估算/均值/猜测 |
| 缺口零容忍 | `check_gaps` 检测 Sep 2017-Jun 2026 连续性；N/R 仅在首尾外 |
| 异常值保留 | COVID 极端值（2020-03=-6.89%, 2020-04=4.87%）如实保留，不纠正 |
| 无幻觉回填 | 提取层纯文本→数字映射（`_pct_to_decimal` Decimal 移位），无 backfill/forward-fill |
| ANTI-FABRICATION | `gate_check_table` 检测连续≥3相同非零值 |
| 字段类型校验 | `|r|<0.5` + YTD 复利交叉验证（替代 rolling） |
| 序列起点=第一份真实研报 | 起点 2017-09（首份有数据月），不反推捏造 Jan-Aug |

**份额类口径一致性**：全程使用 Direct Investor Class（SLT2562AU），与 fundmonitors 数据源口径一致；不入混机构类值。若后续需机构类，另建 `smarter_money_lscf_insto`（SLT3458AU）。

---

## 六、风险与边界

- **fundmonitors 为二级聚合源**（非一级官方），但数据已与 Coolabah 官方交叉验证一致（份额类费用差可解释），且 YTD 内部复利自洽。confirmed_url 记录 fundmonitors，数据可追溯。
- **不改动现有 `gate_check` / `add_fund`**（Stake PDF 路径不受影响），新路径独立，降低回归风险。
- **不算指标**：仅入库原始月度收益 + NAV 复利；Geltner/Omega/回撤/异常检测由 webapp `recompute` 负责。
