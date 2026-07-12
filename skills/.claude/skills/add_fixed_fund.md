---
name: add_fixed_fund
description: "添加澳洲固定收益基金：探测事实单 URL、MCP 抓取网页/PDF、LLM 提取月度收益、清洗并写入 SQLite monthly_returns 表。仅入库原始数据，不算指标。"
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
- 依赖：`lib/db.py`（sqlite3 写入）、`lib/extract.py`（提取/清洗）、`lib/ingest.py`（全自动流水线入口）、MCP `ScraplingServer`（`stealthy_fetch` 抓 JS 渲染/反爬页）、`mcp__search__search`（搜索，替代已禁用的 WebSearch）

## 工作流

### 1. 确认基金信息
- 若用户提供 APIR，验证正则 `^[A-Z]{3}\d{4}AU$`（Stake/MXT 无标准 APIR，apir_code 可为空）
- 若未提供 URL，用 `mcp__search__search` 探测基金月度业绩报告/事实单 URL（**禁止 WebSearch**，已全局禁用；详见 `~/.claude/CLAUDE.md` 工具使用规则）
- 生成 fund_id：基金英文名小写下划线（如 `Stake Accumulate Fund` → `stake_accumulate`）
- 记录 confirmed_url、fetch_method（`html` 或 `pdf`）、url_type、verified_at（今日 YYYY-MM-DD）

**数据源探测决策树**（避免走死路，2026-07 经验固化）：
1. **先查官方月度 PDF 归档**（Stake 模式：归档页含逐月 PDF 链接）-> 走 `add`（PDF 流水线）
2. **官方仅有单一实时报告/无 PDF 归档**（如 Coolabah/Smarter Money 系列）-> 直接查聚合站逐月表：
   - fundmonitors **Full Fund Profile AJAX**：`fund-profile.php?FundID=XXX&AccCode=YYY&IsAjax=1`（**非** `fund-factsheet.php` 摘要页，摘要页无逐月表）。FundID 搜 `site:fundmonitors.com <基金名>` 获取。含 "Historical Performance" Year×Month 逐月表 + YTD 列。-> 走 `add-table`（HTML 表格流水线）
   - 详见 memory `fundmonitors-full-profile-monthly-table`
3. **仍无逐月历史** -> Wayback Machine 快照（最后手段，通常稀疏不可用）
- **份额类口径**：聚合站常跟踪特定份额类（如 Direct Investor），与官方机构类有费用差，须注明 apir_code 口径，全程一致不混用。

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

### 3. 抓取归档页（委派子 agent）
- **JS 渲染归档页**（如 hellostake.com）：用 MCP `stealthy_fetch(url, network_idle=true, wait=3000)`。**禁止用 `fetch`**（只返回 footer，不等 JS 渲染）。
- 存 markdown 到 `/tmp/<fund_id>_archive.md`
- **PDF 基金**（Bentham/Metrics）：MCP 抓基金页面找 PDF 归档链接，同样存归档页 markdown
- **HTML 表格基金**（Coolabah/Smarter Money 等无 PDF 归档）：`stealthy_fetch` 抓 fundmonitors Full Fund Profile AJAX 页，存 markdown 到 `/tmp/<fund_id>_profile.md`
- 子 agent 抓取返回后，主对话核对：PDF 源核对归档页含 PDF 链接；HTML 表格源核对含 "Historical Performance" 逐月表（数值/格式异常则重新委派）

### 4. 全自动入库
**PDF 归档源**（Stake 模式）：
```bash
cd skills && python3 -m lib.ingest add \
  --fund-id <id> --name "<name>" \
  --archive-html /tmp/<fund_id>_archive.md \
  --confirmed-url <url> --verified-at <YYYY-MM-DD> \
  [--apir <apir>] [--max-workers <int>]
```
`ingest.py` 自动完成：解析归档页 PDF 链接 -> 并发下载+提取（Commentary 当月收益 + performance 表滚动收益）-> `gate_check` 硬 gate（复利交叉验证 + 缺口 + ANTI-FABRICATION + 字段类型）-> 入库。

