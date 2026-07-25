# Grok 搜索引擎接入设计（Spec G）

- 日期：2026-07-26
- 分支：`feat/grok-search-engine`
- 状态：待实施

> **本文档为自包含设计**。实施者不需要读产生它的对话即可执行 —— 所有实测数据、
> 文件路径、行号、判据都写在文里。凡涉及"实测"的结论，原始脚本与输出留在
> 临时目录（会话结束即失效），关键数字已抄录进本文，以本文为准。

---

## 一、目标与背景

### 1.1 要做什么

给摄取管道加第二个搜索引擎 Grok（通过中转站 grok2api），与现有 Tavily **并存**，
用户在前端"加基金"表单里每次选一个。Grok 通路端到端验证通过后，删除两个已死/已弱的
搜索通路（SearXNG、sub2api web_search）。

### 1.2 为什么 Grok 不是"再加一个 Tavily"

这是本设计最重要的前提，实施时若忽略会做出错误的抽象。

**Tavily 是检索 API**：给 query，返回一批 URL + snippet，纯检索，无 LLM 参与。

**Grok 是 agentic search**：给它一个问题，它自己在后台跑几十次搜索、读页面内容，
然后由 LLM 给出**答案**。返回体里：

- `search_sources[]`（结构化数组，字段仅 `title` / `type` / `url`）是它的**探索轨迹**，
  天然含噪声（实测 GCI 一次查询的 24 条源里混进了完全无关的
  `stocktitan.net/sec-filings/GTY/...getty-realty`）。
- `choices[0].message.content`（prose 正文）才是**答案**，已由 Grok 排序、筛选。

因此把 `search_sources` 当 URL 池再交给 Gemini 重排是多此一举 —— Grok 已经做完了
那件事，而且做得更好（见 2.3）。

---

## 二、实测验证结论（决策依据，勿凭直觉推翻）

测试对象：`https://grok2api.supernip.site`，模型 `grok-chat-fast`，OpenAI 兼容协议。

### 2.1 snippet 缺失无影响

Grok 的 `search_sources` 只有 `title` / `type` / `url`，没有 snippet，而 Tavily 有。
**但这不构成问题**：全代码库检查确认 `TavilyResult.content`（snippet）**零消费**——

- `multi_query_search()` 只返回 `List[str]`，snippet 在函数内就被丢弃
- `find_fundid_via_tavily()` 只对 `r.url` 跑正则
- `rank_urls()` 喂给 Gemini 的是裸 URL 字符串：`"\n".join(f"- {u}" for u in urls)`，
  既不含 snippet 也不含 title

### 2.2 Gemini 的网页导航不受影响

`llm_ingest/navigate.py::navigate_one_hop` 作用在**已抓取的 HTML** 上挑同域内链，
位于搜索的下游，与换不换搜索引擎无关。本设计不触碰它。

### 2.3 Grok 答"归档页在哪"3/3 全对且稳定

问 GCI（Gryphon Capital Income Trust）的官方月报归档页，3 轮独立调用：

| 轮次 | 答案 | 轨迹条数 |
|---|---|---|
| 1 | `gcapinvest.com` / `https://gcapinvest.com/our-lit` | 37 |
| 2 | 同上 | 31 |
| 3 | 同上 | 36 |

答案 3/3 一致且正确。轨迹条数一直在变 —— **轨迹漂移不等于答案漂移**。

对照：现有 pipeline 要烧 Tavily 三次查询 + Gemini `rank_urls` + 并发抓 4 页 +
一次 PDF 打样，才走到同一个结论。

### 2.4 Grok 答 fundmonitors FundID：无歧义时 3/3 精确命中

| 基金 | DB 真值 | Grok 3 轮结果 | 判定 |
|---|---|---|---|
| Yarra Enhanced Income Fund | `1512` / `fresnjxju` | `1512`/`fresnjxju` ×3 | 3/3 精确命中 |
| Bentham Syndicated Loan Fund | `3312` | `3315` / `622` / `3315` | 全错且漂移 |

