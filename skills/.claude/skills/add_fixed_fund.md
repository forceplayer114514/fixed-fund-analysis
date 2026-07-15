---
name: add_fixed_fund
description: "新增澳洲固定收益基金入库:LLM 定位已验证归档页 URL,代码 fallback 链全自动抓取/解析/判口径/验证/入库。仅入库原始月度收益,不算指标。"
---

# /add_fixed_fund <基金名或标识>

## 职责边界
仅入库原始月度收益到 `monthly_returns`(NAV 复利重算)。不算指标/不检测异常/不更新 RBA(由 webapp)。入库后提示 webapp `POST /api/funds/{fund_id}/recompute`。

## 输入
- 基金名或标识(用于 fund_id / 搜索定位)
- 可选:APIR 代码、份额类前缀

## 环境前提
- 在 `skills/` 工作区运行(本技能仅在此可用)
- DB:`FUND_DB_PATH` 环境变量,默认 `<仓库根>/data/fund_analysis.db`
- 写入需 `FUND_DB_WRITE_TOKEN` 环境变量(主会话跑 ingest 时注入)
- 依赖:`lib/ingest.py`(discover 全流程)、`lib/strategies.py`(fallback 链)、`lib/extract.py`、`lib/db.py`
- 搜索:`mcp__search__search`(WebSearch 已全局禁用);抓取:`mcp__search__fetch`/`stealthy_fetch`

## 工作流

### 1. 确认基金信息 + inception_date 落盘
查官网/招股书/ASX 确认成立日 `inception_date`(YYYY-MM-DD)。无精确日 -> 标 `inception_assumed=True`,代码以全链最早可得月作下界(单向收敛)。

### 2. 检查已注册
```bash
cd skills && python3 -c "from lib.db import get_connection,ensure_tables,list_funds; c=get_connection(); ensure_tables(c); [print(f['fund_id'],'|',f['fund_name']) for f in list_funds(c)]"
```
已注册则转 `/update_fixed_fund`。

### 3. 定位已验证归档页 URL(LLM 唯一决策点)
用 `mcp__search__search` + `mcp__search__fetch`/`stealthy_fetch` 定位官网归档页。**已验证 URL 契约**:必须 fetch 过该 URL,返回 HTTP 200 + content-type + 首屏摘要(确认是归档页含多月 PDF 链接)。"我找到了X"不作数,"我 fetch 过X 返回Y"才作数。

定位 reference(非分支逻辑):上市基金(有 ASX 代码)归档页通常在官网 investor reports 或 ASX 公告聚合站(listcorp 等)。

### 4. 跑代码全流程(默认 solver 提取路径)
```bash
cd skills && FUND_DB_WRITE_TOKEN=<token> python3 -m lib.ingest discover \
  --fund-id <id> --name "<name>" --url <已验证归档页URL> \
  --inception-date <YYYY-MM-DD> [--inception-assumed] \
  [--latest-month <YYYY-MM>] [--issuer-domain <domain>] \
  [--apir <APIR>] [--verified-at <ts>] \
  [--extractor solver] [--dry-run]
```
代码 curl 抓归档页 -> `run_discovery`(L0 local_cache → L1 official_evergreen → L2 wayback_cdx → L3 fundmonitors,集合差驱动补洞)→ `_ingest_discovery_solver`(候选池 `lib/candidates.py` + 滚动收益约束求解 `lib/solver.py` -> 每月 `MonthResolution` -> 分流入库)。**代码全自动,LLM 不干预抓取/解析/判口径**。PDF 持久缓存 `data/pdf_cache/<fund_id>/`(反复跑不重下)。

**A4 数学准入**(不再要求首批人工审):`MonthResolution.status='resolved'` 意味着 ≥2 个独立滚动窗口(3/6/12mo + 1mo 确认)误差 <0.5%,自动入库(`monthly_returns` 表存 `verify_windows`/`source_quote`/`pattern_tag`)。仅无法数学验证 / 验证不过 / 无候选 / tie 未消 / |r|≥0.5 的月进 `pending_review`(带 `candidates_json` 全部候选池)。

### 4.5 加候选模式(新发行商真值不在池中 -> `no_candidates`)

solver dry-run 报告某月 `status='no_candidates'`(候选池为空,现 6 条 pattern 都没匹配)-> **禁 REPL 手写提取入库**,走固定流程加候选模式:

