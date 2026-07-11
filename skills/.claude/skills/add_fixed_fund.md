---
name: add_fixed_fund
description: "添加澳洲固定收益基金：探测事实单 URL、MCP 抓取网页/PDF、LLM 提取月度收益、清洗并写入 SQLite monthly_returns 表。仅入库原始数据，不算指标。"
disable-model-invocation: true
---

# /add_fixed_fund <基金名称或标识>

## 职责边界（不可违反）
**仅负责**：数据源探测、下载、提取、清洗，写入 SQLite 的 `funds` + `monthly_returns` 表。
**不做**：不算指标（Geltner/Omega/回撤）、不检测异常、不更新 RBA、不生成报告--这些由 webapp 负责。
入库后提示用户在 webapp 触发 `POST /api/funds/{fund_id}/recompute`。

## 输入
- 基金名称（如 `Stake Accumulate Fund`）或 fund_id（如 `stake_accumulate`）
- 可选：APIR 代码、confirmed_url

## 环境前提
- 在 `skills/` 目录运行（本技能仅在此工作区可用）
- DB 路径：环境变量 `FUND_DB_PATH`，默认 `<仓库根>/data/fund_analysis.db`
- 依赖：`lib/db.py`（sqlite3 写入）、`lib/extract.py`（提取/清洗）、MCP `ScraplingServer`（网页抓取）

## 工作流

### 1. 确认基金信息
- 若用户提供 APIR，验证正则 `^[A-Z]{3}\d{4}AU$`（Stake/MXT 无标准 APIR，apir_code 可为空）
- 若未提供 URL，用 **WebSearch** 探测基金月度业绩报告/事实单 URL
- 生成 fund_id：基金英文名小写下划线（如 `Stake Accumulate Fund` → `stake_accumulate`）
- 记录 confirmed_url、fetch_method（`html` 或 `pdf`）、url_type、verified_at（今日 YYYY-MM-DD）

### 2. 检查是否已注册
用 Bash 查 `funds` 表：
```bash
cd skills && python3 -c "
from lib.db import get_connection, ensure_tables, get_fund
conn = get_connection(); ensure_tables(conn)
print(get_fund(conn, '<fund_id>'))
"
```
若已存在，提示用户改用 `/update_fixed_fund <fund_id>`。

### 3. 抓取数据（按数据源类型分流）

**HTML 基金**（Stake、Coolabah，fetch_method=html）：
- 用 MCP `fetch`（或 `stealthy_fetch` 遇反爬/Cloudflare 时）抓取 confirmed_url
- 返回 markdown/html，定位月度收益表格区域

**PDF 基金**（Bentham、Metrics，fetch_method=pdf）：
- 用 MCP `fetch` 抓取基金页面，定位最新 PDF 事实单链接
- 用 `extract.download_file(url, local_path)` 下载 PDF
- 用 `extract.parse_pdf_text(pdf_path, max_pages)` 提取文本

### 4. LLM 提取月度收益
从抓取的 markdown/html/PDF 文本中提取月度收益率序列，每条记录：
- `date`：月份（各种格式）
- `net_return`：月度收益率（小数，如 `0.0053` = 0.53%；若原文是百分数如 `0.53%`，除以 100）
- `commentary_truth`（可选）：研报正文对照值

**关键**：你是 LLM，智能适应不同基金的表格结构。不要硬编码特定基金解析器。

### 5. 清洗与缺口检查
- 用 `extract.parse_date_string(text)` 把各种日期格式统一为**月末 YYYY-MM-DD**
- 用 `extract.check_gaps(dates)` 检测缺失月份
- **缺口零容忍**（CLAUDE.md 第一条）：若有缺口，**报错并列出缺失月份，停止入库**，不跳过、不插值
- **异常值保留**（CLAUDE.md 第二条）：若某月收益极端异常，**如实保留**，不自动纠正/剔除；生成异常标记交人工判断
- **字段类型校验**：确认提取的是月度收益（非季度滚动、非年化）；若字段类型错误，视为数据缺口（不参与计算）

### 6. 写入数据库
```bash
cd skills && python3 -c "
from lib.db import get_connection, ensure_tables, create_fund, upsert_monthly_return, get_monthly_returns
conn = get_connection(); ensure_tables(conn)
create_fund(conn, fund_id='<id>', fund_name='<name>', apir_code='<apir或None>',
            confirmed_url='<url>', fetch_method='<html|pdf>', url_type='<type>',
            max_pdf_pages=<int或None>, verified_at='<YYYY-MM-DD>')
# 逐月 upsert（自动重算 NAV）
for date, net_return, ct in <数据列表>:
    upsert_monthly_return(conn, fund_id='<id>', date=date, net_return=net_return, commentary_truth=ct)
# 核对
rows = get_monthly_returns(conn, '<id>')
print(f'入库 {len(rows)} 个月，起止 {rows[0][\"date\"]} ~ {rows[-1][\"date\"]}')
"
```

### 7. 输出与提示
- 打印：基金名、fund_id、apir_code、入库月数、起止日期、数据截止月（最新月）
- 数据可追溯声明：confirmed_url + 抓取时间（verified_at）
- **提示**：数据已入库。启动 webapp 后调用 `POST /api/funds/<fund_id>/recompute` 计算 5 维指标 + 检测异常。

## 数据完整性约束（最高优先级，不可违反）
1. **禁止捏造**：所有数值必须可追溯到 confirmed_url + 抓取时间。数据缺失或抓取失败时，明确报错并停止，不允许用估算值/历史均值/合理猜测填补。
2. **缺口零容忍**：月度序列必须连续，缺失月份报错列出，不跳过、不插值。
3. **异常值保留**：不自动修改/剔除极端值，如实保留 + 异常标记。
4. **无幻觉回填**：禁止 backfill/forward-fill。提取层只做纯文本到数字映射。序列起点为第一份真实研报日期，不反推捏造成立初期数据。禁止连续相同精确浮点数插值（ANTI-FABRICATION GUARD）。

## 完成标准
- [ ] `funds` 表有该基金记录（fund_name/apir_code/confirmed_url 正确）
- [ ] `monthly_returns` 有完整月度数据（无缺口）
- [ ] NAV 复利正确（1.0 起点，逐月 `nav *= (1+net_return)`）
- [ ] 数据可追溯（URL + verified_at）
- [ ] 未计算指标（留给 webapp）

## 子 agent 委派
涉及网络抓取（MCP/WebSearch）的步骤可委派子 agent。子 agent 返回后，主对话核对数据完整性（数值突变、格式异常则重新委派验证）。