Bentham 失败的原因是该基金有多个份额类别（AUD / NZD / 机构），
**Tavily 同题也只给出 3315 和 622，同样拿不到 3312** —— 这是数据源本身的歧义，
不是 Grok 缺陷，也正是代码里保留人工白名单短路（`funds.fundmonitors_fund_id`）的原因。

结论：Grok 可以接 L1，但 name-fuzzy 闸必须保留兜错源。

### 2.5 **【关键风险】Grok 会按文件名规律编造 URL，并声称是抓来的**

问 GCI 归档页上的月报 PDF，3 轮都返回 5 个：
`gci-inv-update-{jun,may,apr,mar,feb}-2026.pdf`，并断言
*"retrieved directly from the Document Library on that page"*，
轮 3 甚至加写 *"These are the only monthly investor update PDFs confirmed and
retrieved directly from the official site"*。

实际抓取 `https://gcapinvest.com/our-lit` 数了一遍：

```
页面上真实存在的 PDF 链接数: 2
    /cmsb/media/current-product-disclosure-statement-gryphon-capital-income-trust.pdf
    /cmsb/uploads/gci-inv-update-jun-2026.pdf
其中 inv-update 月报: 1
```

**页面只有 1 份月报，Grok 报了 5 份，其中 4 份是把文件名里的月份 token 替换推出来的。**
轮 1 它自己写了 *"all follow the consistent `gci-inv-update-[month]-2...` pattern"*，
等于承认。`search_sources` 成员检查也印证：轮 2 有 4/5、轮 3 有 4/5 不在自己的轨迹里。

**最危险之处：这 4 个编造 URL 全部 HTTP 200 且确实是 PDF**（该站把旧文件留在可预测路径）。
所以"能下载成功"完全挡不住这类捏造。

这正是 `CLAUDE.md` 第六条针对的 `213bdd` 事故类型：大模型为逻辑自洽而"合理幻觉"。
**本设计对此的硬约束见 4.4。**

### 2.6 Grok 不是爬虫，给不了完整归档

GCI 本地缓存 `data/pdf_cache/gryphon_capital_income/` 有 **88 份**月报（2019-02 起），
Grok 只给 5 份并断言"这就是全部"。项目要求 36 个月连续序列，
**枚举这一步绝不能交给 Grok**，必须走确定性的抓页 + 正则。

### 2.7 性能与可用性

| 指标 | Tavily | Grok |
|---|---|---|
| 单次调用延迟 | 0.9–2.1s | 4–29s（典型 15–20s） |
| 确定性 | 2 轮字节级一致 | 轨迹漂移，答案在无歧义时稳定 |
| 失败率 | 未见失败 | 503 `upstream_unavailable` 约 15–20% |
| `site:` 运算符 | 严格生效 8/8 | 时好时坏（同题 0 条 vs 32 条） |

**503 是中转站账号额度耗尽，不是 Grok 能力问题** —— 实测重试换账号后即成功
（多次出现"第 2 次请求成功"）。所以重试有效，见 4.5。

**Grok 单次调用内部就会 fan-out 多条 query**（一次给 24–37 条源）。因此 Grok 适配器
**不得照抄 `multi_query_search` 的三次 query 模式**，一次调用即可，否则延迟三倍且无收益。

### 2.8 SearXNG 已死，且当前生产正处于静默降级状态

`http://localhost:8081` 不通（curl 返回 HTTP 000），无 docker 进程，`.env` 里也没有 `SEARXNG_URL`。

**更严重的是**：`tavily_search()` 按 `SEARCH_BACKEND` 分派，默认值是 `"searxng"`
（`llm_ingest/tavily.py:163`），而 `grep -rn SEARCH_BACKEND` 全仓库确认该变量
**在 `.env`、后端配置、shell 环境里都没有设置过**（只有 `tests/test_tavily.py` 里
用 monkeypatch 设过）。

因此当前每一次搜索调用都走向已死的 SearXNG → 抛 `TavilyError` →
**静默降级到 sub2api web_search**（命中率仅 53%、会幻觉 URL、grounding 跳板国内卡死）。
**Tavily 事实上根本没有在跑。** 本文 2.7 表格里的 Tavily 数据是显式设
`SEARCH_BACKEND=tavily` 后测得的。

含义有三：

