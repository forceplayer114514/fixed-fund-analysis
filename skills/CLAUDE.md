# CLAUDE.md - skills 模块规则（数据抓取/清洗/入库端）

本文件夹是**独立的 Claude Code 工作区**，负责澳洲固定收益基金数据的抓取、清洗与入库（写入 SQLite `monthly_returns` 表）。与 webapp **仅通过共享 SQLite 数据库**（`data/fund_analysis.db`）联系，**不 import webapp 任何代码**。

## 一、数据完整性（最高优先级，不可违反）

1. **禁止捏造任何金融数据**。任何净值、收益率等数值，必须能追溯到真实抓取的数据源（URL + 抓取时间）。数据缺失或抓取失败时，必须明确报错并停止，不允许用估算值、历史平均值或"合理猜测"填补。
2. **数据缺口（gap）零容忍**：如果某月数据缺失，必须报错并列出缺失的具体月份，不允许跳过或用插值填补。
3. 月度收益率序列起点必须是第一份真实研报的日期，绝不允许反推捏造填补基金成立初期的无披露月份。

## 二、禁止对原始数值"合理性纠正"

如果解析出的数据数值异常（如单月回报率远超历史正常波动范围），程序不得自动修改、剔除或"猜测式还原"。唯一允许：如实保留原始提取值，同时生成醒目异常标记交人工判断。
若发现**字段类型提取错误**（如把季度滚动回报误当月度回报、年化值误当月度值），视为数据管道结构性缺陷，必须按数据缺口处理（不参与计算），不能套用"保留原值+异常告警"。

## 三、防范大模型幻觉回填

1. 大模型易因追求逻辑自洽（消除 gap 或匹配终值）产生"合理幻觉"篡改基础数据。
2. 任何补齐（backfill）、前推（forward-fill）逻辑在数据摄取层**绝对禁止**。提取层只能做纯文本到数字的映射。
3. 禁止连续出现相同的精确浮点数插值（ANTI-FABRICATION GUARD）。

## 四、搜索/抓取主会话执行（2026-07-13 调整）

涉及网络抓取（MCP `fetch`/`stealthy_fetch`、`mcp__search__search` 探测、`bash curl` 下 PDF）、大批量数据处理的任务，**主会话直接执行，不委派子 agent**。子 agent 有时不退出，阻塞 pipeline。主会话抓取后自行核对数据完整性，可疑（数值突变、格式异常）则重新抓取验证而非直接采信。

## 五、职责边界

- skills **只写** `funds` + `monthly_returns` 表（原始数据 + NAV 复利重算）
- skills **不算指标**（Geltner 去平滑、Omega、回撤等由 webapp 负责）
- skills **不检测异常**（MAD/Z-Score 由 webapp 负责）
- skills **不更新 RBA**（由 webapp 定时调度 + `POST /api/rba/refresh` 负责）
- 入库后提示用户在 webapp 触发 `POST /api/funds/{fund_id}/recompute` 计算指标

## 六、环境

- Python 3.9.6，用 `Optional[X]`（非 PEP 604 `X | None`），`python3`/`pip3`
- DB 路径：环境变量 `FUND_DB_PATH`，默认 `<仓库根>/data/fund_analysis.db`
- APIR 正则：`^[A-Z]{3}\d{4}AU$`（可为空，Stake/MXT 无标准 APIR）

## 七、数据源优先级（2026-07-13 回测固化）

**前置总纲（2026-07-14 新增，优先级高于下方 1-7 条；根因：PCI 基金曾 reader-mode 读完营销页误判"官网无归档"、跳过官网直奔 Wayback CDX 捞散落快照，绕大圈）**：

- **分类路由（方向3）**：先判基金有无 ASX 代码/LIT-LIC 结构。有 ASX 代码 -> 优先钻官网 `/asx-announcements/`+investor-reports 归档页（tier 1），归档页理论上必然存在，找不到更早报警；无 ASX 代码 -> 走"官网穷尽->Wayback->第三方"退化路径。tier 1=官网+ASX 原站，tier 2=listcorp/afr/investorpa 聚合站（转载 PDF 可能裁剪/水印/编号重排，`source_quote` 须标注实际来源层级），tier 2 仅当 tier 1 拿不到具体月份才降级。
- **archive 入口断言（方向6，最高优先级）**：步骤1 success criteria = 拿到 archive/index page（含 ≥6 个不同日期文档链接），**单份 PDF 不算达标**。输出不含 ≥6 日期链接视为未完成，不得入库。根因：目标写"拿1份PDF"会让 reader-mode 读完营销页误判完成、不触发继续挖。
- **工具隔离（方向1，无条件生效）**：reader-mode fetch（`mcp__search__fetch`/trafilatura）仅限"读正文确认口径(gross/net)"，**禁用于提取下载链接/nav/归档表格**（净化是有损操作）。提取链接列表**主用 `stealthy_fetch` 原始 DOM（extraction_type=html，免 classifier、抗 WAF）**；`curl 原始HTML grep href` 降为辅助（静态页快查 / stealthy_fetch 不可用时同目标兜底）。理由：curl 走 Bash->权限 classifier，并发或后端过载即中断发现；stealthy_fetch 走 MCP 不过 classifier。
- **nav 钻探（方向2）**：抓产品页 -> grep nav 子页(reports/documents/downloads/asx-announcements) -> 逐个钻 -> 官网穷尽 checklist 未过不得转第三方。抓一个营销页就跳属违规。
- **机械化止损（方向5，"换工具≠换目标"）**：reader-mode 仅确认口径，不做 archive 判定。archive 提取**先 stealthy_fetch 原始 DOM**（免 classifier 主力，原生处理 JS 渲染页）；stealthy_fetch 失败（MCP 代理拦 198.18.0.x / 连接拒绝 / 超时）-> 切 curl 原始HTML（同目标，不计失败，curl 走系统网络绕 MCP 代理 block）；**curl 因 classifier 不可用 -> 回切 stealthy_fetch（同目标，免 classifier 兜底）**；**只有当前域名 stealthy_fetch+curl 都试过且无 archive 特征才允许换目标**（转 Wayback/第三方），必须显式打印"已耗尽 <域名> 官网抓取手段"。禁旧表述"连续失败2次换工具"（曾导致本该挖官网却跳 Wayback）。
- **并行度（方向4）**：search 以"角度数"为硬约束（≥4 类：官方产品页archive/ASX announcements/聚合站listcorp·afr/Wayback兜底 +1-2 路冗余 = 5-6 路），非纯数字；**stealthy_fetch 主力可 bulk 并发（每批 5-6 URL，抗 WAF 不受 curl 2 路约束）**；curl 仅 PDF 二进制直链下载 + 静态页快查（仍 2 路封顶，WAF 约束）；候选 URL 须来自上一轮 search 具体路径禁瞎猜；slug 两阶段（第一轮自然语言定位 archive 页，精确 slug 仅验证/补漏，禁第一步走精确 slug--PDF 文件名 URL 常不被索引，返回0是覆盖率问题非 block）。

