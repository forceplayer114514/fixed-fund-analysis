---
name: update_fixed_fund
description: "更新已注册澳洲固定收益基金的最新月度收益：读 funds 表配置、MCP 抓取最新月、upsert 新增月份到 monthly_returns。仅入库原始数据，不算指标。"
disable-model-invocation: true
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
- 依赖：`lib/db.py`、`lib/extract.py`、MCP `ScraplingServer`

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
获取 `confirmed_url`、`fetch_method`、`max_pdf_pages`，确定本地最新月份（增量起点）。

### 2. 抓取最新数据
按 `fetch_method` 分流（同 add_fixed_fund）：
- **HTML**：MCP `fetch`/`stealthy_fetch` 抓 confirmed_url
- **PDF**：MCP 抓页面找最新 PDF 链接 -> `extract.download_file` -> `extract.parse_pdf_text`

### 3. LLM 提取新增月份
- 从抓取内容提取月度收益，**仅取比本地最新月份更新的月份**（避免重复 upsert）
- `extract.parse_date_string` 统一日期为月末 YYYY-MM-DD
- `net_return` 为小数（百分数除以 100）

### 4. 缺口检查（含已有数据）
- 将新增月份与本地已有月份合并，用 `extract.check_gaps(全部日期)` 检查
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

## 完成标准
- [ ] 新增月份正确写入 `monthly_returns`
- [ ] NAV 重算正确（后续月份 NAV 同步更新）
- [ ] 合并后月度数据无缺口
- [ ] 数据可追溯
- [ ] 未计算指标（留给 webapp）

## 子 agent 委派
网络抓取步骤可委派子 agent，主对话核对返回数据。