1. 删除 SearXNG 不只是清理，是**修复一个正在发生的故障**
2. 任何"Tavily 通路回归测试"必须显式设 `SEARCH_BACKEND=tavily`，
   否则测的是 sub2api 而不是 Tavily
3. 默认值应尽早翻成 `tavily`，见七.阶段一步骤 0

---

## 三、现状：搜索在管道里的位置

### 3.1 搜索模块

全部搜索代码在 `llm_ingest/tavily.py`（模块名是历史遗留，里面已是双后端）：

- `_tavily_impl()` — Tavily REST，`TAVILY_API_KEY` 走 `.env`，服务端支持 `exclude_domains`
- `_searxng_impl()` — SearXNG，`SEARXNG_URL` 默认 `localhost:8081`（**已死，待删**）
- `tavily_search()` — 按 `SEARCH_BACKEND` 环境变量分派（默认 `searxng`）
- `multi_query_search()` — 跑一组 query，合并去重，返回 `List[str]`
- `AGGREGATOR_DOMAINS` — 17 个聚合站黑名单（morningstar / lonsec / afr 等）
- `TavilyError` — 两个后端共用异常类型

第三个通路 sub2api web_search（Gemini grounding）在 `llm_ingest/client.py`：
`messages_with_search()` + `resolve_sources()` + `_parse_grounding()`。
命中率仅 53%，会幻觉 URL，grounding 跳板需直连 Google 在国内卡死。**待删。**

### 3.2 三个搜索调用点

| # | 位置 | 用途 | 调用 |
|---|---|---|---|
| 1 | `llm_ingest/fundmonitors.py:305` `find_fundid_via_tavily()` | L1 主源：`site:fundmonitors.com <fund_name>` 拿 FundID + AccCode | `tavily_search()` |
| 2 | `llm_ingest/discover2.py:365` `find_archive_v2()` | L2 官网归档发现 v2 | `multi_query_search()` |
| 3 | `llm_ingest/discover.py:363` `find_archive_via_search()` | L2 v1 兜底 | `multi_query_search()`，失败再降 sub2api |

### 3.3 管道全貌

入口 `webapp/backend/app/routers/ingest.py:159` `_run_ingest_job()`（后台 worker 线程）：

**L1 — fundmonitors 主源**（`fm_mod.probe()`，`ingest.py:203`）
先查 `funds.fundmonitors_fund_id` 白名单短路（人工背书，免搜索）；未命中才搜索拿 FundID。
抓 AJAX 页（curl_cffi 伪装 Chrome 指纹过 Cloudflare）→ 抽 `page_fund_name` →
**name-fuzzy 闸**（token 交集为空即 `name_mismatch` 拒绝入库，白名单豁免；
这是 2026-07-18 Coolabah 173 月错源事故的防线）→ 解析逐月表 → `gate_check_table` →
`write_table_records`。L1 成功即 `return`，不做 L2 补差。

**L2 — 官网 PDF 通路**（仅 L1 未覆盖时，`discover.run_discovery`）
内部还有多层：L1 官网归档页（v2 优先，空则降 v1）→ L1.5 navigate 一跳 →
L2 Wayback CDX 补洞 → L3 fundmonitors（占位，已提到最外层）→
L2.6 本地 `pdf_cache` 兜底（`file://` URL，仍过两道闸）。

拿到 links 后逐月下载 → `extract`（PDF/HTML/CSV 分派）→ `verify.py` 两道闸
（含 `check_anti_fabrication` 禁连续相同浮点）→ `store.py` 写库 → 触发 metrics recompute。

### 3.4 `find_archive_v2` 的四步（本设计要切的地方）

`llm_ingest/discover2.py:347`：

1. `multi_query_search()` 三次 query 拿 URL 池
2. `_pick_issuer_domain()` 挑 issuer 域 + `rank_urls()` 让 Gemini 排序
3. `probe_urls()` 并发抓 top-4 页，正则抽出页面里**全部** PDF 链接
4. `confirm_pdf_is_monthly_report()` 对 PDF 打样验证是不是月报

**关键观察**：第 1–2 步本质是在产出 `(issuer_domain, ranked候选列表)`，第 3–4 步消费它。
这里天然有一条接缝。

