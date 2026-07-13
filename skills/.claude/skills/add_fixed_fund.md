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

**数据源探测优先级**（2026-07-13 回测固化，详见 skills/CLAUDE.md 七）：**官网免费源永远优先于第三方聚合站**。先彻底查 issuer 官网所有下载入口，下载 PDF 用 `parse_pdf_text` 解析确认含逐月表；官网确认无才转 fundmonitors/SQM 等聚合站（须区分 featured）。**禁止复用"fundmonitors=免费逐月表"假设**（仅 featured fund 成立）。

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

### 2.5 候选策略探测（strategies.py，2026-07-13）

主会话先预抓候选源（`mcp__search__search` 探测官网 + `bash curl` 下 PDF + `stealthy_fetch` 抓 fundmonitors AJAX），结果回填 fund_info，再调 `strategies.py` 遍历候选清单拿 `DiscoveryReport`：

```bash
cd skills && python3 -m lib.strategies probe \
  --fund-id <id> --name "<name>" [--apir <apir>] \
  --issuer-domain <issuer域名> \
  --official-pdf-url <官网PDF直链> \
  --fundmonitors-html /tmp/<fund_id>_profile.md \
  [--target-months 36] [--db-path <path>]
```

`strategies.py` 自动遍历 `STRATEGY_LIST`（local_cache -> official_evergreen -> fundmonitors -> wayback_cdx -> distributions -> third_party_rolling），满 coverage 早停（剩余 `tried=False skip_reason=full_coverage`），partial/none 穷尽全部（否则 `premature_exit=True` 视为 bug），返回 `DiscoveryReport`（`best_strategy` / `coverage` / `exhausted` / `premature_exit` / `evidence_log`）。

**按 DiscoveryReport 决策**：
- `best_strategy=="official_evergreen"` + `ingest_entry=="add"` -> 步骤 4 走 `add`（PDF 流水线）
- `best_strategy=="fundmonitors"` + `ingest_entry=="add-table"` -> 步骤 4 走 `add-table`
- `best_strategy=="wayback_cdx"` + `ingest_entry=="add"` -> 步骤 4 走 `add`（多 PDF 合成，归档页 markdown 由主会话从 CDX 快照构造）
- `coverage=="partial"` -> 按 best_strategy 入库 + 列 `gaps` 待补（部分成功+缺口结构化输出）
- `coverage=="none"` + `exhausted==True` -> **停下报错**，列 `evidence_log` 全部排除证据（数据完整性 > 入库率）
- `premature_exit==True` -> 视为 bug，报错（partial/none 未遍历完清单）

步骤 3 的官网/fundmonitors/Wayback 手动探测细节作为 probe 函数实现指引保留，主会话预抓时参考。

### 3. 主会话顺序探测 + 抓取（不委派子 agent，2026-07-13 调整）

**搜索/抓取主会话直接执行，不派子 agent**（子 agent 有时不退出，阻塞 pipeline）。禁 WebSearch，用 `mcp__search__search`。按优先级顺序探测：

**步骤 1 -- 官网优先**（最权威免费源）：
- 搜 `<基金名> site:<issuer官网域名>` + `Performance` / `Latest Reports` / `Download Centre`（`mcp__search__search`）
- 查官网**所有下载入口**（不只 /performance，含 /download-centre / /reports / /fund-factsheet 等子页），找月度业绩 PDF/HTML 报告链接
- **PDF 直链直接 bash curl 下载**（见下文抓取工具分流），用 `parse_pdf_text` 解析全文，搜 Monthly/History/Jan-Dec 确认含 Year×Month 逐月历史表（**不能只看链接猜"单一 PDF 无归档"**，必须下载看内容）
- 存归档页 markdown 到 `/tmp/<fund_id>_archive.md`，PDF 到 `/tmp/<fund_id>.pdf`
- 有逐月表 -> 走 `add`（PDF 流水线）

**步骤 2 -- 聚合站免费源**（官网无逐月入口才转）：
- 搜 `site:fundmonitors.com <基金名>` 拿 FundID（从结果 URL 提取 `FundID=` 参数；详见 memory `fundmonitors-full-profile-monthly-table`）
- `stealthy_fetch` 抓 **Full Fund Profile AJAX**：`fund-profile.php?FundID=XXX&AccCode=YYY&IsAjax=1`（**非** `fund-factsheet.php` 摘要页，摘要页无逐月表）
- **遇付费墙/登录墙（"must be logged in"/"Premium"）立即跳过**，不试 AccCode 变体、不提供账号、不区分 featured（付费站点直接跳过，只找免费源）
- 存 markdown 到 `/tmp/<fund_id>_profile.md`，有 Historical Performance 逐月表 -> 走 `add-table`

**步骤 3 -- Wayback Machine 深挖 + 多 PDF 合成**（前两步无逐月源，2026-07-13 回测固化）：
- **CDX 深挖所有 URL 模式**（禁止只查单 slug 就判无源）：
  ```bash
  # 归档页本身快照（/performance/ 等，含当时月份 PDF 链接）
  curl -s "http://web.archive.org/cdx/search/cdx?url=<issuer域名>/performance*&output=json&fl=timestamp,original,statuscode&filter=statuscode:200&limit=200"
  # PDF slug 所有变体（URL 可能改过，如 performance-report -> performance-pdf）
  curl -s "http://web.archive.org/cdx/search/cdx?url=<域名>/performance-pdf-<slug>*&output=json&fl=timestamp,original,statuscode&filter=statuscode:200"
  curl -s "http://web.archive.org/cdx/search/cdx?url=<域名>/performance-report-<slug>*&output=json&fl=timestamp,original,statuscode&filter=statuscode:200"
  # wp-content/uploads 通配
  curl -s "http://web.archive.org/cdx/search/cdx?url=<域名>/wp-content/uploads/*/<slug>*&output=json&fl=timestamp,original,statuscode&filter=statuscode:200"
  ```
