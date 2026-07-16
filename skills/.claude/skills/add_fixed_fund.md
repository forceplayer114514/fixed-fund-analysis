---
name: add_fixed_fund
description: "新增澳洲固定收益基金入库:LLM 定位已验证归档页 URL,代码 fallback 链全自动抓取/解析/判口径/验证/入库。仅入库原始月度收益,不算指标。"
---

# /add_fixed_fund <基金名或标识>

## 职责边界
仅入库原始月度收益到 `monthly_returns`(NAV 复利重算)。不算指标/不检测异常/不更新 RBA(由 webapp)。入库后提示 webapp `POST /api/funds/{fund_id}/recompute`。

## 输入 / 环境
输入:基金名或标识(fund_id/搜索定位),可选 APIR、份额类前缀。在 `skills/` 工作区运行;数据完整性铁律、写库方式、搜索工具规则见 `skills/CLAUDE.md`。

## 工作流

### 1. 确认基金信息 + inception_date 落盘
查官网/招股书/ASX 确认成立日 `inception_date`(YYYY-MM-DD)。无精确日 -> 标 `inception_assumed=True`,代码以全链最早可得月作下界(单向收敛)。

### 2. 检查已注册
```bash
cd skills && python3 -c "from lib.db import get_connection,ensure_tables,list_funds; c=get_connection(); ensure_tables(c); [print(f['fund_id'],'|',f['fund_name']) for f in list_funds(c)]"
```
已注册则转 `/update_fixed_fund`。

### 3. 定位已验证归档页 URL(LLM 唯一决策点)
定位优先级链(前一级失败/无结果才降级到下一级,不并行浪费):
1. **有 ASX 代码** → `mcp__ScraplingServer__stealthy_fetch` 直抓 ASX 公司页拿官网域名(见 `skills/CLAUDE.md` 引用记忆 `asx-code-direct-fetch-over-search`),跳过关键词搜索。
2. **已知/可推测发行商域名**(如从基金名猜 issuer 官网)→ 直接 `mcp__ScraplingServer__stealthy_fetch`/`mcp__search__fetch` 该域名归档页,不经搜索。
3. **上两级均无线索** → `mcp__ScraplingServer__stealthy_fetch` 直抓搜索引擎结果页(如 DuckDuckGo HTML 版)定位官网域名——比 `mcp__search__search` 噪声更低,优先于它触发。
4. **仍无结果才兜底** `mcp__search__search`(默认引擎去 bing 降噪),最多尝试 2 次即止损,不复读同一调用(同 `~/.claude/CLAUDE.md` 全局止损规则)。

**已验证 URL 契约**:无论走哪一级,必须 fetch 过该 URL,返回 HTTP 200 + content-type + 首屏摘要(确认含多月 PDF),"我找到了X"不作数。动态页(静态 fetch 仅 1 PDF)/迭代探测派子代理(见 `skills/CLAUDE.md` §二)。

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
`--url` 走代码 curl 抓归档页;`--archive-html`(AJAX/动态页用)跳过 curl 直读子代理预抓的渲染后 HTML。**代码全自动,LLM 不干预抓取/解析/判口径**;内部机制(fallback 链、solver 求解、A4 准入)见 `skills/docs/pipeline.md`,日常跑本技能不需要读。

### 5. 补料重入 / 输出
返回 `pending_input`(如 L3 缺 fundmonitors FundID、AJAX 页缺 JSON 端点):定位该输入补料后重跑步骤 4(断点续跑)。否则按结果分流:`resolved` 月入 `monthly_returns`;非 resolved 入 `pending_review`(人工审核见下);`gaps` 非失败入 `confirmed_gaps`(`/update_fixed_fund` 每月轻量复查);`human_intervention_needed=True`(obtained 空集)提示投资者门户/联系基金管理人。

**运行期代码冻结**:本工作流执行期间禁改 `lib/`、`tests/` 任何代码——遇到大量
`no_candidates`/`pending_review`(格式漂移超出候选池覆盖),**不就地诊断改代码**,
输出诊断包(fund_id、问题月列表、`review_reason` 统计、`pending_review` id、文本
缓存路径 `data/pdf_cache/<fund_id>/*.txt`)后停止,提示用户跑 `/extend_extractor`。

## 人工通道
`pending_review` 单条审核通过后:
```bash
cd skills && FUND_DB_WRITE_TOKEN=<token> python3 -m lib.ingest promote-pending --review-id <id>
```
legacy `--extractor generic|stake|...` 首批走 `verify-extractor`,见 `skills/docs/pipeline.md` §四。