---

## 四、设计

### 4.1 核心架构：按引擎分派"定位候选"，下游共用

把 `find_archive_v2` 的第 1–2 步抽成一个按引擎分派的函数，第 3–4 步一行不改：

```python
# llm_ingest/discover2.py
def locate_candidates(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    *,
    engine: str = "tavily",
    client: Optional[Client] = None,
) -> Tuple[Optional[str], List[Dict[str, Any]], Dict[str, Any]]:
    """返 (issuer_domain, ranked, evidence)。

    ranked 元素形如 {"url": str, "score": int, "reason": str}，已排序。
    evidence 供 evidence_log 记录（引擎、是否降级、Grok 原始答案等）。
    """
```

- **Tavily 实现**：现状原封不动 —— `multi_query_search()` 三次 query +
  `_pick_issuer_domain()` + `rank_urls()`（Gemini 排序）
- **Grok 实现**：一次 `answer_archive()` 调用，直接产出
  `(domain, [{"url": archive_url, "score": 100, "reason": "grok_answer"}])`，
  **不调 `rank_urls()`**

**为什么这个切法对**：

1. 拿到了 Grok 的答案能力，跳过了对它冗余的 Gemini 排序（2.3 已证 Grok 3/3 答对）
2. 第 3–4 步（抓页 + 正则抽全部 PDF + 打样）成为**两个引擎共用的确定性链路**，
   同时天然是反捏造闸 —— Grok 编造的 PDF 不在抓下来的页面 href 里，自动被丢弃
3. 后续加新功能加在第 3 步之后的共用段，两个引擎自动都有；只有"怎么找到候选页"
   这一件事需要分别实现，而那恰是两个引擎唯一真正不同之处

### 4.2 模块结构

| 文件 | 动作 | 内容 |
|---|---|---|
| `llm_ingest/search.py` | **新建**（由 `tavily.py` 改名而来） | Tavily 检索 + `AGGREGATOR_DOMAINS` + `TavilyError`。阶段一原样搬入含 SearXNG，阶段三才删 SearXNG 部分 |
| `llm_ingest/grok.py` | **新建** | Grok 客户端、重试、答案解析 |
| `llm_ingest/tavily.py` | **改名消失** | 阶段一 `git mv` 成 `search.py`，内容先不动 |
| `llm_ingest/discover2.py` | 改 | 加 `locate_candidates()`；`find_archive_v2` 接 `engine` 参数 |
| `llm_ingest/fundmonitors.py` | 改 | `find_fundid_via_tavily` → `find_fundid(…, engine=)` 分派 |
| `llm_ingest/discover.py` | 改 | `engine` 参数贯穿；删 sub2api 兜底块 |
| `llm_ingest/client.py` | 改 | 删 `messages_with_search` 及 grounding 辅助（**仅在阶段三**） |
| `webapp/backend/app/schemas.py` | 改 | `IngestRequest` 加 `search_engine` |
| `webapp/backend/app/routers/ingest.py` | 改 | 把 `search_engine` 传下去；job 日志记引擎与降级 |
| `webapp/frontend/src/pages/FundManagement.tsx` | 改 | 摄取表单加引擎单选 |

**引擎参数用显式函数参数贯穿，不用全局状态/环境变量** —— 因为选择是每次摄取请求级的
（见 4.6），全局状态在并发 job 下会串味。

**明确不在范围内**：`TavilyError` 不改名（不要顺手改成 `SearchError`）。它是三个调用点
共用的异常类型，改名会牵动一批 import 与测试，与本设计目标无关。函数名
`tavily_search()` / `multi_query_search()` 同理保留。

### 4.3 `llm_ingest/grok.py`