- **多 PDF 合成逐月**：若 CDX 返回多个时间点 PDF 快照，并发下各时间点 PDF（复用 `download_and_extract_parallel`），每个提 Commentary 当月收益（`extract_commentary_return`），合成逐月序列 -> 走 `add`。即使下 200 个 PDF 也可，并发下载+清洗很快。
- **归档页快照提链接**：若 PDF slug 无快照但归档页有快照，下归档页多时间点快照，每个提当时月份 PDF 链接 + slug 变体，再查各链接 Wayback 快照。**注意 slug 可能改过**（如 2023 performance-report -> 2026 performance-pdf）。
- **禁止罢工**：穷尽 CDX 所有模式（归档页 + slug 变体 + wp-content）+ 多 PDF 合成才停，不能查单 slug 零快照就判"无源"。

**选源优先级**：
1. 步骤 1 官网有逐月表 -> `add`（官网优先）
2. 步骤 2 聚合站免费逐月表 -> `add-table`（聚合站兜底）
3. 步骤 3 Wayback 多 PDF 合成 -> `add`
4. 穷尽官网 + 聚合站（免费源）+ Wayback CDX 深挖 + 多 PDF 合成后仍无逐月源 -> **停下报错列证据**（CDX 查了哪些 URL 模式、各返回 count、PDF 快照数、聚合站付费墙状态），不强入库（数据完整性 > 入库率）

**份额类口径**：聚合站 share_class_apir 须全程一致不混用（聚合站常跟踪 Direct Investor Class，与官方机构类有费用差）。

**核对**：主会话抓取后核对数据完整性--PDF 源核对归档页含逐月 PDF 链接；HTML 源核对含 "Historical Performance" 逐月表（数值/格式异常则重新抓取验证）。

**抓取工具分流**（2026-07-13 回测固化）：
- **PDF/DOCX 直链**（URL 含 `.pdf`/`.docx` 或 content-type application/pdf）：直接 bash curl 下载，不用 MCP（PDF 无 JS 渲染，curl 足够且不被代理拦）：
  ```bash
  curl -sL --max-time 60 -o /tmp/<fund_id>.pdf "<pdf_url>"
  # 再解析：python3 -c "from lib.extract import parse_pdf_text; print(parse_pdf_text('/tmp/<fund_id>.pdf'))"
  ```
- **JS 渲染页/反爬页**（hellostake/fundmonitors 等）：用 MCP `stealthy_fetch(network_idle=true, wait=3000)`。
- **MCP 失败兜底**（DNS 198.18.0.x 回环 / 连接拒绝 / 超时）：改 bash curl。已验证 coolabahcapital.com 等站点 MCP 被本机代理拦，bash curl 走系统网络正常（见 memory `coolabahcapital-mcp-proxy-block`）。
- /tmp 下 .pdf 下载 OK，禁放 .py 脚本（inspect 遮蔽，见 memory `tmp-inspect-shadowing`）。
- 主会话执行时直接按此分流，否则只用 MCP 会漏抓 PDF 直链。

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
8. **必须下载 PDF 看内容，禁止凭链接猜**（2026-07-13 回测固化）：看到 PDF 链接必须下载 `parse_pdf_text` 解析全文，不能仅凭"单一 PDF/非归档页/重定向到单个 PDF"就判"无逐月历史"。月报 PDF 内可能含 Year×Month 逐月历史表（Stake 模式即如此），须搜 Monthly/History/Jan-Dec 确认。Coolabah PDF 即反例：含滚动收益但无逐月表，须看了才知道，不能猜。
9. **Commentary 口径核对 gross vs net**（2026-07-13 回测固化）：部分基金 Commentary 正文同时给 gross+net（如 Coolabah "returned 0.59% gross (0.51% net)"），`extract_commentary_return` 提取第一个百分比可能是 gross。入库须用 net，属"字段类型提取错误"风险（gross 误当 net，维度不可比），须人工核对或改提取逻辑提 net，不能直接采信。

## 完成标准
- [ ] `funds` 表有该基金记录（fund_name/apir_code/confirmed_url 正确）
- [ ] `monthly_returns` 有完整月度数据（无缺口）
- [ ] NAV 复利正确（1.0 起点，逐月 `nav *= (1+net_return)`）
- [ ] 数据可追溯（URL + verified_at）
- [ ] 未计算指标（留给 webapp）

## 主会话执行（2026-07-13 调整）
- 搜索/抓取主会话直接执行（`mcp__search__search` 探测、`bash curl` 下 PDF、`stealthy_fetch` 抓 JS 页），**不委派子 agent**。子 agent 有时不退出，阻塞 pipeline。
- `ingest.py` 由主会话跑（程序化）。
- 主会话抓取后自行核对数据完整性（数值突变、格式异常则重新抓取验证）。