1. **一次性诊断脚本(仅探,无写权限)**:`python3 /tmp/diag_<issuer>.py` 用 fitz open 2-3 样本 PDF(早期+晚期)-> dump 全文 + grep 候选关键词(`return|Net Return|NTA|Performance` 等)+ 前后 5 行上下文 -> `print`。1 次 Bash 出全部信息,脚本留存 /tmp 跨会话不重做。**禁 desktop-commander 交互 REPL 串行探**(GCI 一役 3 轮 REPL ~39min 反面案例)。探索会话**不发 `FUND_DB_WRITE_TOKEN`**(`_require_write_token` 拒写)。
2. **`lib/candidates.py` 加 pattern**:实现 `pattern_<issuer>(text) -> list[ReturnCandidate]`(finditer 全部匹配,`pattern_tag='<issuer>_<field>'`, `priority=0` 专属模式,`ambiguous_context` 按需)。
3. **`CANDIDATE_PATTERNS` 注册表**:加 `("<issuer>_<field>", 0, pattern_<issuer>)`(顺序在专属模式区)。
4. **`tests/test_candidates.py` 加测试**:早期+晚期样本,断言 value/source_quote/pattern_tag。
5. **`python3 -c "import pytest; pytest.main(['tests/', '-q'])"` 全绿**。
6. **`--dry-run --extractor solver` 全量回跑验收**:`solver_report` 显示该基金全部月 resolved 或合理 pending(不再出 no_candidates)。
7. **重跑 discover 入库**:solver 数学准入直通,新基金首批不需人工审。

不需要写 rolling 提取器(`extract_rolling_any` perf/bentham/gci 三条链已覆盖大多数格式;真无 rolling 表 -> 该月落 `unverifiable_no_rolling` pending,需人工判断)。

### 5. pending_input 补料重入
若代码返回 `pending_input`(如 L3 缺 fundmonitors FundID):LLM 用 `mcp__search__search` 定位该输入,补料后重跑步骤 4(断点续跑)。

### 6. 输出
- solver `resolved` 月:直入 `monthly_returns`(带 `verify_windows`/`source_quote`/`pattern_tag`)
- solver 非 resolved 月 -> `pending_review`(带 `candidates_json`),`review_reason` 枚举:
    - `no_unique_candidate` / `constraint_violation` / `unverifiable_no_rolling` / `no_candidates` / `abs_return_exceeds_threshold`
- `gaps` 入 `confirmed_gaps`(缺口非失败)
- `human_intervention_needed=True`(obtained 空集)-> 提示投资者门户/联系基金管理人

## 数据完整性铁律(不可违反,详见 CLAUDE.md)
1. 禁止捏造任何金融数据(可追溯 URL + 抓取时间)
2. 提取诚实性:单次提取不许静默跳月,声称 [a,b] 必须出全集或显式报缺月
3. 异常值保留(不自动纠正);字段类型错误(如年化误当月度)按缺口处理
4. 无幻觉回填:提取层只做纯文本到数字映射,禁 backfill/forward-fill;序列起点=第一份真实研报日期
5. ANTI-FABRICATION:禁止连续相同精确浮点数插值

## 人工通道

### pending_review 审核入口
LLM 兜底提取 / |r|≥0.5 超限值进 `pending_review` 表(过同一 gate_check,永不直通 ingest)。人工审核通过后:
```bash
cd skills && python3 -c "from lib.db import get_connection,promote_pending; c=get_connection(); promote_pending(c, <review_id>)"
```
`promote_pending` 走同一 `upsert_monthly_return`(NAV 重算),无旁路。

### verify-extractor 入口(legacy generic 首批审核后)
> 2026-07-15 起 solver 路径为默认,数学准入代替首批人工审。本入口仅处置存量
> legacy generic pending 或显式 `--extractor generic|stake|...` 首批批次。

legacy generic 新基金首批全进 `pending_review`(`generic_first_use`)。人工抽审通过后,标 `extractor_verified=1` + 批量 promote 该基金 `generic_first_use` pending:
```bash
cd skills && FUND_DB_WRITE_TOKEN=<token> python3 -m lib.ingest verify-extractor --fund-id <id>
```
`verify_extractor` 仅 promote `generic_first_use`(信任已抽审),不碰 `ambiguous_subject`/超限值 pending(那些需逐条 `promote_pending`)。标 1 后 update 侧 legacy generic 增量直通 `monthly_returns`。**solver 路径不读写 `extractor_verified`,数学准入自动直通。**

### confirmed_gaps 说明
缺口非失败:穷尽 fallback 链后该月确无数据 -> `confirmed_gaps` 表。`/update_fixed_fund` 侧每月轻量复查(CDX/fundmonitors 是否新可得),有收获补录 `monthly_returns` + 移出 `confirmed_gaps`。