```python
GROK_ENDPOINT = "https://grok2api.supernip.site/v1/chat/completions"
GROK_MODEL = "grok-chat-fast"
# API key 走 .env 的 GROK_API_KEY，禁止硬编码

class GrokError(RuntimeError):
    pass

@dataclass(frozen=True)
class GrokAnswer:
    content: str            # prose 正文（答案）
    sources: List[str]      # search_sources 里的 URL（探索轨迹）
    raw: Dict[str, Any]

def grok_ask(prompt: str, *, timeout: int = 180, retries: int = 3) -> GrokAnswer: ...

@dataclass(frozen=True)
class ArchiveAnswer:
    issuer_domain: Optional[str]
    archive_url: Optional[str]
    pdf_urls: List[str]     # 仅记录，禁止用作下载源，见 4.4
    sources: List[str]

def answer_archive(fund_name: str, issuer: str, asx_code: Optional[str] = None) -> ArchiveAnswer: ...

def answer_fundmonitors_id(fund_name: str) -> Optional[Tuple[int, str]]: ...
```

**答案解析要求**：让 Grok 输出 JSON（prompt 里明确要求 JSON），
复用 `discover._parse_json_response()` 剥 JSON。解析失败则回退到正则从 prose 抽 URL。
两条路都要有单元测试覆盖。

**Prompt 存 `llm_ingest/prompts/` 下**，与现有 4 个 prompt 同址，便于调整：
`grok_archive.md`、`grok_fundmonitors.md`。

### 4.4 反捏造闸（硬约束，不可妥协）

由 2.5 的实测事实：Grok 会编造 PDF URL，且编造出来的 URL 能 200 下载成功。

**规则：Grok 报的 PDF URL 一律不作为下载源。**

- `ArchiveAnswer.pdf_urls` **只写进 `evidence_log`**，不进入任何自动下载/入库流程
- PDF 枚举**完全**走既有的第 3 步：`probe_urls()` 抓页 + 正则抽 `<a href>` 里的 `.pdf`
- 记一个可观测指标进 `evidence_log`：
  `grok_pdf_reported`（Grok 报了几个）、`grok_pdf_on_page`（其中几个真在页面 href 里）。
  两者差值即捏造数量，供人工审阅与后续评估 Grok 可靠性

现有代码已有对应的闸，**接上即可，不必新写防线**：

- `llm_ingest/discover.py:477` `_extract_href_whitelist(html, base_url)` — 页面 href 白名单
- `llm_ingest/discover.py:277` `_validate_url_in_sources(url, sources)` — URL 域须在轨迹内

**注意**：`archive_url` 本身（不是 PDF）可以采信 Grok 的答案，因为它随后会被真实 `_fetch()`，
抓不到或抓到的页里没有 PDF，自然在第 3–4 步失败降级 —— 有天然验证。

### 4.5 错误处理与降级

**Grok 调用重试**：HTTP 429 / 502 / 503 重试，最多 3 次，间隔 5s。
理由见 2.7：503 是中转站账号额度耗尽，换账号即成功，重试实测有效。

**重试耗尽后降级 Tavily**，并且**降级必须可见**：

- 写入 `evidence_log`：`{"level": "L1"/"L2", "engine_requested": "grok", "engine_used": "tavily", "fallback_reason": "..."}`
- 写入 job 日志（`_job_log`），用户在前端 job 面板能看到

**为什么这里允许自动降级，而 `tavily.py` 现有注释明确禁止 SearXNG→Tavily 自动降级**：
旧顾虑是"SearXNG 故障时静默烧 Tavily 免费额度且故障不可见"。本设计的降级**不静默**
（双重记录），且 Grok 的 503 是高频瞬时故障，不降级会让 15–20% 的摄取直接失败。
实施时把这个理由写进代码注释，避免后人按旧注释推翻。

### 4.6 引擎选择的传递路径

**粒度：每次摄取请求级，不落库。**

1. 前端 `FundManagement.tsx` 摄取弹窗加单选（`tavily` / `grok`），默认 `tavily`，
   加进 `addForm` 初始 state（当前在第 21 行）
2. `IngestRequest`（`webapp/backend/app/schemas.py:74`）加
   `search_engine: Literal["tavily", "grok"] = "tavily"`
3. `_run_ingest_job()` 把 `req.search_engine` 传给 `fm_mod.probe()` 和 `disc_mod.run_discovery()`
4. `run_discovery()` → `probe_l1_official()` → `find_archive_v2()` → `locate_candidates()`

默认值 `"tavily"` 保证所有现有调用方（CLI、测试、旧前端）行为完全不变。

### 4.7 L1 fundmonitors 的引擎分派

