# 清洗管道内部机制（fallback 链 + solver 求解器）

维护 `lib/candidates.py` / `lib/solver.py` / `lib/strategies.py`，处置 `no_candidates`
等 pending，或新增发行商候选模式时读本文档。日常跑 `/add_fixed_fund` 不需要——
代码全自动执行这套机制，LLM 只做定位（找已验证归档页 URL）+ 兜底（被代码点名补料/异常）。

## 一、fallback 链原则（集合差驱动，2026-07-14 重构）

抓取/解析/判口径/验证/缺口/入库全由代码（`lib/strategies.py` + `lib/ingest.py`）
确定性执行，决策逻辑在代码，本节只讲原则。

1. **fallback 链**：L0 local_cache -> L1 official_evergreen -> L2 wayback_cdx ->
   L3 fundmonitors，集合差驱动（每级输入 gap_set 只补洞，榨干才降级，gap 空早停）。
   **无固定月数阈值**（36 月是下游去平滑准入条件，爬取层禁知；入库门槛见
   `skills/CLAUDE.md`）。
2. **官网优先**：官网免费源永远优先于第三方聚合站；付费墙/登录墙立即跳过，
   不提供账号、不试参数变体。
3. **缺口非失败**：穷尽后 gap 非空入 `confirmed_gaps` 表；仅 `obtained` 空集才
   `human_intervention_needed`（投资者门户/联系基金管理人）。
4. **LLM 兜底过同一闸门**：兜底提取 / |r|≥0.5 超限值进 `pending_review`
   （过同一 `gate_check`），人工 `promote_pending`（`python3 -m lib.ingest
   promote-pending --review-id <id>`）后入 `monthly_returns`，永不直通。
5. **下界单向收敛**：发现更早月 -> 下界前移 -> expected_range 扩展 -> gap_set
   重算；禁后缩（=静默删缺口）。L2 CDX 每缺失月至多 3 快照。
6. **定位已验证 URL 契约**：LLM 定位的归档页 URL 必须 fetch 过（HTTP 200 +
   content-type + 首屏摘要），代码独立复核。reader-mode fetch 仅限读正文确认
   口径，禁用于提链接（代码 curl 抓原始 HTML，`extract_archive_links` 提链接，
   支持 HTML `<a href>` 与 markdown）。

## 二、清洗层闭环（候选池 + 滚动收益约束求解，2026-07-15 solver 重构）

**第一性原理**：目标只是"从 PDF 拿到月收益率"。每份月报 performance 表天然含
3/6/12mo 滚动收益，正确的月度序列复利必与滚动值吻合。用这条纯数学约束替代每个
发行商专属正则试错（GCI 一役 ~39min 探同一正则）。

**核心管道（默认路径，`--extractor solver`）**：
- **候选生成器** `lib/candidates.py`：`CANDIDATE_PATTERNS` 6 条模式（现 4 个
  专属正则 + generic `returned X%` finditer 全部匹配 + performance 表 1mo）产
  候选池。`generate_candidates(text) -> list[ReturnCandidate]`；
  `extract_rolling_any` perf/bentham/gci 三种 rolling 提取器串行尝试，附
  `precision`（原文最小小数位，供求解器容差自适应）。
- **约束求解器** `lib/solver.py`：`solve_series(months) -> {ym: MonthResolution}`
  三阶段——Pass Z 池唯一值预定；Pass A 约束传播 fixpoint（窗口内 1 未知月反解
  expected → SELECT_TOL 内命中）；Pass B 冷启动锚定枚举（3mo 笛卡尔
  ≤MAX_COMBOS + 后续窗口消歧，1mo 永不单独作选择依据）；Pass C 验证窗口计数
  （3/6/12mo 主 + 1mo 确认）。
- **数学准入 A4**：`MonthResolution.status='resolved'` 意味着 ≥2 个独立滚动
  窗口误差 <0.5%（`VERIFY_TOL=0.005`，`MIN_VERIFY_WINDOWS=2`），自动入库；
  不再要求 `generic_first_use` 人工首批审。
- **反捏造铁律**：`MonthResolution.chosen` 必为候选池对象
  （`_pick_candidate_for_value` + assert 断言 chosen is in pool），反解期望值
  仅用于比对选择，**绝不写库**。候选值全部经 `_pct_to_decimal` Decimal 无损
  转换，`source_quote` 保留原文可追溯。