1. **官网免费源永远优先于第三方聚合站**。抓取任何基金月度数据前，第一步必须验证 issuer/manager 官网是否有 Performance / Latest Reports / Download Centre 页面。持牌基金监管要求披露，官网是最权威免费源。
2. **必须下载解析确认**：找到官网 PDF/HTML 报告链接后，必须下载用 `parse_pdf_text` 解析全文，**同时判两条路径**（非先逐月后单月）：
   - **单月路径（常态默认）**：Commentary 正文含当月收益 -> 须归档页全量月 PDF 合成逐月序列（多 PDF 合成）。**单月 PDF 是绝大多数基金的常态**，勿因"无 Year×Month 逐月表"放弃单月合成。
   - 逐月表路径（罕见）：PDF 含 Year×Month **total return** 逐月历史表（须区分 distribution 表口径）-> 单 PDF 即足。
3. **官网确认无任何月度 PDF 入口**（非"无逐月表"）后，才转第三方聚合站（fundmonitors / SQM / Morningstar）。**付费墙/登录墙立即跳过**，不提供账号、不试参数变体，只找免费源（featured fund 免费逐月表可用，如 Smarter Money LSCF）。
4. **并行探测 + 4 分钟墙钟预算**（2026-07-14 回测固化）：同一轮多工具并行下 1 份最新月报 PDF + 抓官网归档页，解析 PDF 同时判单月/逐月两条路径。探测阶段 >4 分钟立即用已确认最佳源入库（单月合成优先），**禁止继续逐月表搜索**。超时不构成失败，单月合成是默认成功路径。禁止在"找逐月表"上耗预算--曾在 Bentham GIF 上花 9 分钟找逐月表才转单月合成，属流程 bug。
5. **禁止复用"fundmonitors=免费逐月表"假设**：该假设仅对 featured fund 成立。每只基金须单独验证源适用性，不能因前一只用了 fundmonitors 就默认本只也用。
6. **聚合站遇付费墙立即跳过**：换 AccCode 变体是死循环（动作变搜索空间没变），立刻转其他免费源或停下报错，不在同一站点耗。**禁止提供付费网站账号，付费站点直接跳过**。
7. **探测纪律（禁止构造 URL/诊断 404 浪费，2026-07-14 回测固化）**：(a) 禁止构造 URL 猜 slug 后缀变体（`-1`/`-2`/日期变体等）批量枚举下载探测，归档页月度 PDF 链接须从归档页 markdown 或 Wayback CDX `original` 字段提取；(b) 404 不做 HEAD/GET 对比诊断，单 URL 404 即判不存在换模式，不诊断原因不重试无后缀版；(c) 单月 Commentary 已确认（`probe_official_evergreen` found=True）后不再穷尽 wayback_cdx slug 变体 CDX，CDX 深挖仅归档页拿不到月度链接时兜底。详见 `.claude/skills/add_fixed_fund.md` 步骤 3 探测纪律。

## 八、候选策略遍历（PDD，2026-07-13）

1. **第一性原理**：目标是尽可能多拿逐月收益数据。单一路径失败（付费墙/404/无逐月表/API 报错）只记"候选策略 X 已排除"，不构成任务失败。Agent 心智："我还没找到 = 换方法继续找"，而非"没找到 = 数据不存在"。
2. **必须维护候选策略清单并遍历完毕**（`lib/strategies.py::STRATEGY_LIST`）：local_cache（最先做）-> official_evergreen -> fundmonitors -> wayback_cdx -> distributions -> third_party_rolling。满 coverage（>=36 月）早停+标记 skip；partial/none 必须穷尽全部，否则 `premature_exit=True` 视为 bug。
3. **产出是"部分成功+缺口"结构化结果**（`DiscoveryReport`），非二元成功/失败。例：拿 34/40 月，缺口 2023年6-7月，缺口原因"官网无归档+Wayback 无该时段快照"，非笼统"拿不到数据"。
4. **区分真无解 vs 过早退出**：真无解 = `exhausted=True`（穷尽清单且逐项记录排除原因）；过早退出 = `premature_exit=True`（不允许，视为 bug）。
5. **禁止**：遍历过半前下"无数据"结论；基金专属逻辑写死通用抓取流程（基金特征走 fund_info 参数/DB，非 if-else）；弱化断言（"必须遍历完清单"不可改"尝试任意一种即可"）。
6. **兜底**：穷尽清单仍无解 -> 标记 `human_intervention_needed=True`（投资者门户/联系基金管理人），不静默放弃。