`fundmonitors.py` 的 `find_fundid_via_tavily()` 改名 `find_fundid()`，加 `engine` 参数：

- **Tavily 路径**：现状不变 —— `site:fundmonitors.com <fund_name>`，正则扫**第一个**命中的 URL
- **Grok 路径**：`answer_fundmonitors_id()` 直接问 FundID + AccCode，从答案解析

两条路出来都接进同一下游（拼 URL → 抓 AJAX → name-fuzzy 闸 → 解析逐月表），
`probe()` 的其余逻辑不动。

**name-fuzzy 闸必须保留**（`fundmonitors.py:464`）：由 2.4，Grok 在多份额类别基金上会
给错 FundID，靠这道闸拦下并降级 L2。白名单短路（`funds.fundmonitors_fund_id`）
优先级仍最高，两个引擎都绕不过它。

---

## 五、删除范围（**仅在阶段二端到端通过后执行**）

> **命名提示**：阶段一已把 `tavily.py` 改名为 `search.py`，本节所指均为改名后的 `search.py`。
> SearXNG 代码在阶段一被原样搬过去（保持行为中性），到本阶段才删。

### 5.1 SearXNG

- `search.py::_searxng_impl()` 整个函数
- `tavily_search()` 里的 `SEARCH_BACKEND` 分派逻辑与 `over_fetch` 客户端过滤分支
  （Tavily 服务端支持 `exclude_domains`，客户端过滤只为 SearXNG 存在）
- `_host_blocked()` —— 仅被客户端过滤使用，确认无其他调用方后一并删
- 环境变量 `SEARXNG_URL` / `SEARXNG_ENGINES` / `SEARCH_BACKEND` 的读取
- 模块 docstring 里关于 SearXNG 的大段选型说明改写

### 5.2 sub2api web_search

- `client.py::Client.messages_with_search()`
- `client.py::_parse_grounding()` / `_is_grounding_redirect_label()` / `follow_redirect()` / `resolve_sources()`
- `discover.py::_search_and_resolve()` / `_search_with_retry()`
- `discover.py::find_archive_via_search()` 里的 sub2api 兜底块（约 372–393 行的 `p_variants` 段），
  该函数保留，只走 Tavily
- `discover.py:30` 的 `resolve_sources` import

### 5.3 关联测试

`tests/test_tavily.py` 里覆盖 SearXNG 分派的用例、`tests/test_client.py` 里覆盖
grounding 解析的用例，随之删除或改写。**删除前先确认这些测试当前是通过的**，
避免把本来就红的测试当成"被我删掉的"。

### 5.4 删除前必须确认

`grep -rn` 全仓库确认无残留引用（含 `webapp/backend`、`tools/`、`tests/`）。
`tools/rotate_proxy.py` 提到过 SearXNG，需检查是否仅是注释。

---

## 六、测试策略

遵循 `CLAUDE.md` 三.2：先跑单元测试，全绿后才允许跑一次端到端。

### 6.1 单元测试（全部 mock HTTP，不打网络）

新建 `tests/test_grok.py`：

| 用例 | 断言 |
|---|---|
| `grok_ask` 正常 200 | 正确解析 `content` / `search_sources` |
| `grok_ask` 遇 503 后 200 | 重试生效，返回成功结果，请求次数 == 2 |
| `grok_ask` 连续 503 耗尽 | 抛 `GrokError`，请求次数 == 4（首次 + 3 重试） |
| `answer_archive` JSON 答案 | 正确提取 `issuer_domain` / `archive_url` / `pdf_urls` |
| `answer_archive` prose 答案（非 JSON） | 正则兜底能抽出 URL |
| `answer_fundmonitors_id` | 从答案里解析出 `(1512, "fresnjxju")` |
| **捏造 PDF 场景** | 喂入 2.5 的真实响应样本（Grok 报 5 个 PDF），断言 `pdf_urls` 被记录但**不出现在任何下载调用里** |

新建/扩充 `tests/test_locate_candidates.py`：

