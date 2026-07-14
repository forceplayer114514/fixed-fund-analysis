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

**分类路由（方向3，前置分支，2026-07-14）**：先判基金有无 ASX 代码 / 是否 LIT-LIC 结构（上市投资信托/公司）。两条路径披露制度结构性不同（ASX 持续披露义务 vs 无强制月报归档义务），是"走哪棵树"的分类差异，非"多挖一层"的深度差异：
- **有 ASX 代码**（如 PCI/MXT/Stake）：优先钻官网 `/asx-announcements/` + investor-reports 归档页（tier 1），月报常作 ASX announcement 提交，归档页理论上必然存在；找不到应**更早报警**（属异常）。兜底 tier 2 聚合站（listcorp/afr/investorpa）仅当 tier 1 拿不到具体月份才降级
- **无 ASX 代码**（非上市管理基金）：进入"官网穷尽 -> Wayback -> 第三方聚合站"退化路径，归档页不必然存在，止损标准更宽
- **tier 分级**（影响 `source_quote` 来源追溯字段）：tier 1 = 官网 + ASX 公告原站（原始文件托管方）；tier 2 = listcorp/afr/investorpa 聚合站（转载 PDF 可能裁剪/水印/编号重排，非原始托管）。pipeline 默认只从 tier 1 抓，tier 2 降级时必须标注实际来源层级

**archive 入口断言（方向6，最高优先级，2026-07-14）**：步骤1的 success criteria = 拿到 **archive/index page**（含 ≥6 个不同日期的文档链接），**单份 PDF 链接不算达标**。机器可执行 checklist：探测输出不含 ≥6 个不同日期文档链接 -> 步骤1视为未完成，不得进入入库。根因：若目标写成"拿到1份PDF"，reader-mode 读完营销页会误判任务完成、不触发继续挖；目标写成"拿到归档页"才驱动 nav 钻探到底。

**数据源探测优先级 + 并行查单月/逐月 + 4 分钟超时**（2026-07-14 回测固化，详见 skills/CLAUDE.md 七）：

- **官网免费源永远优先于第三方聚合站**（持牌基金监管披露，官网最权威）
- **单月 PDF 是常态，逐月表罕见**：绝大多数基金按月发单月报告 PDF（Commentary 给当月收益），须多 PDF 合成逐月序列。Year×Month 逐月历史表仅在少数 factsheet 出现。**默认走单月合成路径，勿在逐月表搜索上耗预算**
- **并行探测（同一轮多工具）**：bash curl 下 1 份最新月报 PDF + **stealthy_fetch(extraction_type=html) 抓官网归档页**（免 classifier 主力；live 失败立即 Wayback 快照），解析 PDF 同时判：
  - 单月路径：Commentary 正文含当月收益 -> 多 PDF 合成（需归档页全量月 PDF 链接）
  - 逐月表路径：PDF 含 Year×Month total return 逐月历史表 -> 单 PDF 即足
- PDF 有 Commentary 单月收益 + 归档页有月度 PDF -> **立即多 PDF 合成，不等逐月表确认**
- **4 分钟墙钟预算**：探测阶段 >4 分钟立即用已确认最佳源入库（单月合成优先），**禁止继续逐月表搜索**。超时不构成失败，单月合成是默认成功路径
- 官网确认无任何月度 PDF 入口（非"无逐月表"）才转聚合站（须区分 featured，禁止复用"fundmonitors=免费逐月表"假设）

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

**工具隔离（方向1，无条件生效，2026-07-14）**：
- **reader-mode fetch（`mcp__search__fetch`/trafilatura）仅限"读正文确认收益口径(gross/net)"**，其"净化"是有损操作，**禁用于提取下载链接列表/nav 结构/归档表格**--拿它做结构性任务是工具误用
- **提取下载链接列表/nav/归档表格**：**主用 `stealthy_fetch` 拿原始 DOM（extraction_type=html，免 classifier、抗 WAF，可 bulk 并发）**；`curl 原始HTML grep href` 降为辅助（静态页快查 / stealthy_fetch 不可用时同目标兜底）。curl 走 Bash->权限 classifier 过载即中断；stealthy_fetch 走 MCP 不过 classifier
- 核心教训：曾 reader-mode fetch 官网产品页看到纯营销正文就误判"官网无归档"，实际归档页链接在 nav 里被 reader-mode 裁掉，导致跳过官网直奔 Wayback

