---
name: update_fixed_fund
description: "更新已注册澳洲固定收益基金的最新月度收益：读 funds 表配置、MCP 抓取最新月、upsert 新增月份到 monthly_returns。仅入库原始数据，不算指标。"
---

# /update_fixed_fund <基金ID或名称>

## 职责边界（不可违反）
**仅负责**：读取已注册基金配置、抓取最新月度数据、upsert 新增月份到 `monthly_returns`。
**不做**：不算指标、不检测异常、不更新 RBA--由 webapp 负责。入库后提示 webapp recompute。

## 输入
- 基金 ID（如 `stake_accumulate`）或名称
- 不传则列出所有已注册基金供用户选择

## 环境前提
- 在 `skills/` 目录运行（本技能仅在此工作区可用）
- DB 路径：环境变量 `FUND_DB_PATH`，默认 `<仓库根>/data/fund_analysis.db`
- 依赖：`lib/db.py`、`lib/extract.py`、`lib/ingest.py`（全自动流水线入口）、MCP `ScraplingServer`（`stealthy_fetch` 抓 JS 渲染/反爬页）、`mcp__search__search`（搜索，替代已禁用的 WebSearch）

## 工作流

### 1. 读取基金配置与现有数据
```bash
cd skills && python3 -c "
from lib.db import get_connection, ensure_tables, list_funds, get_fund, get_monthly_returns
conn = get_connection(); ensure_tables(conn)
# 不传参数时列出全部基金
funds = list_funds(conn)
for f in funds: print(f['fund_id'], '|', f['fund_name'], '|', f['confirmed_url'])
# 选中后读现有最新月份
fund = get_fund(conn, '<fund_id>')
rows = get_monthly_returns(conn, '<fund_id>')
print(f'现有 {len(rows)} 个月，最新 {rows[-1][\"date\"]}' if rows else '无数据')
"
```
获取 `confirmed_url`、`fetch_method`、`max_pdf_pages`，确定本地最新月份（增量起点）。若 `confirmed_url` 失效，用 `mcp__search__search` 重新探测（**禁止 WebSearch**，已全局禁用）。

### 2. 抓取最新数据
按 `fetch_method` 分流（同 add_fixed_fund）：
- **HTML**：MCP `fetch`/`stealthy_fetch` 抓 confirmed_url
- **PDF**：MCP 抓页面找最新 PDF 链接 -> `extract.download_file` -> `extract.parse_pdf_text`

### 3. LLM 提取新增月份
- 从抓取内容提取月度收益，**仅取比本地最新月份更新的月份**（避免重复 upsert）
- 提取用 `extract.extract_commentary_return`（Commentary 正文优先）+ `extract.extract_perf_rolling`（滚动收益），负号正则 `[+-]?\d+\.\d+%`（`-0.26%` 必须捕获）
- `extract.parse_date_string` 统一日期为月末 YYYY-MM-DD
- `net_return` 为小数（百分数除以 100）

### 4. 缺口检查（含已有数据）
- 将新增月份与本地已有月份合并，用 `extract.check_gaps(全部日期)` 检查
- 合并后用 `extract.gate_check(全部 records, rolling_per_month)` 做硬 gate（复利交叉验证 + 缺口 + ANTI-FABRICATION + 字段类型），不通过停止更新
- **缺口零容忍**：若合并后有缺口，报错并列出缺失月份，停止更新
- 异常值保留（不自动纠正）

### 5. upsert 新增月份
```bash
cd skills && python3 -c "
from lib.db import get_connection, ensure_tables, upsert_monthly_return, get_monthly_returns
conn = get_connection(); ensure_tables(conn)
for date, net_return, ct in <新增月份列表>:
    upsert_monthly_return(conn, fund_id='<id>', date=date, net_return=net_return, commentary_truth=ct)
rows = get_monthly_returns(conn, '<id>')
print(f'更新后 {len(rows)} 个月，最新 {rows[-1][\"date\"]}')
"
```
`upsert_monthly_return` 自动重算 NAV（新增月份后所有 NAV 更新）。

### 6. 输出
- 打印：新增月数、最新截止月
- 若无新月份数据：提示"已是最新，无需更新"
- **提示**：启动 webapp 后调用 `POST /api/funds/<fund_id>/recompute` 重算指标 + 异常检测。

## 数据完整性约束（同 add_fixed_fund）
1. 禁止捏造（可追溯 URL + 抓取时间）
2. 缺口零容忍（缺失月份报错列出）
3. 异常值保留（不自动纠正）
4. 无幻觉回填（不 backfill/forward-fill）

## 硬约束（PDF 提取，2026-07 优化固化）
1. **Commentary 正文优先于 performance 表 1mo**：复利交叉验证已证明 performance 表 1mo 口径错误（列错位/12mo=inception 合并），Commentary 正文值才是当月真实收益。`extract.extract_commentary_return` 优先于 `extract.extract_perf_rolling` 的 1mo。
2. **负号强制捕获**：所有百分比正则用 `[+-]?\d+\.\d+%`（负号 `-0.26%` 必须捕获，正数可省略正号）。
3. **入库前必须过 `gate_check`**：复利交叉验证（monthly 复利 vs 滚动收益，误差<0.5%）+ 缺口零容忍 + ANTI-FABRICATION（连续>=3月相同非零值）+ 字段类型（|r|<0.5）。不通过报错停，不入库。
4. **PDF 下载并发**：`download_and_extract_parallel` ThreadPool pipeline，`max_workers=min(16, os.cpu_count())`（M5 满核），下载+提取 IO/CPU 重叠无 barrier，失败隔离。
5. **inspect 避坑**：脚本在 `skills/` 目录跑（`python3 -m lib.ingest`），**不在 /tmp**。/tmp/inspect.py 曾遮蔽标准库致 PyMuPDF 加载失败，已清理但 /tmp 下禁放 .py 脚本。
6. **序列起点=第一份真实研报日期**：不反推捏造成立初期数据。提取层只做纯文本到数字映射，禁止 backfill/forward-fill。
7. **单 PDF 提取失败隔离**：失败项 commentary=None，不中断其他 PDF；gate_check 检测由此产生的缺口。

## 完成标准
- [ ] 新增月份正确写入 `monthly_returns`
- [ ] NAV 重算正确（后续月份 NAV 同步更新）
- [ ] 合并后月度数据无缺口
- [ ] 数据可追溯
- [ ] 未计算指标（留给 webapp）

## 子 agent 委派
网络抓取步骤可委派子 agent，主对话核对返回数据。