| 用例 | 断言 |
|---|---|
| `engine="tavily"` | 调用 `multi_query_search` + `rank_urls`，**不调** Grok |
| `engine="grok"` | 调用 `answer_archive`，**不调** `rank_urls`（这是本设计的核心收益，必须有测试守住） |
| `engine="grok"` 且 Grok 抛 `GrokError` | 自动降级走 Tavily 路径，且 `evidence` 里含 `engine_used="tavily"` + `fallback_reason` |
| 未传 `engine` | 默认 `tavily`，行为与改动前一致 |

扩充 `tests/test_fundmonitors_probe_return.py`：

| 用例 | 断言 |
|---|---|
| `find_fundid(engine="grok")` | 走 `answer_fundmonitors_id` |
| `find_fundid(engine="tavily")` | 走现有正则扫描 |
| Grok 给错 FundID → page_fund_name 不匹配 | `probe()` 返回 `status="name_mismatch"`，不入库 |

后端：扩充 `webapp/backend/tests/`，断言 `IngestRequest.search_engine` 默认 `"tavily"`、
非法值被 Pydantic 拒绝、`_run_ingest_job` 把值透传下去。

### 6.2 端到端测试（**最后执行**，三个场景）

**前置**：单元测试全绿。**隔离**：`FUND_DB_PATH` 指向 `data/fund_analysis.db` 的临时副本，
绝不写生产库（`llm_ingest/store.py:55` 支持该环境变量，优先级最高）。

#### E2E-1：L1 fundmonitors 经 Grok（有 DB 真值可对分）

- **对象**：Yarra Enhanced Income Fund
- **设置**：临时库里清掉该基金的 `fundmonitors_fund_id` / `fundmonitors_acc_code`，
  强制走搜索而非白名单短路
- **执行**：`search_engine="grok"` 跑一次完整摄取
- **判据**：
  1. Grok 独立重新发现 `FundID=1512`、`AccCode=fresnjxju`（2.4 实测 3/3 命中）
  2. name-fuzzy 闸通过，`status="ok"`
  3. 写入的月份数与区间与生产库的 **276 行、2003-07-31 ~ 2026-06-30** 一致
  4. job 日志记录 `engine=grok`

#### E2E-2：L2 官网归档经 Grok + 反捏造闸（**本设计最重要的一条**）

- **对象**：Gryphon Capital Income Trust（不在 `funds` 表，走全新摄取）
- **执行**：`search_engine="grok"` 跑一次完整摄取
- **判据**：
  1. `archive_pointer.archive_url == "https://gcapinvest.com/our-lit"`（2.3 实测 3/3）
  2. **实际下载并入库的月报数 == 1**（页面真实只有 `gci-inv-update-jun-2026.pdf`），
     即 Grok 报的另外 4 个编造 URL **未被下载、未入库**
  3. `evidence_log` 里 `grok_pdf_reported == 5`、`grok_pdf_on_page == 1`
  4. 入库的那 1 个月数据能通过 `verify.py` 两道闸

> E2E-2 的价值：Grok 编造的 4 个 URL 是能 200 下载成功的真 PDF，
> 所以这条测试是唯一能证明反捏造闸真的起作用的手段 —— 只看"下载没报错"会漏过去。

**E2E-2 的陷阱（务必先处理，否则判据 2 会被污染）**：
`run_discovery()` 末尾有 L2.6 本地兜底 —— 当 `aggregate` 为空时会扫
`data/pdf_cache/{fund_id}/*.pdf` 并全部当作 links（`llm_ingest/discover.py` 约 902 行起）。
而 GCI 的本地缓存目录 `data/pdf_cache/gryphon_capital_income/` **已有 88 份 PDF**。

若摄取时 `fund_id` 恰好 slugify 成 `gryphon_capital_income`，且 Grok 通路因任何原因
返回空，L2.6 就会静默灌入 88 个月，判据"只入库 1 个月"随即失效，而且**失败会伪装成成功**。

处理方式（二选一，推荐前者）：

- 用一个不与现有 `pdf_cache` 目录同名的 `fund_id`（如 `gci_e2e_probe`）跑该测试
- 或在断言里同时检查 `evidence_log` 中确实出现了 `L1` 且**没有**出现 L2.6 本地兜底记录

无论选哪种，都要断言"这 1 个月是经由 Grok→抓页→href 白名单这条路来的"，而非兜底路径。