**并行度（方向4，2026-07-14）**：
- **search 以"角度数"为硬约束，非纯数字**：≥4 个不同信息类别角度（官方产品页 archive / ASX announcements / 上市信息聚合站 listcorp·afr / Wayback 兜底）+ 1-2 路同角度不同措辞冗余 = 5-6 路。同一角度反复变换措辞凑数无意义
- **curl 2 路封顶**（curl 降为辅助：PDF 二进制直链下载 + 静态页快查 + stealthy_fetch 失败兜底；走系统网络绕 MCP 代理 block，路数多易触发目标站 WAF）。**归档页发现抓取主用 stealthy_fetch，可 bulk 每批 5-6 并发，不受 curl 2 路约束**
- **候选 URL 必须来自上一轮 search 结果的具体路径**，禁瞎猜 `/reports`/`/download-centre` 等未验证路径
- **slug 两阶段使用（不禁用，明确阶段）**：第一轮固定自然语言/概念词（如 "XX Trust monthly report archive"）定位 index/archive 页；拿到 archive 页后，精确 slug 仅用于验证/补漏（某月链接死了才用具体文件名 site: 找镜像/wayback）。禁第一步就走精确 slug--PDF 文件名式 URL 常不被搜索引擎单独索引，返回 0 是索引覆盖率问题非 block，混为一谈会误判"官网没有"

**机械化止损（方向5，硬性 checklist，2026-07-14）**--"换工具≠换目标"，界限锁死：
- archive 提取**先 stealthy_fetch 原始 DOM**（免 classifier 主力，原生处理 JS 渲染页）；reader-mode 仅确认口径，不做 archive 判定
- stealthy_fetch 失败（MCP 代理拦 198.18.0.x / 连接拒绝 / 超时）-> **立即切 curl 原始HTML，同一目标 URL**（curl 走系统网络绕 MCP 代理 block，见 memory `coolabahcapital-mcp-proxy-block`），不算"失败"不计入换方向计数
- curl 因 classifier 不可用 -> **回切 stealthy_fetch（同目标，免 classifier 兜底）**
- **只有"当前域名下所有工具(stealthy_fetch/curl)都试过、且都拿不到 archive 特征"才允许"换目标"**(转 Wayback/第三方)，这一步必须**显式打印日志 "已耗尽 <域名> 官网抓取手段"**，避免静默跳转
- 禁止"连续失败 2 次换工具"的旧模糊表述（"换工具"与"换目标"界限没锁死，曾导致本该继续挖官网却跳去 Wayback）

**探测子 agent 越权防护（2026-07-13 字段错位教训）**：
- 若主会话派探测子 agent（并行探多源时），**必须**用 `cavecrew-investigator`（read-only：Read/Grep/Glob/Bash，无 Write/Edit），禁止用有写权限的 agent 类型。
- 子 agent prompt 必须明确：仅返回 JSON 探测结果（候选 URL、PDF 是否含逐月表、付费墙状态），**禁止**调 `lib.db`/`lib.ingest` 任何写操作（`create_fund`/`upsert_monthly_return`/`add_fund*`），**禁止**写 .py 脚本调 lib.db。
- 主会话派子 agent 前后各跑一次 `SELECT COUNT(*) FROM funds` + `SELECT COUNT(*) FROM monthly_returns`，行数变了说明子 agent 越权写库，立即回滚并报错。
- 入库一律由主会话跑 `python3 -m lib.ingest`（带 `FUND_DB_WRITE_TOKEN`），探测 agent bash 不继承 token，越权写库 raise PermissionError。

