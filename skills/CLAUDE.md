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
7. **清洗层闭环（候选池 + 滚动收益约束求解，2026-07-15 solver 重构）**：

**第一性原理**：目标只是"从 PDF 拿到月收益率"。每份月报 performance 表天然含 3/6/12mo 滚动收益，正确的月度序列复利必与滚动值吻合。用这条纯数学约束替代每个发行商专属正则试错（GCI 一役 ~39min 探同一正则）。

**核心管道（默认路径，`--extractor solver`）**：
- **候选生成器** `lib/candidates.py`：`CANDIDATE_PATTERNS` 6 条模式（现 4 个专属正则 + generic `returned X%` finditer 全部匹配 + performance 表 1mo）产候选池。`generate_candidates(text) -> list[ReturnCandidate]`；`extract_rolling_any` perf/bentham/gci 三种 rolling 提取器串行尝试，附 `precision`（原文最小小数位，供求解器容差自适应）。
- **约束求解器** `lib/solver.py`：`solve_series(months) -> {ym: MonthResolution}` 三阶段——Pass Z 池唯一值预定；Pass A 约束传播 fixpoint（窗口内 1 未知月反解 expected → SELECT_TOL 内命中）；Pass B 冷启动锚定枚举（3mo 笛卡尔 ≤MAX_COMBOS + 后续窗口消歧，1mo 永不单独作选择依据）；Pass C 验证窗口计数（3/6/12mo 主 + 1mo 确认）。
- **数学准入 A4**：`MonthResolution.status='resolved'` 意味着 ≥2 个独立滚动窗口误差 <0.5%（`VERIFY_TOL=0.005`，`MIN_VERIFY_WINDOWS=2`），自动入库；不再要求 `generic_first_use` 人工首批审。
- **反捏造铁律**：`MonthResolution.chosen` 必为候选池对象（`_pick_candidate_for_value` + assert 断言 chosen is in pool），反解期望值仅用于比对选择，**绝不写库**。候选值全部经 `_pct_to_decimal` Decimal 无损转换，`source_quote` 保留原文可追溯。
- **|r|≥0.5 优先兜底**：即使 resolved 也强制 `pending_review` `abs_return_exceeds_threshold`（§五异常值人工复核规则）。
- **新 `review_reason` 枚举**（`pending_review.candidates_json` 记录全部候选池）：
    - `no_unique_candidate`（tie，多候选落容差）
    - `constraint_violation`（无候选落容差，rolling 与候选矛盾）
    - `unverifiable_no_rolling`（无 rolling 表 / 验证窗口 <2）
    - `no_candidates`（候选池空，≙ 旧 EXTRACTOR_MISMATCH）
    - `abs_return_exceeds_threshold`（|r|≥0.5，兜底优先）

**Phase 0 确定性提速**（已生效，无行为变化）：PDF 持久缓存 `data/pdf_cache/<fund_id>/YYYY-MM.pdf`（环境变量 `FUND_PDF_CACHE` 可覆盖），命中跳过下载；`funds.max_pdf_pages` 透传给 extractor 限制读页；`make_pdf_extractor` 工厂收敛旧 4 段样板包装器。**缓存文件即 source 可追溯载体，禁手工修改。**

**加候选模式流程**（新发行商真值不在池中）：
- solver dry-run 出 `no_candidates` 月 → 阅读该月 PDF 定位真值原文行 → 决定加一条 pattern：
  1. 一次性诊断脚本（`/tmp/diag_<issuer>.py`：fitz open 2-3 样本 PDF → dump 全文 + grep 候选关键词 + 前后 5 行 → print，1 次 Bash 出全部信息，脚本留存跨会话）；**禁 desktop-commander 交互 REPL 串行探**（`start_process python3 -i` + `interact_with_process` N 次；GCI 一役 3 轮 REPL ~39min 反面案例）
  2. 在 `lib/candidates.py` 加 `pattern_<issuer>(text) -> list[ReturnCandidate]` + 进 `CANDIDATE_PATTERNS`，priority=0（专属模式）
  3. `tests/test_candidates.py` 加测试用例覆盖该 pattern
  4. `python3 -c "import pytest; pytest.main(['tests/', '-q'])"` 全绿
  5. `--dry-run --extractor solver` 全量回跑验收（`solver_report` 显示该基金全部 resolved 或合理 pending）
- **禁 REPL 手写提取直接入库**；探正则仅在探索会话无写权限（不发 `FUND_DB_WRITE_TOKEN`，`_require_write_token` 拒绝）。

**Legacy 路径保留**（兼容存量 33 条 pending 与旧数据）：`--extractor generic|stake|bentham|kkc|gci` 走单提取器分支，`is_generic_extractor` + `extractor_verified` + `generic_first_use` + `ambiguous_subject` + `EXTRACTOR_MISMATCH` 语义与 2026-07-14 版一致；`verify-extractor` 子命令仍可用于处置 legacy pending。solver 路径不读写 `extractor_verified`（该列语义仅约束 legacy）。

