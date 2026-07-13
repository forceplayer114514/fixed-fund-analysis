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

1. **官网免费源永远优先于第三方聚合站**。抓取任何基金月度数据前，第一步必须验证 issuer/manager 官网是否有 Performance / Latest Reports / Download Centre 页面。持牌基金监管要求披露，官网是最权威免费源。
2. **必须下载解析确认**：找到官网 PDF/HTML 报告链接后，必须下载用 `parse_pdf_text` 解析全文，确认含 Year×Month 逐月历史表，不能仅凭"单一 PDF/最新月报告/非归档页"就判"无逐月归档"。
3. **官网确认无逐月入口后**，才转第三方聚合站（fundmonitors / SQM / Morningstar）。**付费墙/登录墙立即跳过**，不提供账号、不试参数变体，只找免费源（featured fund 免费逐月表可用，如 Smarter Money LSCF）。
4. **禁止复用"fundmonitors=免费逐月表"假设**：该假设仅对 featured fund 成立。每只基金须单独验证源适用性，不能因前一只用了 fundmonitors 就默认本只也用。
5. **聚合站遇付费墙立即跳过**：换 AccCode 变体是死循环（动作变搜索空间没变），立刻转其他免费源或停下报错，不在同一站点耗。**禁止提供付费网站账号，付费站点直接跳过**。

## 八、候选策略遍历（PDD，2026-07-13）

1. **第一性原理**：目标是尽可能多拿逐月收益数据。单一路径失败（付费墙/404/无逐月表/API 报错）只记"候选策略 X 已排除"，不构成任务失败。Agent 心智："我还没找到 = 换方法继续找"，而非"没找到 = 数据不存在"。
2. **必须维护候选策略清单并遍历完毕**（`lib/strategies.py::STRATEGY_LIST`）：local_cache（最先做）-> official_evergreen -> fundmonitors -> wayback_cdx -> distributions -> third_party_rolling。满 coverage（>=36 月）早停+标记 skip；partial/none 必须穷尽全部，否则 `premature_exit=True` 视为 bug。
3. **产出是"部分成功+缺口"结构化结果**（`DiscoveryReport`），非二元成功/失败。例：拿 34/40 月，缺口 2023年6-7月，缺口原因"官网无归档+Wayback 无该时段快照"，非笼统"拿不到数据"。
4. **区分真无解 vs 过早退出**：真无解 = `exhausted=True`（穷尽清单且逐项记录排除原因）；过早退出 = `premature_exit=True`（不允许，视为 bug）。
5. **禁止**：遍历过半前下"无数据"结论；基金专属逻辑写死通用抓取流程（基金特征走 fund_info 参数/DB，非 if-else）；弱化断言（"必须遍历完清单"不可改"尝试任意一种即可"）。
6. **兜底**：穷尽清单仍无解 -> 标记 `human_intervention_needed=True`（投资者门户/联系基金管理人），不静默放弃。
