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
- DB:`FUND_DB_PATH`(默认 `<仓库根>/data/fund_analysis.db`);写入需 `FUND_DB_WRITE_TOKEN`
- 依赖:`lib/ingest.py`(discover)、`lib/strategies.py`(fallback 链)、`lib/extract.py`、`lib/db.py`、`lib/candidates.py`、`lib/solver.py`
- 搜索/抓取工具规则详见全局 `~/.claude/CLAUDE.md`

## 工作流

### 1. 确认基金信息 + inception_date 落盘
查官网/招股书/ASX 确认成立日 `inception_date`(YYYY-MM-DD)。无精确日 -> 标 `inception_assumed=True`,代码以全链最早可得月作下界(单向收敛)。

### 2. 检查已注册
```bash
cd skills && python3 -c "from lib.db import get_connection,ensure_tables,list_funds; c=get_connection(); ensure_tables(c); [print(f['fund_id'],'|',f['fund_name']) for f in list_funds(c)]"
```
已注册则转 `/update_fixed_fund`。

### 3. 定位已验证归档页 URL(LLM 唯一决策点)
用 `mcp__search__search`(默认引擎去 bing 降噪)+ `mcp__search__fetch`/`stealthy_fetch` 定位官网归档页。

**已验证 URL 契约**:必须 fetch 过该 URL,返回 HTTP 200 + content-type + 首屏摘要(确认是归档页含多月 PDF)。"我找到了X"不作数,"我 fetch 过X 返回Y"才作数。

**AJAX/动态页判别**(GCI 教训):静态 `fetch` 首屏仅 1 份最新 PDF -> 判为 AJAX/动态归档页 -> 改用 `stealthy_fetch(network_idle=true, wait≥2000, extraction_type=html)` 渲染确认多月文档存在。注意:渲染后 DOM 可能只给资源页 URL(`data-href`)而非直 PDF 链接,此时需子代理进一步探 AJAX JSON 端点或逐资源页定位真实 PDF URL(见下"子代理隔离")。

**子代理隔离**(详见 `skills/CLAUDE.md` §四):归档页探测若需迭代写/调脚本(Vue AJAX 端点、分页参数、正则试错、渲染后解析、逐资源页爬)-> 派 `general-purpose` 子代理,返回结构化摘要(已验证归档页 URL + 归档结构:静态/AJAX/分页 + PDF 链接规律或 JSON 端点),脚本代码不进主上下文。单次 one-shot fetch 主会话可做。

定位 reference(非分支逻辑):上市基金归档页通常在官网 investor reports 或 ASX 公告聚合站。

### 4. 跑代码全流程(默认 solver 提取路径)
```bash
cd skills && FUND_DB_WRITE_TOKEN=<token> python3 -m lib.ingest discover \
  --fund-id <id> --name "<name>" --url <已验证归档页URL> \
  --inception-date <YYYY-MM-DD> [--inception-assumed] \
  [--latest-month <YYYY-MM>] [--issuer-domain <domain>] \
  [--apir <APIR>] [--verified-at <ts>] \
  [--archive-html <预抓渲染HTML路径>] \
  [--extractor solver] [--dry-run]
```
`--url` 走代码 curl 抓归档页;`--archive-html`(AJAX/动态页用)跳过 curl 直读子代理预抓的渲染后 HTML。代码 `run_discovery`(L0 local_cache -> L1 official_evergreen -> L2 wayback_cdx -> L3 fundmonitors,集合差驱动补洞)-> `_ingest_discovery_solver`(候选池 + 滚动收益约束求解 -> 每月 `MonthResolution` -> 分流入库)。**代码全自动,LLM 不干预抓取/解析/判口径**。PDF 持久缓存 `data/pdf_cache/<fund_id>/`。

**A4 数学准入 + review_reason 枚举 + 加候选模式流程 + 反捏造铁律**:详见 `skills/CLAUDE.md` §七。要点:`resolved`(≥2 独立滚动窗口误差 <0.5%)自动入库;非 resolved -> `pending_review`(带 `candidates_json`);`no_candidates` 月走加候选模式(禁 REPL 手写提取)。

### 5. pending_input 补料重入
若代码返回 `pending_input`(如 L3 缺 fundmonitors FundID、AJAX 页缺 JSON 端点):LLM(必要时子代理)定位该输入,补料后重跑步骤 4(断点续跑)。

### 6. 输出
- `resolved` 月 -> `monthly_returns`(带 `verify_windows`/`source_quote`/`pattern_tag`)
- 非 resolved 月 -> `pending_review`(带 `candidates_json`)
- `gaps` -> `confirmed_gaps`(缺口非失败)
- `human_intervention_needed=True`(obtained 空集)-> 提示投资者门户/联系基金管理人

## 数据完整性铁律
详见 `fixed_fund_analysis/CLAUDE.md` §一/五/六 + `skills/CLAUDE.md` §一/二/三。要点:禁捏造(可追溯 URL+时间)、缺口零容忍、异常值保留不纠正、字段类型错误按缺口处理、提取层禁 backfill/forward-fill、ANTI-FABRICATION。

## 人工通道

### pending_review 审核入口
LLM 兜底提取 / |r|≥0.5 超限值进 `pending_review`(过同一 gate_check,永不直通 ingest)。人工审核通过后:
```bash
cd skills && python3 -c "from lib.db import get_connection,promote_pending; c=get_connection(); promote_pending(c, <review_id>)"
```

### verify-extractor 入口(legacy)
仅处置存量 legacy generic pending 或显式 `--extractor generic|stake|...` 首批。legacy generic 新基金首批全进 `pending_review`(`generic_first_use`),人工抽审后:
```bash
cd skills && FUND_DB_WRITE_TOKEN=<token> python3 -m lib.ingest verify-extractor --fund-id <id>
```
`solver` 路径为默认,数学准入代替首批人工审,solver 不读写 `extractor_verified`。

### confirmed_gaps 说明
缺口非失败:穷尽 fallback 链后该月确无数据 -> `confirmed_gaps` 表。`/update_fixed_fund` 侧每月轻量复查,有收获补录 + 移出 `confirmed_gaps`。