**步骤 1 -- 官网并行查单月+逐月（4 分钟预算，单月默认）**（最权威免费源）：
- 搜 `<基金名> site:<issuer官网域名>` + `Performance` / `Latest Reports` / `Download Centre`（`mcp__search__search`）
- 查官网**所有下载入口**（不只 /performance，含 /download-centre / /reports / /fund-factsheet 等子页），找月度业绩 PDF/HTML 报告链接
- **并行**（同一轮多工具，勿串行）：
  - bash curl 下 1 份最新月报 PDF 直链（PDF 二进制，curl 合适；见下文抓取工具分流）
  - **stealthy_fetch(extraction_type=html) 抓官网归档页**（免 classifier 主力；live 失败立即 Wayback 快照 `http://web.archive.org/web/{ts}id_/<域名>/funds/fund-reports` 等）
- 用 `parse_pdf_text` 解析全文，**同时判两条路径**（非先逐月后单月）：
  - **单月路径（常态默认）**：Commentary 正文含当月收益 -> 多 PDF 合成（需归档页全量月 PDF 链接）
  - 逐月表路径：搜 Year×Month **total return** 逐月历史表（**非 distribution 表**，须区分口径）-> 单 PDF 即足
- 存归档页 markdown 到 `/tmp/<fund_id>_archive.md`，PDF 到 `/tmp/<fund_id>.pdf`
- **决策**：PDF 有 Commentary 单月收益 + 归档页有月度 PDF 链接 -> 走 `add`（多 PDF 合成，**默认**）。PDF 有 Year×Month total return 逐月表 -> 走 `add`（单 PDF）。**勿因"无逐月表"放弃单月合成**——单月 PDF 是绝大多数基金的常态
- **4 分钟墙钟预算**：步骤 1 探测 >4 分钟立即用已确认源入库（单月合成优先），禁止继续逐月表搜索

**步骤 2 -- 聚合站免费源**（官网无**任何月度 PDF 入口**才转，非"无逐月表"才转）：
- 搜 `site:fundmonitors.com <基金名>` 拿 FundID（从结果 URL 提取 `FundID=` 参数；详见 memory `fundmonitors-full-profile-monthly-table`）
- `stealthy_fetch` 抓 **Full Fund Profile AJAX**：`fund-profile.php?FundID=XXX&AccCode=YYY&IsAjax=1`（**非** `fund-factsheet.php` 摘要页，摘要页无逐月表）
- **遇付费墙/登录墙（"must be logged in"/"Premium"）立即跳过**，不试 AccCode 变体、不提供账号、不区分 featured（付费站点直接跳过，只找免费源）
- 存 markdown 到 `/tmp/<fund_id>_profile.md`，有 Historical Performance 逐月表 -> 走 `add-table`

**步骤 3 -- Wayback CDX 深挖**（归档页 live+Wayback 快照都拿不到月度 PDF 链接时，非"无逐月表"时）：
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
- **多 PDF 合成逐月**：CDX 返回多时间点 PDF 快照 -> 并发下（复用 `download_and_extract_parallel`），每个提 Commentary 当月收益（基金专属提取器，Bentham 用 `extract_pdf_one_bentham`，其他用 `extract_pdf_one`），合成逐月序列 -> 走 `add`。即使下 200 个 PDF 也可，并发下载+清洗很快。
- **归档页快照提链接**：PDF slug 无快照但归档页有快照 -> 下归档页多时间点快照，每个提当时月份 PDF 链接 + slug 变体，再查各链接 Wayback 快照。**注意 slug 可能改过**（如 2023 performance-report -> 2026 performance-pdf）。
- **禁止罢工**：穷尽 CDX 所有模式（归档页 + slug 变体 + wp-content）+ 多 PDF 合成才停，不能查单 slug 零快照就判"无源"。

