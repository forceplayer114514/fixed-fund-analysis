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

### 4. 跑代码全流程
```bash
cd skills && FUND_DB_WRITE_TOKEN=<token> python3 -m lib.ingest discover \
  --fund-id <id> --name "<name>" --url <已验证归档页URL> \
  --inception-date <YYYY-MM-DD> [--inception-assumed] \
  [--latest-month <YYYY-MM>] [--issuer-domain <domain>] \
  [--apir <APIR>] [--verified-at <ts>]
```
代码 curl 抓归档页 -> `run_discovery`(L0 local_cache → L1 official_evergreen → L2 wayback_cdx → L3 fundmonitors,集合差驱动补洞)→ `ingest_discovery`(下载提取 + gate + 入库分流)。**代码全自动,LLM 不干预抓取/解析/判口径**。

### 5. pending_input 补料重入
若代码返回 `pending_input`(如 L3 缺 fundmonitors FundID):LLM 用 `mcp__search__search` 定位该输入,补料后重跑步骤 4(断点续跑)。

### 6. 输出
- `obtained` 月数入 `monthly_returns`
- `gaps` 入 `confirmed_gaps`(缺口非失败,穷尽后正常产出)
- `pending_review` 提示(|r|≥0.5 超限值/LLM 兜底提取待人工确认)
- `human_intervention_needed=True`(obtained 空集)时提示投资者门户/联系基金管理人

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

### confirmed_gaps 说明
缺口非失败:穷尽 fallback 链后该月确无数据 -> `confirmed_gaps` 表。`/update_fixed_fund` 侧每月轻量复查(CDX/fundmonitors 是否新可得),有收获补录 `monthly_returns` + 移出 `confirmed_gaps`。