#### E2E-3：Tavily 通路回归

同样两个对象用 `search_engine="tavily"` 各跑一次，确认改动没破坏原有通路。

**前提**：必须确认 `SEARCH_BACKEND` 已是 `tavily`（阶段一步骤 0 已翻默认值）。
否则由 2.8，测的是 sub2api 而非 Tavily，这条回归测试会失去意义。

### 6.3 端到端失败时的处理

按 `CLAUDE.md` 三.2：优先判断能否把该报错场景补成新的单元测试，而不是直接改代码再猜一遍。

---

## 七、执行顺序

分三阶段，**阶段二不通过不得进入阶段三**。

### 阶段一：建 Grok 通路（不删任何现有代码）

0. **把 `tavily_search()` 的 `SEARCH_BACKEND` 默认值从 `"searxng"` 翻成 `"tavily"`**
   （`llm_ingest/tavily.py:163` 那一行）。理由见 2.8：SearXNG 已死，当前默认值
   导致所有搜索静默降级到 sub2api。这是**改默认值，不删代码**，一行、可逆，
   且不做这一步后面的 Tavily 回归测试测不到真 Tavily。
   同步改 `tests/test_tavily.py:100` 那条"未设环境变量时"的用例预期。
1. `tavily.py` → `search.py` 改名，更新 3 处 import（`discover.py:31`、`discover2.py:45`、`fundmonitors.py:315`）。
   SearXNG 相关代码**原样搬过去**，本阶段不删，保持行为中性。
2. 新建 `llm_ingest/grok.py` + 两个 prompt 文件
3. 写 `tests/test_grok.py`，跑绿
4. `discover2.py` 加 `locate_candidates()`，`find_archive_v2` 改用它；写测试跑绿
5. `fundmonitors.py` 加 `find_fundid()` 分派；写测试跑绿
6. `engine` 参数贯穿 `discover.py` → 后端 schema → 路由；写测试跑绿
7. 前端加引擎单选
8. **全量单元测试跑绿**

每步一个 commit。

### 阶段二：端到端验证

跑 6.2 的 E2E-1 / E2E-2 / E2E-3。全部通过才进入阶段三。

### 阶段三：删除 SearXNG 与 sub2api web_search

按第五节执行，删完再跑一次全量单元测试 + 一次端到端复验。

---

## 八、验收标准

- [ ] 前端摄取表单可选 tavily / grok，默认 tavily
- [ ] `engine="grok"` 时不调用 `rank_urls`（有测试守住）
- [ ] Grok 报的 PDF URL 永不作为下载源，仅记入 `evidence_log`（有测试守住）
- [ ] Grok 失败重试 3 次后降级 Tavily，降级在 `evidence_log` 与 job 日志双重可见
- [ ] E2E-1：Yarra 经 Grok 重新发现 FundID 1512，入库 276 行匹配生产库
- [ ] E2E-2：GCI 经 Grok 定位到 `gcapinvest.com/our-lit`，**只入库 1 个月**，捏造的 4 个 URL 被挡
- [ ] E2E-3：Tavily 通路回归无变化
- [ ] SearXNG 与 sub2api web_search 代码及其测试清除，全仓库无残留引用
- [ ] `SEARCH_BACKEND` 默认值不再指向已死的 SearXNG（2.8 的静默降级故障已修复）
- [ ] 全量单元测试绿

---

## 九、未决/风险

1. **Grok API key 与端点写进 `.env`**（`GROK_API_KEY`、可选 `GROK_BASE_URL`），
   同步更新 `.env.example`。禁止硬编码进源码。
2. **中转站稳定性不在我们控制内**。若 503 率显著高于实测的 15–20%，
   降级路径会频繁触发，届时 Grok 的实际价值需重新评估 —— `evidence_log` 里的
   `engine_used` 字段就是评估依据。
3. **Grok 延迟 15–20s/次**，摄取 job 是后台线程不阻塞前端，可接受；
   但若将来要批量摄取几十支基金，需要重新考虑并发策略。
4. **Bentham 类多份额基金**两个引擎都定位不准，只能靠人工白名单，本设计不解决该问题。