**探测纪律（禁止 D/E/C 类浪费，2026-07-14 回测固化）**：
- **禁止构造 URL 猜 slug 后缀变体**（`-1`/`-2`/`3`/日期变体/`Bentham-` 前缀等）批量枚举下载探测。归档页月度 PDF 链接须从归档页 markdown 或 Wayback CDX 快照 `original` 字段提取，不猜不枚举。曾构造 36 个 `-1` 后缀 URL 全 404 浪费 1.5m。
- **404 不做 HEAD/GET 对比诊断**。单 URL 返回 404 即判该快照/链接不存在，直接换 URL 模式（归档页快照 / wp-content / 其他源），不诊断原因、不重试无后缀版。曾 HEAD vs GET 对比 + 无 `-1` 重探测仅 2 月存在，浪费 1.5m。
- **单月 Commentary 已确认（probe found=True）后不再穷尽 wayback_cdx slug 变体 CDX**。单月路径命中即合成，CDX 深挖仅在归档页 live+Wayback 快照都拿不到月度 PDF 链接时兜底。曾 GIF CDX=0 但已知 live PDF 存在还查多 slug，浪费 2m。

**选源优先级**（单月合成为默认常态，逐月表罕见）：
1. 官网单月 PDF + 归档页月度链接 -> `add`（多 PDF 合成，**默认**）
2. 官网 PDF 含 Year×Month total return 逐月表 -> `add`（单 PDF，罕见）
3. 聚合站免费逐月表 -> `add-table`（官网无月度 PDF 时）
4. Wayback CDX 多 PDF 合成 -> `add`（归档页失联时）
5. 穷尽官网 + 聚合站（免费源）+ Wayback CDX 深挖 + 多 PDF 合成后仍无月度源 -> **停下报错列证据**（CDX 查了哪些 URL 模式、各返回 count、PDF 快照数、聚合站付费墙状态），不强入库（数据完整性 > 入库率）

**份额类口径**：聚合站 share_class_apir 须全程一致不混用（聚合站常跟踪 Direct Investor Class，与官方机构类有费用差）。

**核对**：主会话抓取后核对数据完整性--PDF 源核对归档页含逐月 PDF 链接；HTML 源核对含 "Historical Performance" 逐月表（数值/格式异常则重新抓取验证）。

**抓取工具分流**（2026-07-13 回测固化；2026-07-14 stealthy_fetch 主力化，免 classifier 中断）：
- **归档页/HTML 结构抓取**（提下载链接/nav/归档表）：**主用 `stealthy_fetch(extraction_type="html")`**（免权限 classifier、抗 WAF、原生处理 JS 渲染，可 bulk 每批 5-6 并发）。reader-mode fetch 禁用于此（净化有损）。
- **PDF/DOCX 直链**（URL 含 `.pdf`/`.docx` 或 content-type application/pdf）：直接 bash curl 下载（PDF 二进制，curl 合适且不被代理拦；低频操作，classifier 一般无压力）：
  ```bash
  curl -sL --max-time 60 -o /tmp/<fund_id>.pdf "<pdf_url>"
  # 再解析：python3 -c "from lib.extract import parse_pdf_text; print(parse_pdf_text('/tmp/<fund_id>.pdf'))"
  ```
- **stealthy_fetch 失败兜底**（DNS 198.18.0.x 回环 / 连接拒绝 / 超时 / MCP 代理拦）：改 bash curl 原始 HTML（同目标）。已验证 coolabahcapital.com 等站点 MCP 被本机代理拦，bash curl 走系统网络正常（见 memory `coolabahcapital-mcp-proxy-block`）。
- **curl 因 classifier 不可用**：归档页 / Wayback CDX JSON 等 HTML/文本查询回切 stealthy_fetch（extraction_type=html/text，免 classifier，不中断发现）；仅 PDF 二进制下载等恢复再做（stealthy_fetch 不适合二进制），不阻塞发现。
- /tmp 下 .pdf 下载 OK，禁放 .py 脚本（inspect 遮蔽，见 memory `tmp-inspect-shadowing`）。
- 主会话执行时直接按此分流，否则只用单一工具会漏抓（curl 漏 JS 渲染归档页，stealthy_fetch 漏 MCP 被拦站点）。

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