- **|r|≥0.5 优先兜底**：即使 resolved 也强制 `pending_review`
  `abs_return_exceeds_threshold`（异常值人工复核规则，见 `skills/CLAUDE.md`）。
- **新 `review_reason` 枚举**（`pending_review.candidates_json` 记录全部候选池）：
    - `no_unique_candidate`（tie，多候选落容差）
    - `constraint_violation`（无候选落容差，rolling 与候选矛盾）
    - `unverifiable_no_rolling`（无 rolling 表 / 验证窗口 <2）
    - `no_candidates`（候选池空，≙ 旧 EXTRACTOR_MISMATCH）
    - `abs_return_exceeds_threshold`（|r|≥0.5，兜底优先）

**Phase 0 确定性提速**（已生效，无行为变化）：PDF 持久缓存
`data/pdf_cache/<fund_id>/YYYY-MM.pdf`（环境变量 `FUND_PDF_CACHE` 可覆盖），
命中跳过下载；`funds.max_pdf_pages` 透传给 extractor 限制读页；
`make_pdf_extractor` 工厂收敛旧 4 段样板包装器。**缓存文件即 source 可追溯
载体，禁手工修改**（`.claude/hooks/db_write_guard.py` 已 deny 对 `data/pdf_cache/`
的 Write/Edit）。`parse_pdf_text`（`lib/extract.py`）额外把提取出的全文落盘为
同目录同名 `.txt`（mtime 校验 + 页数标记，命中免重新 fitz 解析）——诊断/回归
批量重跑时直接 grep 文本缓存，见 §三。

## 三、加候选模式流程（新发行商真值不在池中，执行入口 `/extend_extractor`）

solver dry-run / `add_fixed_fund`·`update_fixed_fund` 运行期出 `no_candidates`
月 → 运行期代码冻结，输出诊断包后停止，转 `/extend_extractor`（主会话/强模型
执行，禁派 flash 子代理写这部分代码——格式漂移改动需要人读 PDF 原文判断口径）：

1. **存在性确认先行**：先 grep 该基金文本缓存（`data/pdf_cache/<fund_id>/
   YYYY-MM.txt`，`parse_pdf_text` 已自动落盘，见 §二 Phase 0）确认问题月 PDF
   原文到底有没有对应数值——没有 = 缺口（入 `confirmed_gaps`），不是提取器
   bug，到此为止。默认"提取器有 bug"去找是常见诊断盲区，务必先排除。
2. **全区间广采样**：grep 该基金全部月份文本缓存（非抽 2-3 个样本），列出
   格式变体分布及各自起止区间，一次看全漂移谱系（GCI 一役曾因只抽 4 样本
   漏了 6 个月的脚注/括号负数变体，被迫二轮诊断+改+测）。
3. 在 `lib/candidates.py` 加 `pattern_<issuer>(text) -> list[ReturnCandidate]`
   覆盖步骤 2 全部变体 + 进 `CANDIDATE_PATTERNS`，priority=0（专属模式）
4. `tests/test_candidates.py` 加测试用例覆盖每个格式变体
5. `python3 -c "import pytest; pytest.main(['tests/', '-q'])"` 全绿
6. `python3 -m lib.ingest regress`（先单基金 `--fund-id`，再全库）零
   `value_drift`/`coverage_regression`——以库内已入库值为基线做 diff，取代
   人肉抽样回归验收（只读，不需要 write token）
7. 正式重跑入库（`update --fund-id <id>`），确认问题月已 resolved 或合理 pending

**禁 desktop-commander 交互 REPL 串行探**（`start_process python3 -i` +
`interact_with_process` N 次；GCI 一役 3 轮 REPL ~39min 反面案例）——有文本
缓存后一次性 grep/awk 定位即可，不需要交互式反复探。

**禁 REPL 手写提取直接入库**；探正则仅在探索会话无写权限（不发
`FUND_DB_WRITE_TOKEN`，`_require_write_token` 拒绝；`db_write_guard.py` hook
另外拦截了 token 出现在非 `lib.ingest` 命令中的用法）。

## 四、Legacy 路径（兼容存量 pending 与旧数据）

`--extractor generic|stake|bentham|kkc|gci` 走单提取器分支，
`is_generic_extractor` + `extractor_verified` + `generic_first_use` +
`ambiguous_subject` + `EXTRACTOR_MISMATCH` 语义与 2026-07-14 版一致；
`verify-extractor` 子命令仍可用于处置 legacy pending。solver 路径不读写
`extractor_verified`（该列语义仅约束 legacy）。
