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

## 七、代码 fallback 链（集合差驱动，2026-07-14 重构）

抓取/解析/判口径/验证/缺口/入库全由代码（`lib/strategies.py` + `lib/ingest.py`）确定性执行，LLM 只做定位（找已验证归档页 URL）+ 兜底（被代码点名补料 / 异常）。决策逻辑在代码，本节只讲原则。

1. **fallback 链**：L0 local_cache -> L1 official_evergreen -> L2 wayback_cdx -> L3 fundmonitors，集合差驱动（每级输入 gap_set 只补洞，榨干才降级，gap 空早停）。**无固定月数阈值**（36 月是下游去平滑准入条件，爬取层禁知）。
2. **官网优先**：官网免费源永远优先于第三方聚合站；付费墙/登录墙立即跳过，不提供账号、不试参数变体。
3. **缺口非失败**：穷尽后 gap 非空入 `confirmed_gaps` 表；仅 `obtained` 空集才 `human_intervention_needed`（投资者门户/联系基金管理人）。
4. **LLM 兜底过同一闸门**：兜底提取 / |r|≥0.5 超限值进 `pending_review`（过同一 `gate_check`），人工 `promote_pending` 后入 `monthly_returns`，永不直通。
5. **下界单向收敛**：发现更早月 -> 下界前移 -> expected_range 扩展 -> gap_set 重算；禁后缩（=静默删缺口）。L2 CDX 每缺失月至多 3 快照。
6. **定位已验证 URL 契约**：LLM 定位的归档页 URL 必须 fetch 过（HTTP 200 + content-type + 首屏摘要），代码独立复核。reader-mode fetch 仅限读正文确认口径，禁用于提链接（代码 curl 抓原始 HTML，`extract_archive_links` 提链接，支持 HTML `<a href>` 与 markdown）。
7. **清洗层闭环（提取逻辑不许活 REPL/临时脚本，2026-07-15 新增）**：清洗层（提取器）与搜索层同病——提示词不钉死"提取器不存在时怎么办"留决策真空，LLM 用 REPL 手写填真空（GCI 事件复盘）。堵法三层：(a) generic 兜底（`--extractor generic` 默认 `returned X%` 正则）；(b) A4 准入验证——新基金首批全进 `pending_review` `generic_first_use`（不入 `monthly_returns`），人工 `verify-extractor` 标 `extractor_verified=1` 后才入库 + 解锁 update 增量直通；反 benchmark 守卫：generic 匹配点前后 200 字符窗口含 benchmark/index/outperform/underperform/relative to/versus/vs -> `ambiguous=True` -> `ambiguous_subject` 进 pending（防命中 benchmark 收益率而非基金自身）；|r|≥0.5 优先 `abs_return_exceeds_threshold`；(c) EXTRACTOR_MISMATCH——generic 正则没匹配（`mismatch_months` 非空）报指引不 fail，走 add_fixed_fund.md 4.5 固定流程加专属提取器。专属提取器信任等级高于 generic（人写时已过样本核验，`ambiguous` 永远 False，首批直通）。探正则优先一次性诊断脚本（`python3 /tmp/diag_<issuer>.py`：fitz open 早期+晚期 2-3 样本 PDF -> dump 全文 -> grep 候选关键词 `NTA|return|Net Return` + 数值行 + 前后 5 行上下文 -> print，1 次 Bash 出全部信息，脚本留存 /tmp 跨会话不重做），**禁 desktop-commander 交互 REPL 串行探**（`start_process python3 -i` + `interact_with_process` N 次；GCI 一役 3 轮 REPL 跨 3 天探同一正则，每轮重做 import fitz + open PDF + 重定义 parse 函数，上下文未累积，纯 REPL ~39min，13 次 interact 串行 + 3 次 8s 卡顿放大）。REPL 探正则仅探无写权限（探索会话不发 `FUND_DB_WRITE_TOKEN`，`ingest`/`db` 写函数 `_require_write_token` 拒绝），输出物=候选正则+3 条源文本样例，固化进 `extract.py`+`tests`+`EXTRACTORS` 注册表+`--dry-run` 全量回跑验收后才入库。**禁 REPL 手写提取直接入库**。