**HTML 表格源**（fundmonitors 等聚合站逐月表）：
```bash
cd skills && python3 -m lib.ingest add-table \
  --fund-id <id> --name "<name>" \
  --table-html /tmp/<fund_id>_profile.md \
  --confirmed-url <url> --verified-at <YYYY-MM-DD> \
  [--apir <apir>]
```
`add_fund_from_html_table` 自动完成：`parse_html_monthly_table`（Year×Month 逐月表，N/R 跳过，负号捕获，排除 FY 表）-> `gate_check_table` 硬 gate（**YTD 复利交叉验证** + 缺口 + ANTI-FABRICATION + 字段类型）-> 入库。
- `gate_pass=True`：入库成功，打印 months/start/end（NAV 由 `upsert_monthly_return` 自动重算）
- `gate_pass=False`：报错列出 errors，**不入库**，退出码 1
- `short_history_warning=True`（月数<36）：提示数据不足，webapp 将标记不参与 Sortino/去平滑

### 5. 输出与提示
- `ingest.py` 返回 JSON dict：`{months, start, end, gaps, gate_pass, errors, failed_months, short_history_warning}`
- 数据可追溯声明：confirmed_url + 抓取时间（verified_at）
- **提示**：数据已入库。启动 webapp 后调用 `POST /api/funds/<fund_id>/recompute` 计算 5 维指标 + 检测异常。

## 数据完整性约束（最高优先级，不可违反）
1. **禁止捏造**：所有数值必须可追溯到 confirmed_url + 抓取时间。数据缺失或抓取失败时，明确报错并停止，不允许用估算值/历史均值/合理猜测填补。
2. **缺口零容忍**：月度序列必须连续，缺失月份报错列出，不跳过、不插值。
3. **异常值保留**：不自动修改/剔除极端值，如实保留 + 异常标记。
4. **无幻觉回填**：禁止 backfill/forward-fill。提取层只做纯文本到数字映射。序列起点为第一份真实研报日期，不反推捏造成立初期数据。禁止连续相同精确浮点数插值（ANTI-FABRICATION GUARD）。

## 硬约束（PDF 提取，2026-07 优化固化）
1. **Commentary 正文优先于 performance 表 1mo**：复利交叉验证已证明 performance 表 1mo 口径错误（列错位/12mo=inception 合并），Commentary 正文值才是当月真实收益。`extract.extract_commentary_return` 优先于 `extract.extract_perf_rolling` 的 1mo。
2. **负号强制捕获**：所有百分比正则用 `[+-]?\d+\.\d+%`（负号 `-0.26%` 必须捕获，正数可省略正号）。
3. **入库前必须过 `gate_check`**：复利交叉验证（monthly 复利 vs 滚动收益，误差<0.5%）+ 缺口零容忍 + ANTI-FABRICATION（连续>=3月相同非零值）+ 字段类型（|r|<0.5）。不通过报错停，不入库。
4. **PDF 下载并发**：`download_and_extract_parallel` ThreadPool pipeline，`max_workers=min(16, os.cpu_count())`（M5 满核），下载+提取 IO/CPU 重叠无 barrier，失败隔离。
5. **inspect 避坑**：脚本在 `skills/` 目录跑（`python3 -m lib.ingest`），**不在 /tmp**。/tmp/inspect.py 曾遮蔽标准库致 PyMuPDF 加载失败，已清理但 /tmp 下禁放 .py 脚本。
6. **序列起点=第一份真实研报日期**：不反推捏造成立初期数据。提取层只做纯文本到数字映射，禁止 backfill/forward-fill。
7. **单 PDF 提取失败隔离**：失败项 commentary=None，不中断其他 PDF；gate_check 检测由此产生的缺口。

## 完成标准
- [ ] `funds` 表有该基金记录（fund_name/apir_code/confirmed_url 正确）
- [ ] `monthly_returns` 有完整月度数据（无缺口）
- [ ] NAV 复利正确（1.0 起点，逐月 `nav *= (1+net_return)`）
- [ ] 数据可追溯（URL + verified_at）
- [ ] 未计算指标（留给 webapp）

## 子 agent 委派
- `stealthy_fetch` 抓归档页（第 3 步）委派子 agent；`mcp__search__search` 探测 URL 亦可委派。
- `ingest.py` 由主对话跑（程序化，无需委派）。
- 子 agent 返回后，主对话核对数据完整性（数值突变、格式异常则重新委派验证）。
