# Grok 搜索引擎接入 + 错源漏洞修复 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给摄取管道加 Grok 作为第二搜索引擎（前端每次摄取可选 tavily/grok），同时修复 5 处会导致错误基金数据静默入库的漏洞，最后删除已死的 SearXNG 与低命中的 sub2api web_search。

**Architecture:** 把 `find_archive_v2` 的「搜索 + 排序」两步抽成按引擎分派的 `locate_candidates()`，「抓页 + 正则抽 PDF + 打样」两步保持两引擎共用 —— 后者天然是反捏造闸。另在 `write_extraction` 内加第五道「基金身份闸」，作为所有错源路径的统一出口兜底。

**Tech Stack:** Python 3.9 / pytest / requests / FastAPI(Pydantic v2) / sqlite3 / React+TypeScript(Vite)

## Global Constraints

- 设计依据：`docs/superpowers/specs/2026-07-26-grok-search-engine-design.md`（Spec G）。本计划的章节引用如「见 Spec G 10.5」均指该文件。
- 分支：`feat/grok-search-engine`（已创建）。
- **禁止捏造金融数据**。任何净值/收益率必须可追溯到真实抓取源。数据缺失必须报错并停止，不允许估算值/历史均值/猜测填补。（`CLAUDE.md` 一.1）
- **数据缺口零容忍**：缺月必须报错并列出具体月份，不允许跳过或插值。（`CLAUDE.md` 一.3）
- **改计算逻辑代码后先跑单元测试，全绿才允许跑端到端。**（`CLAUDE.md` 三.2）
- **RTK 会拦截 `python3 -m pytest` 导致 spawn 失败**。本计划所有测试命令一律用绕过形式：
  `python3 -c "import pytest,sys; sys.exit(pytest.main([...]))"`
- API key 一律走 `.env`，**禁止硬编码进源码**。`.env` 已 gitignore；`GROK_BASE_URL` / `GROK_API_KEY` / `GROK_MODEL` 已写入本地 `.env`。
- 类型注解写在所有函数签名上（PEP 8 + 项目 Python 规则）。
- 每个 Task 结束必须 commit。commit message 用中文正文 + 英文 type 前缀（`feat:` / `fix:` / `refactor:` / `test:` / `docs:`）。

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `llm_ingest/search.py` | 由 `tavily.py` 改名 | Tavily 检索 + 聚合站黑名单 + `TavilyError` |
| `llm_ingest/grok.py` | 新建 | Grok 客户端：调用、重试、答案解析。**只回答"东西在哪一页"，不产出 PDF 链接** |
| `llm_ingest/prompts/grok_archive.md` | 新建 | 问归档页/下载中心/官网域名的 prompt |
| `llm_ingest/prompts/grok_fundmonitors.md` | 新建 | 问 fundmonitors FundID/AccCode 的 prompt |
| `llm_ingest/verify.py` | 改 | 新增 `check_fund_identity()` —— 第五道闸的判据 |
| `llm_ingest/store.py` | 改 | `write_extraction()` 接第五道闸，参与 `all_ok` 与 `gate_summary` |
| `llm_ingest/discover.py` | 改 | 删搜索结果直接当 PDF 的捷径；Wayback 入口收窄；`engine` 贯穿 |
| `llm_ingest/discover2.py` | 改 | 新增 `locate_candidates()`；`discovered_pdfs` 只回 `matched_pdfs` |
| `llm_ingest/fundmonitors.py` | 改 | `find_fundid_via_tavily` → `find_fundid(engine=)` 分派 |
| `llm_ingest/cli.py` | 改 | `write_extraction` 调用点补第五道闸参数 |
| `webapp/backend/app/schemas.py` | 改 | `IngestRequest` 加 `search_engine` |
| `webapp/backend/app/routers/ingest.py` | 改 | 逐份身份核对；纠名判据收严；`engine` 透传 |
| `webapp/frontend/src/pages/FundManagement.tsx` | 改 | 摄取表单加引擎单选 |

---

## Task 1: 把 SEARCH_BACKEND 默认值从已死的 searxng 翻成 tavily

**背景（Spec G 2.8）：** `SEARCH_BACKEND` 在 `.env`、后端配置、shell 环境里**都没设置过**，而代码默认值是 `"searxng"`，SearXNG 服务已死（`localhost:8081` 不通，无 docker 进程）。因此当前每次搜索都抛 `TavilyError` 并静默降级到 sub2api web_search（命中率 53%、会幻觉 URL）。**Tavily 事实上根本没在跑。** 不先修这一条，后面所有 Tavily 相关测试测的都是 sub2api。

**Files:**
- Modify: `llm_ingest/tavily.py:163`
- Test: `tests/test_tavily.py:98-107`

**Interfaces:**
- Consumes: 无
- Produces: `tavily_search()` 默认后端为 Tavily（供 Task 3/5/9/10 的测试依赖）

- [ ] **Step 1: 改写现有的默认后端测试为期望 tavily**

把 `tests/test_tavily.py` 第 99-107 行整个测试函数替换为：

```python
    def test_default_backend_is_tavily(self, monkeypatch):
        """未设 SEARCH_BACKEND 时必须走 Tavily。

        Spec G 2.8: 旧默认值 "searxng" 指向已死服务 (localhost:8081 不通),
        导致每次搜索都抛 TavilyError 静默降级到 sub2api web_search。
        """
        monkeypatch.delenv("SEARCH_BACKEND", raising=False)
        mock_searxng = MagicMock(return_value=[])
        mock_tavily = MagicMock(return_value=[])
        monkeypatch.setattr(tavily_mod, "_searxng_impl", mock_searxng)
        monkeypatch.setattr(tavily_mod, "_tavily_impl", mock_tavily)
        tavily_search("q")
        mock_tavily.assert_called_once()
        mock_searxng.assert_not_called()
```

- [ ] **Step 2: 跑测试确认它失败（RED）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_tavily.py::TestTavilySearchDispatch::test_default_backend_is_tavily','-v']))"
```

预期：FAIL —— `AssertionError: Expected '_tavily_impl' to have been called once. Called 0 times.`

- [ ] **Step 3: 改默认值**

`llm_ingest/tavily.py:163`，把

```python
    backend = os.environ.get("SEARCH_BACKEND", "searxng").strip().lower()
```

改为

```python
    # 默认 tavily: SearXNG 服务已下线 (Spec G 2.8), 旧默认值 "searxng" 会让
    # 每次搜索都抛 TavilyError 并静默降级到 sub2api web_search, Tavily 形同虚设。
    backend = os.environ.get("SEARCH_BACKEND", "tavily").strip().lower()
```

同时把该函数 docstring 里的 `(默认 searxng)` 改成 `(默认 tavily)`。

- [ ] **Step 4: 跑测试确认通过（GREEN）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_tavily.py','-v']))"
```

预期：全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add llm_ingest/tavily.py tests/test_tavily.py
git commit -m "fix(search): SEARCH_BACKEND 默认值改 tavily, 修复静默降级 sub2api

SearXNG 服务已下线且该变量全仓库未设置, 旧默认值 searxng 导致每次搜索
都抛 TavilyError 降级到命中率 53% 且会幻觉 URL 的 sub2api web_search。"
```

---

## Task 2: `tavily.py` 改名为 `search.py`

**背景：** 模块名是历史遗留 —— 里面已是双后端（Tavily + SearXNG），马上还要加 Grok 分派的上游。改名让职责名副其实。**本任务只改名，一行逻辑都不动**，SearXNG 代码原样搬过去（阶段三才删）。

**Files:**
- Rename: `llm_ingest/tavily.py` → `llm_ingest/search.py`
- Modify: `llm_ingest/discover.py:31`, `llm_ingest/discover2.py:45`, `llm_ingest/fundmonitors.py:315`
- Rename: `tests/test_tavily.py` → `tests/test_search.py`

**Interfaces:**
- Consumes: Task 1 的默认值改动
- Produces: `from .search import TavilyError, TavilyResult, tavily_search, multi_query_search, AGGREGATOR_DOMAINS`

**注意：`TavilyError` / `TavilyResult` / `tavily_search()` / `multi_query_search()` 一律不改名。** 它们是三个调用点共用的符号，改名会牵动一批 import 与测试，与本计划目标无关（Spec G 4.2）。

- [ ] **Step 1: git mv 两个文件**

```bash
git mv llm_ingest/tavily.py llm_ingest/search.py
git mv tests/test_tavily.py tests/test_search.py
```

- [ ] **Step 2: 更新三处生产 import**

`llm_ingest/discover.py:31`：
```python
from .tavily import TavilyError, multi_query_search
```
改为
```python
from .search import TavilyError, multi_query_search
```

`llm_ingest/discover2.py:45`：
```python
from .tavily import TavilyError, multi_query_search
```
改为
```python
from .search import TavilyError, multi_query_search
```

`llm_ingest/fundmonitors.py:315`（在 `find_fundid_via_tavily` 函数体内）：
```python
    from .tavily import tavily_search, TavilyError
```
改为
```python
    from .search import tavily_search, TavilyError
```

- [ ] **Step 3: 更新测试里的 import**

`tests/test_search.py` 顶部，把所有 `llm_ingest.tavily` 替换为 `llm_ingest.search`：

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("tests/test_search.py")
s = p.read_text()
s = s.replace("llm_ingest.tavily", "llm_ingest.search")
s = s.replace("from llm_ingest import tavily as tavily_mod", "from llm_ingest import search as tavily_mod")
p.write_text(s)
print("done")
PY
```

- [ ] **Step 4: 确认全仓库无残留引用**

```bash
grep -rn "llm_ingest.tavily\|from .tavily\|import tavily" llm_ingest webapp tests tools 2>/dev/null | grep -v __pycache__
```

预期：无输出（若有输出，逐个改掉再继续）。

- [ ] **Step 5: 跑全量测试确认没打断任何东西**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/','-q','--no-header']))"
```

预期：与改名前同样的通过数，无新增失败。

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(search): llm_ingest/tavily.py 改名 search.py

模块早已是双后端(Tavily+SearXNG), 名字是历史遗留。本次纯改名,
逻辑零改动; TavilyError/TavilyResult/tavily_search 等符号一律不改名。"
```

---

## Task 3: 【漏洞 10.6】删除「搜索结果里的 .pdf 直接当月报」捷径

**背景（Spec G 10.6）：** `find_archive_via_search`（v1 兜底）在无 `archive_url` 且无 `latest_pdf_url` 时，直接扫搜索结果，**取第一个以 `.pdf` 结尾的 URL 当月报** —— 不抓页面、不校验域名归属、不做内容打样。

实证：Tavily 搜 "Gryphon Capital Income Trust"，排首位的是
`https://www.pricefinancial.com.au/wp-content/uploads/2024/05/Gryphon-GCI-Mar-Factsheet.pdf`
—— `pricefinancial.com.au` 是第三方理财顾问站，非发行方，仅转贴该基金资料。该例因文件名无年份、ym 解析失败而侥幸未入库；换成 `GCI-Jun-2026.pdf` 式命名即会入库。

**规矩统一为：搜索层只回答"哪一页"，PDF 链接一律只能来自真实抓取的页面 HTML。**

**已知代价（已确认接受）：** 少数原靠此捷径摸到一份月报的基金将变为该级别 0 链接、继续降级（Wayback → 本地缓存）。0 链接是可见失败会记入 `confirmed_gaps` 交人工；第三方转贴入库是不可见错误、静默污染。

**Files:**
- Modify: `llm_ingest/discover.py:423-427`
- Test: `tests/test_discover.py`（新增测试类）

**Interfaces:**
- Consumes: Task 2 的 `from .search import ...`
- Produces: `find_archive_via_search()` 不再从搜索结果直接取 PDF；其余返回结构不变

- [ ] **Step 1: 写复现测试（RED）**

在 `tests/test_discover.py` 末尾追加：

```python
class TestSearchLayerDoesNotYieldPdfLinks:
    """Spec G 10.6: 搜索层只回答"哪一页", PDF 链接只能来自真实抓取的页面 HTML。

    历史漏洞: v1 兜底直接扫搜索结果取第一个 .pdf 当月报, 不抓页不验域名。
    实证 Tavily 搜 GCI 时首位结果是第三方理财顾问站 pricefinancial.com.au
    转贴的 factsheet。
    """

    def test_third_party_pdf_in_search_results_is_not_adopted(self, monkeypatch):
        from llm_ingest import discover as disc

        third_party_pdf = (
            "https://www.pricefinancial.com.au/wp-content/uploads/"
            "2024/05/Gryphon-GCI-Jun-2026.pdf"
        )
        sources = [third_party_pdf, "https://gcapinvest.com/our-lit"]

        monkeypatch.setattr(disc, "multi_query_search", lambda *a, **k: sources)

        # 阶段 B 的 Gemini 判 JSON 返回空 -> 走兜底分支
        class _FakeResp:
            text = "{}"

        class _FakeClient:
            def messages(self, *a, **k):
                return _FakeResp()

        ptr = disc.find_archive_via_search(
            "Gryphon Capital Income Trust", "Gryphon Capital",
            client=_FakeClient(),
        )

        assert ptr.latest_pdf_url != third_party_pdf, (
            "搜索结果里的第三方 PDF 被直接当成月报采纳了 -- "
            "PDF 链接只能来自真实抓取的页面 HTML"
        )
```

- [ ] **Step 2: 跑测试确认失败（RED）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover.py::TestSearchLayerDoesNotYieldPdfLinks','-v']))"
```

预期：FAIL —— 断言消息为「搜索结果里的第三方 PDF 被直接当成月报采纳了」。

- [ ] **Step 3: 删除 PDF 捷径分支**

`llm_ingest/discover.py`，把 422-447 行这段：

```python
    # 兜底: 无 archive/latest 但有 sources, 挑相关度最高的
    if not archive_url and not latest_pdf_url and real_sources:
        # 优先 PDF
        for s in real_sources:
            if s.lower().endswith(".pdf"):
                latest_pdf_url = s
                break
        # 其次 domain 下, path 匹配基金关键词的
        if not latest_pdf_url and final_domain:
```

改为（**删掉「优先 PDF」那个 for 循环，并把下一段的 `if not latest_pdf_url and final_domain:` 改成 `if final_domain:`**）：

```python
    # 兜底: 无 archive/latest 但有 sources, 挑相关度最高的**页面**。
    #
    # Spec G 10.6: 这里曾有一段"优先 PDF"捷径 -- 直接扫 real_sources 取第一个
    # 以 .pdf 结尾的 URL 当月报, 不抓页、不验域名归属、不做内容打样。实证 Tavily
    # 搜 GCI 时首位结果是第三方理财顾问站 pricefinancial.com.au 转贴的 factsheet,
    # 一旦其文件名能解析出 ym 就会被当作官方月报入库。已删除。
    #
    # 统一规矩: 搜索层只回答"哪一页", PDF 链接一律只能来自真实抓取的页面 HTML
    # (由 discover2.probe_urls 从 <a href> 正则抽取)。
    if not archive_url and not latest_pdf_url and real_sources:
        if final_domain:
```

（该 `if` 块内部的 `fund_tokens` / `best_score` / `best_url` 逻辑与结尾的
`if best_url: latest_pdf_url = best_url` 保持原样不动 —— 它挑的是同域下的页面，不是搜索结果里的 PDF。）

- [ ] **Step 4: 跑测试确认通过（GREEN）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover.py','-v']))"
```

预期：新测试 PASS，`test_discover.py` 其余测试无新增失败。

- [ ] **Step 5: Commit**

```bash
git add llm_ingest/discover.py tests/test_discover.py
git commit -m "fix(discover): 删除搜索结果直接当 PDF 的捷径 (Spec G 10.6)

v1 兜底原会扫搜索结果取第一个 .pdf 当月报, 不抓页不验域名不打样。
实证 Tavily 搜 GCI 首位结果是第三方站 pricefinancial.com.au 转贴的
factsheet, 文件名一旦能解析出 ym 即会被当官方月报入库。

统一规矩: 搜索层只回答哪一页, PDF 链接只能来自真实抓取的页面 HTML。"
```

---

## Task 4: 【漏洞 10.3】`discovered_pdfs` 只回传与基金名匹配的 PDF

**背景（Spec G 10.3）：** `find_archive_v2` 步 5 先用
`matched_pdfs = [u for u in cand["pdf_urls"] if _pdf_slug_match_count(u, fund_name) > 0]`
筛出与基金名沾边的逐个打样，**但任一份打样通过就把该页 `pdf_urls` 全量（未经 `matched_pdfs` 过滤）塞进 `discovered_pdfs` 返回**。

下游 `probe_l1_official`（`discover.py:611-625`）遍历 `discovered_pdfs`，仅以 `_NON_MONTHLY_HINTS` 与「文件名能否解析出 ym」过滤，**不做基金名匹配** —— 同页其他基金的月报就此入库。

代码注释自述的场景（`discover2.py:409-412`）正是此情形：`yarracm.com/performance` 同挂 Enhanced Income 与 Australian Income 两支基金的月报。

**Files:**
- Modify: `llm_ingest/discover2.py:450`, `llm_ingest/discover2.py:471`
- Test: `tests/test_discover2.py`（新增测试类）

**Interfaces:**
- Consumes: 无
- Produces: `ArchivePointer.discovered_pdfs` 只含与 `fund_name` 有实义 token 交集的 PDF

- [ ] **Step 1: 写复现测试（RED）**

在 `tests/test_discover2.py` 末尾追加：

```python
class TestDiscoveredPdfsExcludeSiblingFunds:
    """Spec G 10.3: 同页多基金时, discovered_pdfs 不得带回其他基金的 PDF。

    真实场景 (discover2.py 注释自述): yarracm.com/performance 同挂
    Yarra Enhanced Income Fund 与 Yarra Australian Income Fund 两支基金月报。
    """

    def test_sibling_fund_pdfs_not_returned(self, monkeypatch):
        from llm_ingest import discover2 as d2

        target_pdf = "https://yarracm.com/docs/yarra-enhanced-income-jun-2026.pdf"
        sibling_pdf = "https://yarracm.com/docs/yarra-australian-income-jun-2026.pdf"
        page_url = "https://yarracm.com/performance"

        monkeypatch.setattr(d2, "multi_query_search", lambda *a, **k: [page_url])
        monkeypatch.setattr(
            d2, "rank_urls",
            lambda *a, **k: [{"url": page_url, "score": 90, "reason": "t"}],
        )
        # 该页抓下来含 3 份 PDF: 目标基金 1 份 + 兄弟基金 2 份
        monkeypatch.setattr(
            d2, "probe_urls",
            lambda urls, **k: [{
                "url": page_url,
                "pdf_urls": [
                    target_pdf,
                    sibling_pdf,
                    "https://yarracm.com/docs/yarra-australian-income-may-2026.pdf",
                ],
                "html": "",
            }],
        )
        # 打样一律通过 (模拟目标基金 PDF 验证成功)
        monkeypatch.setattr(
            d2, "confirm_pdf_is_monthly_report", lambda *a, **k: (True, None),
        )

        ptr = d2.find_archive_v2(
            "Yarra Enhanced Income Fund", "Yarra Capital Management",
            client=object(),
        )

        assert target_pdf in ptr.discovered_pdfs
        assert sibling_pdf not in ptr.discovered_pdfs, (
            "兄弟基金 Yarra Australian Income 的 PDF 被带回了 -- "
            "下游 probe_l1_official 不做基金名匹配, 会直接入库"
        )
```

- [ ] **Step 2: 跑测试确认失败（RED）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover2.py::TestDiscoveredPdfsExcludeSiblingFunds','-v']))"
```

预期：FAIL —— 断言消息为「兄弟基金 Yarra Australian Income 的 PDF 被带回了」。

- [ ] **Step 3: 新增 `_best_match_pdfs()` 辅助函数**

**注意：不能直接回传现有的 `matched_pdfs`。** 它的判据是 `_pdf_slug_match_count(u, fund_name) > 0`（交集非空），与 Spec G 10.1 的根因是同一个毛病 —— 分不了兄弟基金：

- 目标 `Yarra Enhanced Income Fund` → token `{yarra, enhanced, income}`
- 目标 PDF `yarra-enhanced-income-jun-2026.pdf` → 交集 3
- 兄弟 PDF `yarra-australian-income-jun-2026.pdf` → 交集 `{yarra, income}` = 2，**同样 > 0，同样会被放行**

改用**相对判据**：取该页所有 PDF 的最高匹配分，只保留并列最高的。这样 3 > 2，兄弟基金被排除；而当整页文件名都用同样的缩写（如 `GIF-Monthly` 代表 Bentham Global Income Fund）时全部并列，不会过度过滤。

在 `llm_ingest/discover2.py` 的 `_pdf_slug_match_count` 之后插入：

```python
def _best_match_pdfs(pdf_urls: List[str], fund_name: str) -> List[str]:
    """只保留与 fund_name 匹配分并列最高的 PDF (保序).

    Spec G 10.3: 不能用 `_pdf_slug_match_count(u, fund_name) > 0` 这种绝对判据 --
    它与 Spec G 10.1 的根因同病, 分不了兄弟基金:
      目标 "Yarra Enhanced Income Fund" token = {yarra, enhanced, income}
      目标 PDF  yarra-enhanced-income-jun-2026.pdf   -> 交集 3
      兄弟 PDF  yarra-australian-income-jun-2026.pdf -> 交集 2, 同样 > 0
    改用相对判据(取最高分并列): 3 > 2 排除兄弟基金; 而整页文件名统一用缩写时
    (如 GIF-Monthly 代表 Bentham Global Income Fund) 全部并列, 不会过度过滤。

    全页零匹配 -> 返回空列表 (该页与本基金无关)。
    """
    if not pdf_urls:
        return []
    scored = [(u, _pdf_slug_match_count(u, fund_name)) for u in pdf_urls]
    best = max(s for _u, s in scored)
    if best <= 0:
        return []
    return [u for u, s in scored if s == best]
```

- [ ] **Step 4: 两处 `discovered_pdfs` 改用 `_best_match_pdfs`**

`llm_ingest/discover2.py` 步 5（约 450 行），把

```python
                # 把此页所有 PDF 都带回 run_discovery, 免它再让 Gemini 解析一遍
                discovered_pdfs=list(cand["pdf_urls"]),
```

改为

```python
                # 把此页**与本基金名匹配度最高的** PDF 带回 run_discovery, 免它
                # 再让 Gemini 解析一遍。
                #
                # Spec G 10.3: 这里原本回传 cand["pdf_urls"] 全量(含同页其他基金
                # 的月报)。下游 probe_l1_official 只按 _NON_MONTHLY_HINTS 与"文件名
                # 能否解析出 ym"过滤, 不做基金名匹配 -> 兄弟基金月报直接入库。
                # 真实场景: yarracm.com/performance 同挂 Enhanced Income 与
                # Australian Income 两支基金月报。
                discovered_pdfs=_best_match_pdfs(cand["pdf_urls"], fund_name),
```

`llm_ingest/discover2.py` 步 6（约 471 行），把

```python
                # 单份场景下同页可能仍有其他 PDF (如 1-2 份), 一并带回
                discovered_pdfs=list(cand["pdf_urls"]),
```

改为

```python
                # 单份场景下同页可能仍有其他 PDF (如 1-2 份), 只带回与本基金名
                # 匹配度最高的 (Spec G 10.3, 理由同步 5)。
                discovered_pdfs=_best_match_pdfs(cand["pdf_urls"], fund_name),
```

- [ ] **Step 5: 跑测试确认通过（GREEN）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover2.py','-v']))"
```

预期：新测试 PASS，`test_discover2.py` 其余测试无新增失败。

- [ ] **Step 6: Commit**

```bash
git add llm_ingest/discover2.py tests/test_discover2.py
git commit -m "fix(discover2): discovered_pdfs 只回传匹配度最高的 PDF (Spec G 10.3)

原本任一份打样通过即把整页 pdf_urls 全量回传, 下游 probe_l1_official
不做基金名匹配, 同页兄弟基金的月报直接入库。真实场景:
yarracm.com/performance 同挂 Enhanced Income 与 Australian Income。

判据用相对分(取该页最高分并列)而非绝对分(交集>0) -- 后者与 Spec G 10.1
根因同病, 兄弟基金 yarra-australian-income 与目标交集也有 2 > 0 挡不住。"
```

---

## Task 5: 【漏洞 10.2】Wayback 入口收窄 —— 不再按整个发行商域名无差别抓

**背景（Spec G 10.2，四个漏洞里最严重的一个）：** `probe_l2_wayback()` 的 CDX 查询范围是 `{issuer_domain}/*` 与 `{issuer_domain}/wp-content/uploads/*`，即该发行商站上曾存在过的**全部** PDF。筛选条件仅三条：`statuscode:200`、`mimetype:application/pdf`、文件名解析出的 ym 落在 `gap_set` 内。

**没有基金名筛选**，也**没有接上别处已有的 `_NON_MONTHLY_HINTS` 文档类型黑名单**（该黑名单在 `discover.py:468` 定义，只在 `probe_l1_official` 与 L1_nav 用了）。

一家发行商旗下十支基金的 PDF 同处一域，只要文件名月份落在缺口内，兄弟基金月报即被当作本基金数据填入。

**加重情节：** 此步专用于补缺口，而 `CLAUDE.md` 一.3 对缺口是零容忍、禁填补的。当前实现却用全系统最宽松的条件往缺口里塞东西。

**Files:**
- Modify: `llm_ingest/discover.py:674-716`（`probe_l2_wayback` 签名与函数体）、约 885 行调用点
- Test: `tests/test_discover.py`（新增测试类）

**Interfaces:**
- Consumes: `discover2._best_match_pdfs(pdf_urls: List[str], fund_name: str) -> List[str]`（Task 4 新增）
- Produces: `probe_l2_wayback(issuer_domain: str, gap_set: Set[str], fund_name: str) -> List[Tuple[str, str]]` —— **新增第三个必填参数 `fund_name`**

- [ ] **Step 1: 写复现测试（RED）**

在 `tests/test_discover.py` 末尾追加：

```python
class TestWaybackNarrowing:
    """Spec G 10.2: Wayback 按整个发行商域名抓, 必须筛基金名与文档类型。

    该步专用于补缺口, 而 CLAUDE.md 一.3 对缺口零容忍禁填补 --
    此处却曾用全系统最宽松的条件往缺口里塞东西。
    """

    def _cdx_payload(self, originals):
        import json
        rows = [["timestamp", "original", "statuscode"]]
        for o in originals:
            rows.append(["20260701000000", o, "200"])
        return json.dumps(rows)

    def test_sibling_fund_pdf_not_used_to_fill_gap(self, monkeypatch):
        from llm_ingest import discover as disc

        target = "https://yarracm.com/docs/yarra-enhanced-income-jun-2026.pdf"
        sibling = "https://yarracm.com/docs/yarra-australian-income-jun-2026.pdf"
        monkeypatch.setattr(
            disc, "_curl",
            lambda url, timeout=30: self._cdx_payload([sibling, target]),
        )

        hits = disc.probe_l2_wayback(
            "yarracm.com", {"2026-06"}, "Yarra Enhanced Income Fund",
        )
        urls = [u for _ym, u in hits]

        assert any(target in u for u in urls), "目标基金的 PDF 应当被采纳"
        assert not any(sibling in u for u in urls), (
            "兄弟基金 Yarra Australian Income 的 PDF 被用来填缺口了"
        )

    def test_pds_tmd_not_used_to_fill_gap(self, monkeypatch):
        from llm_ingest import discover as disc

        pds = "https://yarracm.com/docs/yarra-enhanced-income-PDS-jun-2026.pdf"
        monkeypatch.setattr(
            disc, "_curl", lambda url, timeout=30: self._cdx_payload([pds]),
        )

        hits = disc.probe_l2_wayback(
            "yarracm.com", {"2026-06"}, "Yarra Enhanced Income Fund",
        )

        assert hits == [], "PDS 不是月度业绩报告, 不得用来填缺口"
```

- [ ] **Step 2: 跑测试确认失败（RED）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover.py::TestWaybackNarrowing','-v']))"
```

预期：两个测试都 FAIL —— 第一个是 `TypeError: probe_l2_wayback() takes 2 positional arguments but 3 were given`。

- [ ] **Step 3: 给 `probe_l2_wayback` 加基金名与文档类型过滤**

`llm_ingest/discover.py`，把 `probe_l2_wayback` 的签名与筛选段改为：

```python
def probe_l2_wayback(
    issuer_domain: str,
    gap_set: Set[str],
    fund_name: str,
) -> List[Tuple[str, str]]:
    """L2: 用 CDX API 查 issuer_domain 快照, 从 original URL 提月份补 gap_set 中的洞.

    每月最多 CDX_SNAPSHOTS_PER_MONTH 快照 (抄自 strategies.py 补1).

    Spec G 10.2: CDX 查的是整个发行商域名下的全部 PDF。一家发行商旗下多支基金
    的文件同处一域, 若只按"文件名月份落在缺口内"筛选, 兄弟基金的月报会被当作
    本基金数据填进缺口。而本步专用于补缺口 -- CLAUDE.md 一.3 对缺口是零容忍、
    禁填补的, 这里必须是全系统最严的地方, 不是最宽的。故加两道过滤:
      (a) _NON_MONTHLY_HINTS: 排除 PDS/TMD/FSG/研究报告等非月度业绩文档
      (b) _best_match_pdfs: 只留与 fund_name 匹配分并列最高的
          -- 必须用**相对**判据。绝对判据 (_pdf_slug_match_count > 0) 与
          Spec G 10.1 的根因同病, 挡不住兄弟基金:
            目标 "Yarra Enhanced Income Fund" -> {yarra, enhanced, income}
            yarra-enhanced-income-jun-2026.pdf   交集 3
            yarra-australian-income-jun-2026.pdf 交集 2, 同样 > 0
          故改为两趟: 先收集候选, 再按最高分筛, 最后套快照数上限。
    """
    if not gap_set or not issuer_domain:
        return []
    from .discover2 import _best_match_pdfs
    patterns = [f"{issuer_domain}/*", f"{issuer_domain}/wp-content/uploads/*"]

    # ---- 第一趟: 收集通过文档类型与月份筛选的候选 ----
    cands: List[Tuple[str, str, str]] = []  # (ym, ts, original)
    for pat in patterns:
        # https 端更稳; http 通常也可, 但本机可能被拦
        api = (
            f"https://web.archive.org/cdx/search/cdx?url={pat}"
            f"&output=json&fl=timestamp,original,statuscode"
            f"&filter=statuscode:200&filter=mimetype:application/pdf"
            f"&limit=500"
        )
        out = _curl(api, timeout=30)
        if not out:
            continue
        try:
            arr = json.loads(out)
        except json.JSONDecodeError:
            continue
        for row in arr[1:]:  # 首行表头
            if len(row) < 2:
                continue
            ts, original = row[0], row[1]
            fname = original.rsplit("/", 1)[-1]
            # (a) 文档类型: PDS/TMD/FSG/研究报告等不是月度业绩报告
            if _NON_MONTHLY_HINTS.search(fname):
                continue
            ym = _parse_ym_from_text(original)
            if not ym or ym not in gap_set:
                continue
            cands.append((ym, ts, original))

    if not cands:
        return []

    # ---- 第二趟: (b) 只留与 fund_name 匹配分并列最高的 ----
    keep = set(_best_match_pdfs([o for _ym, _ts, o in cands], fund_name))
    snap_count: Dict[str, int] = {}
    hits: List[Tuple[str, str]] = []
    for ym, ts, original in cands:
        if original not in keep:
            continue
        if snap_count.get(ym, 0) >= CDX_SNAPSHOTS_PER_MONTH:
            continue
        snap_count[ym] = snap_count.get(ym, 0) + 1
        hits.append((ym, f"https://web.archive.org/web/{ts}/{original}"))
    return _dedup_links(hits)
```

- [ ] **Step 4: 更新调用点传 `fund_name`**

`llm_ingest/discover.py` 约 885 行，把

```python
            l2_links = probe_l2_wayback(dom_clean, gap_set)
```

改为

```python
            l2_links = probe_l2_wayback(dom_clean, gap_set, fund_name)
```

- [ ] **Step 5: 跑测试确认通过（GREEN）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover.py','-v']))"
```

预期：两个新测试 PASS。若 `test_discover.py` 里已有的 wayback 测试因签名变化而失败，给它们补上 `fund_name` 实参（用与 URL slug 匹配的基金名，例如 URL 含 `yarra-enhanced` 就传 `"Yarra Enhanced Income Fund"`）。

- [ ] **Step 6: 跑全量测试**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/','-q','--no-header']))"
```

预期：无新增失败。

- [ ] **Step 7: Commit**

```bash
git add llm_ingest/discover.py tests/test_discover.py
git commit -m "fix(discover): Wayback 补缺口加基金名与文档类型过滤 (Spec G 10.2)

CDX 查的是整个发行商域名下全部 PDF, 原本只按"文件名月份落在缺口内"筛,
一家发行商旗下多支基金文件同处一域 -> 兄弟基金月报被当本基金数据填缺口。
本步专用于补缺口, 而 CLAUDE.md 一.3 对缺口零容忍禁填补, 这里必须最严。

加两道: _NON_MONTHLY_HINTS 排 PDS/TMD/研究报告; _pdf_slug_match_count
要求文件名与基金名有实义 token 交集。probe_l2_wayback 新增 fund_name 必填参数。"
```

---

## Task 6: 【漏洞 10.5，核心】加第五道「基金身份闸」到 `write_extraction`

**背景（Spec G 10.5）：** 这是四个漏洞的**共同出口兜底**，也是本计划最重要的一个任务。

现状：
- `extract_unified.md:71` 确实要求模型输出 `fund_name_text`（文档抬头基金全称原文）
- `verify.check_fund_name_token`（`verify.py:101`）确实校验它，但**只校验"该名字确实出现在文档里"**（防模型编造），**从不与目标基金比对**
- 唯一与目标基金比对之处是自动纠名分支（`ingest.py:506-528`），且：只对**第一份**文档执行；比对不通过时**仅跳过纠名**，`rename_attempted = True` 照常置位，数据继续流向 `write_extraction` **正常入库**

代码注释（`ingest.py:505`）称 `_name_matches` 为"同页多基金混淆的兜底防线"，**但它既不逐份检查，也不拒绝数据** —— 注释与实现不符。

闸 1（`check_quote_tokens`）与闸 2（`check_rolling`）检查的是"数值是否如实抄自文档"与"月度复利是否对得上滚动值"，均属数值可信度，**不涉及文档身份**。

**为什么判据不能复用 `fundmonitors._name_matches`：** 它的停用词表已排除 `income`/`enhanced`/`capital`/`australian`，"Yarra Enhanced Income Fund" 与 "Yarra Australian Income Fund" 去停用词后**双方都只剩 `{yarra}`**，交集非空即放行。该闸为跨发行商错源（Coolabah vs Smarter Money）而设，结构上分辨不了同发行商兄弟基金。

**新判据：** 保留区分性词汇，只剥结构性词（`fund`/`trust`/`class`/`wholesale` 等），要求两侧 token **逐个都能找到近似对应**。用 `difflib` 容忍用户输入拼写错误（真实案例：缓存目录里有 `stake_accumlate`，少一个 `u`），但不容忍语义不同的词。

- `enhanced` vs `{yarra, australian, income}` → 无近似匹配 → **拦下** ✓
- `accumlate` vs `{stake, accumulate}` → difflib 比值 0.947 → 视为同一词 ✓

**处置：转 `pending_review` 人工待审，既不静默入库也不静默丢弃。** 理由：`CLAUDE.md` 要求宁可报错停下不许猜测；而自动丢弃会造成静默数据缺失，同样违反缺口零容忍。系统已有 `pending_review` 表与前端审核抽屉，直接复用。

**Files:**
- Modify: `llm_ingest/verify.py`（新增 `check_fund_identity`）
- Modify: `llm_ingest/store.py:457-545`（`write_extraction`）
- Modify: `llm_ingest/cli.py:404`（调用点）
- Modify: `webapp/backend/app/routers/ingest.py:537`（调用点）
- Test: `tests/test_verify.py`、`tests/test_store.py`

**Interfaces:**
- Consumes: `verify.QuoteCheck(passed: bool, reason: str)`
- Produces:
  - `verify.check_fund_identity(fund_name_text: Optional[str], target_fund_name: str) -> QuoteCheck`
  - `store.write_extraction(..., identity_check: QuoteCheck, ...)` —— **新增必填关键字参数**
  - `gate_summary` 格式由 `"q1r1f1a1"` 变为 `"q1r1f1a1i1"`

- [ ] **Step 1: 写 `check_fund_identity` 的失败测试（RED）**

在 `tests/test_verify.py` 末尾追加：

```python
class TestCheckFundIdentity:
    """Spec G 10.5: 逐份核对文档基金身份, 兄弟基金必须被拦下。"""

    def test_exact_same_name_passes(self):
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(
            "Yarra Enhanced Income Fund", "Yarra Enhanced Income Fund")
        assert r.passed

    def test_sibling_fund_is_rejected(self):
        """核心用例: 老的 _name_matches 判据在这里会放行 (双方去停用词后
        都只剩 {yarra}), 新判据必须拦下。"""
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(
            "Yarra Australian Income Fund", "Yarra Enhanced Income Fund")
        assert not r.passed
        assert "identity_mismatch" in r.reason

    def test_structural_suffix_stripped(self):
        """份额类别等结构性后缀不影响身份判定。"""
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(
            "Bentham Syndicated Loan Fund - Wholesale Class",
            "Bentham Syndicated Loan Fund")
        assert r.passed

    def test_user_typo_tolerated(self):
        """真实案例: pdf_cache 里存在 stake_accumlate 目录 (少一个 u)。
        用户输入拼写错误不应导致全部数据转待审。"""
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(
            "Stake Accumulate Fund", "Stake Accumlate Fund")
        assert r.passed

    def test_different_issuer_rejected(self):
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(
            "Smarter Money Long-Short Credit Fund",
            "Coolabah Active Composite Bond Fund")
        assert not r.passed

    def test_empty_fund_name_text_does_not_block(self):
        """模型没读到抬头时不阻断 (与 check_fund_name_token 现有行为一致)。"""
        from llm_ingest.verify import check_fund_identity
        r = check_fund_identity(None, "Yarra Enhanced Income Fund")
        assert r.passed
        assert r.reason == "no_fund_name_text"
```

- [ ] **Step 2: 跑测试确认失败（RED）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_verify.py::TestCheckFundIdentity','-v']))"
```

预期：全部 FAIL —— `ImportError: cannot import name 'check_fund_identity'`。

- [ ] **Step 3: 实现 `check_fund_identity`**

`llm_ingest/verify.py`，在 `check_fund_name_token` 函数之后插入：

```python
# ---------- 闸 5: check_fund_identity (文档属于哪支基金) ----------
#
# Spec G 10.5: 闸 1/2/3/4 检查的都是"数值是否如实抄自文档"与"数值是否自洽",
# 全都不涉及**文档身份**。历史上唯一与目标基金比对之处只跑第一份文档, 且比对
# 不通过时仅跳过自动纠名, 数据照常入库 -> 兄弟基金月报可静默入库。
#
# 判据不复用 fundmonitors._name_matches: 它的停用词表已排除 income/enhanced/
# capital/australian, "Yarra Enhanced Income Fund" 与 "Yarra Australian Income
# Fund" 去停用词后双方都只剩 {yarra}, 交集非空即放行。那个闸是为跨发行商错源
# (2026-07-18 Coolabah 事故) 设计的, 结构上分辨不了同发行商兄弟基金。
#
# 这里改为: 只剥结构性词 (fund/trust/class/wholesale...), 保留全部区分性词汇,
# 要求两侧 token 逐个都能找到近似对应。近似用 difflib 容忍用户输入拼写错误
# (真实案例: pdf_cache 存在 stake_accumlate 目录, 少一个 u), 但不容忍语义
# 不同的词 ("enhanced" 与 "australian" 比值远低于 cutoff)。

_IDENTITY_STOPWORDS = frozenset({
    "fund", "funds", "trust", "the", "of", "and", "a", "an",
    "class", "units", "unit", "au", "aud", "nz", "nzd",
    "wholesale", "retail", "ltd", "limited", "pty", "plc",
})
_IDENTITY_CUTOFF = 0.85


def _identity_tokens(name: str) -> frozenset:
    """归一化基金名 -> 只剥结构性词后的 token 集合 (区分性词汇全部保留)."""
    if not name:
        return frozenset()
    toks = re.findall(r"[a-z0-9]+", name.lower())
    return frozenset(t for t in toks if t not in _IDENTITY_STOPWORDS and len(t) >= 2)


def _tokens_correspond(a: frozenset, b: frozenset) -> bool:
    """a 的每个 token 在 b 里都有近似对应, 且反向亦然 (对称)."""
    import difflib
    for src, dst in ((a, b), (b, a)):
        dst_list = list(dst)
        for t in src:
            if t in dst:
                continue
            if not difflib.get_close_matches(t, dst_list, n=1, cutoff=_IDENTITY_CUTOFF):
                return False
    return True


def check_fund_identity(
    fund_name_text: Optional[str],
    target_fund_name: str,
) -> QuoteCheck:
    """闸 5: 文档抬头的基金名须与本次摄取的目标基金指向同一支基金.

    fund_name_text 为空 -> 放行 (模型没读到抬头, 无从判断, 不阻断; 与
    check_fund_name_token 现有行为一致)。
    """
    if not fund_name_text:
        return QuoteCheck(True, "no_fund_name_text")
    doc = _identity_tokens(fund_name_text)
    target = _identity_tokens(target_fund_name)
    if not doc or not target:
        return QuoteCheck(True, "identity_tokens_empty")
    if _tokens_correspond(doc, target):
        return QuoteCheck(True, f"identity_ok doc={sorted(doc)}")
    return QuoteCheck(
        False,
        f"identity_mismatch doc={fund_name_text[:60]!r} "
        f"target={target_fund_name[:60]!r}",
    )
```

- [ ] **Step 4: 跑测试确认通过（GREEN）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_verify.py','-v']))"
```

预期：`TestCheckFundIdentity` 全部 PASS，`test_verify.py` 其余无新增失败。

- [ ] **Step 5: Commit（判据先落地）**

```bash
git add llm_ingest/verify.py tests/test_verify.py
git commit -m "feat(verify): 新增 check_fund_identity 基金身份判据 (Spec G 10.5)

只剥结构性词保留区分性词汇, 两侧 token 须逐个近似对应。
difflib cutoff 0.85 容忍用户输入拼写错误(stake accumlate 少一个 u),
但拦下兄弟基金(Yarra Enhanced vs Yarra Australian)。"
```

- [ ] **Step 6: 写 `write_extraction` 接第五道闸的失败测试（RED）**

在 `tests/test_store.py` 末尾追加：

```python
def test_write_extraction_identity_fail_goes_pending(conn):
    """Spec G 10.5: 身份闸未过的文档不得入 monthly_returns, 转 pending_review。"""
    ex = Extraction(
        ym="2025-03",
        net_return=0.0065,
        source_quote="Fund returned 0.65% (net of fees).",
        measure="net_monthly",
        measure_label_in_pdf="Net Return",
        rolling={"1mo": 0.65, "3mo": None, "6mo": None, "12mo": None},
        not_found=False,
        raw={"net_return_pct": 0.65},
        fund_name_text="Yarra Australian Income Fund",
    )
    q = QuoteCheck(passed=True, reason="ok")
    r = RollingCheck(passed=True, reason="ok", windows_verified=1)
    ident = verify.check_fund_identity(
        ex.fund_name_text, "Yarra Enhanced Income Fund")
    dec = store.write_extraction(
        conn, fund_id="fund_x", ex=ex,
        quote_check=q, rolling_check=r, identity_check=ident,
        monthly_history={},
    )
    assert dec.action == "pending"
    assert "identity" in dec.reason
    assert dec.gate_summary == "q1r1f1a1i0"
    n = conn.execute(
        "SELECT COUNT(*) FROM monthly_returns WHERE fund_id='fund_x'"
    ).fetchone()[0]
    assert n == 0, "身份存疑的数据不得进 monthly_returns"
```

在 `tests/test_store.py` 顶部 import 区补上（若尚未有）：

```python
from llm_ingest import verify
```

- [ ] **Step 7: 跑测试确认失败（RED）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_store.py::test_write_extraction_identity_fail_goes_pending','-v']))"
```

预期：FAIL —— `TypeError: write_extraction() got an unexpected keyword argument 'identity_check'`。

- [ ] **Step 8: 把第五道闸接进 `write_extraction`**

`llm_ingest/store.py`，签名（457-466 行）加一个必填关键字参数：

```python
def write_extraction(
    conn: sqlite3.Connection,
    *,
    fund_id: str,
    ex: Extraction,
    quote_check: QuoteCheck,
    rolling_check: RollingCheck,
    identity_check: QuoteCheck,
    monthly_history: Dict[str, float],
    exhausted_levels: str = "L1,L2,L3",
) -> WriteDecision:
```

docstring 的决策矩阵段改为：

```python
    """核心写库决策. 传入五个 check 结果, 决定 monthly / pending / gap.

    monthly_history: {ym: net_return} 已入库的历史, 用于 anti-fab 检测.
    exhausted_levels: not_found 时该月记 confirmed_gaps 的 exhausted 字段.

    决策矩阵:
      parse_error / not_found + net_return=None -> confirmed_gaps
      net_return 存在 + 五闸全过 -> monthly_returns
      net_return 存在 + 任一闸未过 -> pending_review

    identity_check (闸 5, Spec G 10.5): 文档抬头基金名是否与目标基金一致。
    设为必填参数而非可选, 是要让"不核对身份就写库"在类型层面不可能发生 --
    这道闸原本缺失, 导致兄弟基金月报可静默入库。

    reason 用 review_reason 落库: 'quote_failed' / 'rolling_failed' /
    'field_type_failed' / 'antifab_failed' / 'identity_failed' /
    'parse_error' / 'multi:xxx'
    """
```

把「四道闸」那段（约 494-505 行）改为：

```python
    # 五道闸
    f_ok, f_reason = check_field_type(ex.net_return)
    # anti-fab 用历史倒序
    hist_recent = sorted(monthly_history.items(), reverse=True)
    a_ok, a_reason = check_anti_fabrication(ex.net_return, ex.ym, hist_recent)

    summary = (
        f"q{int(quote_check.passed)}"
        f"r{int(rolling_check.passed)}"
        f"f{int(f_ok)}"
        f"a{int(a_ok)}"
        f"i{int(identity_check.passed)}"
    )
    all_ok = (
        quote_check.passed and rolling_check.passed
        and f_ok and a_ok and identity_check.passed
    )
```

把 pending 原因汇总段（约 519-528 行）里，在 `antifab` 那条之后加一条：

```python
    if not a_ok:
        reasons.append(f"antifab:{a_reason}")
    if not identity_check.passed:
        reasons.append(f"identity:{identity_check.reason}")
    reason = "|".join(reasons)
```

把 `payload` 的 `gate_reasons`（约 536-541 行）加一项：

```python
        "gate_reasons": {
            "quote": quote_check.reason,
            "rolling": rolling_check.reason,
            "field_type": f_reason,
            "antifab": a_reason,
            "identity": identity_check.reason,
        },
```

- [ ] **Step 9: 给 `test_store.py` 已有的 13 处调用补参数**

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("tests/test_store.py")
s = p.read_text()
before = s.count("rolling_check=r,")
s = s.replace(
    "rolling_check=r,",
    'rolling_check=r, identity_check=QuoteCheck(passed=True, reason="ok"),',
)
# 五闸后 gate_summary 多一位
s = s.replace('gate_summary == "q1r1f1a1"', 'gate_summary == "q1r1f1a1i1"')
s = s.replace('gate_summary == "q0r1f1a1"', 'gate_summary == "q0r1f1a1i1"')
s = s.replace('gate_summary == "q1r0f1a1"', 'gate_summary == "q1r0f1a1i1"')
s = s.replace('gate_summary == "q1r1f0a1"', 'gate_summary == "q1r1f0a1i1"')
s = s.replace('gate_summary == "q1r1f1a0"', 'gate_summary == "q1r1f1a0i1"')
p.write_text(s)
print(f"补了 {before} 处 identity_check")
PY
```

- [ ] **Step 10: 跑 store 测试，逐个修剩余失败**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_store.py','-v']))"
```

预期：全部 PASS。若仍有 `TypeError: missing keyword argument 'identity_check'`，说明该调用点的 `rolling_check=` 后面跟的不是 `r,`，手动给它补上 `identity_check=QuoteCheck(passed=True, reason="ok"),`。若有 `gate_summary` 断言失败，按实际输出补上第五位。

- [ ] **Step 11: 更新两个生产调用点**

`llm_ingest/cli.py:404` 附近，把

```python
        dec = store_mod.write_extraction(
```

的调用补上 identity 参数。先在该函数内、调用之前算出闸值（`fund_name` 变量名以该函数实际持有的目标基金名为准）：

```python
        identity = verify.check_fund_identity(ex.fund_name_text, fund_name)
        dec = store_mod.write_extraction(
            conn, fund_id=fund_id, ex=ex,
            quote_check=q, rolling_check=r, identity_check=identity,
            monthly_history=history,
        )
```

若 `cli.py` 尚未 import verify，在文件顶部 import 区加 `from . import verify`。

`webapp/backend/app/routers/ingest.py:537`，把

```python
            dec = store_mod.write_extraction(
                conn, fund_id=req.fund_id, ex=ex,
                quote_check=q, rolling_check=r,
                monthly_history=history,
            )
```

改为

```python
            # 闸 5 (Spec G 10.5): 逐份核对文档抬头基金名与目标基金是否同一支。
            # 不通过 -> write_extraction 判 pending_review 转人工, 既不静默入库
            # 也不静默丢弃 (CLAUDE.md: 宁可报错停下; 自动丢弃会造成静默缺失)。
            identity = verify.check_fund_identity(ex.fund_name_text, req.fund_name)
            if not identity.passed:
                _job_log(jid, f"[{i}/{len(links)}] {ym} identity FAIL: {identity.reason}")
            dec = store_mod.write_extraction(
                conn, fund_id=req.fund_id, ex=ex,
                quote_check=q, rolling_check=r, identity_check=identity,
                monthly_history=history,
            )
```

- [ ] **Step 12: 跑全量测试**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/','-q','--no-header']))"
```

预期：全绿。若 `tests/test_ingest_priority_l1_l2.py` 等集成测试因五闸而失败，检查是不是测试里的假 `fund_name_text` 与目标基金名不一致 —— 若是，说明闸生效了，把测试数据改成一致的名字。

- [ ] **Step 13: Commit**

```bash
git add llm_ingest/store.py llm_ingest/cli.py webapp/backend/app/routers/ingest.py tests/test_store.py
git commit -m "feat(store): write_extraction 加第五道基金身份闸 (Spec G 10.5)

四个错源漏洞的共同出口兜底。此前 fund_name_text 只验"确在文档中"(防幻觉),
从不与目标基金比对; 唯一比对处只跑第一份文档且不通过时仅跳过纠名,
数据照常入库 -> 兄弟基金月报可静默入库。

identity_check 设为必填关键字参数, 让"不核对身份就写库"在类型层面不可能。
未过转 pending_review 人工待审 -- 不静默入库也不静默丢弃。
gate_summary 由 q1r1f1a1 变为 q1r1f1a1i1。"
```

---

## Task 7: 【漏洞 10.4】自动纠名判据收严，防止改成兄弟基金

**背景（Spec G 10.4）：** 自动纠名读第一份文档的 `fund_name_text`，经 `check_fund_name_token`（验其确在文档中）+ `_name_matches`（Task 6 已证挡不住兄弟基金）后，用 `slugify_fund_id` 生成新 id 并 `rename_fund_id` **整体迁移该基金已有数据**。

若首份文档是兄弟基金的，整支基金被改名迁走 —— 这是漏洞 10.2/10.3 的放大器：单份错数据升级为整支基金身份错乱。

**修复：** 纠名判据从 `_name_matches`（交集非空）换成 Task 6 的 `check_fund_identity`。不通过时**不纠名、不阻断**，仅写入 `discovered_source_name` 供前端人工核对（该列已存在，Spec B 引入）。

**Files:**
- Modify: `webapp/backend/app/routers/ingest.py:506-528`
- Test: `webapp/backend/tests/` 或 `tests/test_ingest_rename_l2.py`

**Interfaces:**
- Consumes: `verify.check_fund_identity()`（Task 6）
- Produces: L2 自动纠名仅在身份闸通过时发生

- [ ] **Step 1: 写复现测试（RED）**

在 `tests/test_ingest_rename_l2.py` 末尾追加：

```python
class TestRenameRejectsSiblingFund:
    """Spec G 10.4: 自动纠名不得把基金改成其兄弟基金。

    _name_matches 停用词表已排除 income/enhanced, "Yarra Enhanced Income Fund"
    与 "Yarra Australian Income Fund" 双方都只剩 {yarra} -> 交集非空 -> 旧判据放行,
    整支基金会被改名迁移到兄弟基金名下。
    """

    def test_sibling_fund_name_does_not_trigger_rename(self):
        from llm_ingest import verify
        # 旧判据 (fundmonitors._name_matches) 会放行
        from llm_ingest import fundmonitors as fm
        old_ok, _ = fm._name_matches(
            "Yarra Australian Income Fund", "Yarra Enhanced Income Fund")
        assert old_ok, "前提: 旧判据确实放行 (这正是漏洞所在)"

        # 新判据必须拦下
        new = verify.check_fund_identity(
            "Yarra Australian Income Fund", "Yarra Enhanced Income Fund")
        assert not new.passed, "纠名判据必须拦下兄弟基金"
```

- [ ] **Step 2: 跑测试确认失败（RED）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_ingest_rename_l2.py::TestRenameRejectsSiblingFund','-v']))"
```

预期：若 Task 6 已完成，此测试直接 PASS（判据已存在）。**此时把它当作回归保护，继续 Step 3 改真正的调用点**；若 FAIL，说明 Task 6 未完成，先回去补。

- [ ] **Step 3: 把纠名判据换成身份闸**

`webapp/backend/app/routers/ingest.py`，把 506-528 行整段：

```python
            if not rename_attempted and ex.fund_name_text and not ex.not_found:
                name_ok = verify.check_fund_name_token(ex.fund_name_text, source_text)
                fuzzy_ok, _fuzzy_reason = fm_mod._name_matches(ex.fund_name_text, req.fund_name)
                if name_ok.passed and fuzzy_ok:
```

改为：

```python
            # Spec G 10.4: 纠名判据由 fm_mod._name_matches (去停用词后交集非空)
            # 换成 verify.check_fund_identity。前者停用词表已排除 income/enhanced/
            # capital/australian, "Yarra Enhanced Income" 与 "Yarra Australian
            # Income" 双方都只剩 {yarra} -> 交集非空 -> 放行, 会把整支基金改名
            # 迁移到兄弟基金名下 (单份错数据升级为整支基金身份错乱)。
            # 不通过时不纠名也不阻断, 仅写 discovered_source_name 供前端人工核对。
            if not rename_attempted and ex.fund_name_text and not ex.not_found:
                name_ok = verify.check_fund_name_token(ex.fund_name_text, source_text)
                ident_ok = verify.check_fund_identity(ex.fund_name_text, req.fund_name)
                if not ident_ok.passed:
                    _job_log(jid, f"rename_skipped: {ident_ok.reason}")
                    conn.execute(
                        "UPDATE funds SET discovered_source_name=? WHERE fund_id=?",
                        (ex.fund_name_text, req.fund_id),
                    )
                    conn.commit()
                if name_ok.passed and ident_ok.passed:
```

（该 `if` 之后的 `UPDATE funds ... / slugify_fund_id / rename_fund_id / old_dir.rename` 全部保持原样不动，末尾的 `rename_attempted = True` 也不动。）

- [ ] **Step 4: 跑测试**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_ingest_rename_l2.py','tests/test_ingest_priority_l1_l2.py','-v']))"
```

预期：全部 PASS。若原有纠名测试因判据收严而失败，检查其测试数据的 `fund_name_text` 与目标基金名是否指向同一支基金 —— 若确为同一支，把名字写一致；若本就是不同基金，说明闸生效了，改断言。

- [ ] **Step 5: Commit**

```bash
git add webapp/backend/app/routers/ingest.py tests/test_ingest_rename_l2.py
git commit -m "fix(ingest): 自动纠名判据换成身份闸, 防改名成兄弟基金 (Spec G 10.4)

原用 _name_matches (交集非空), 停用词表排除了 income/enhanced/australian,
兄弟基金双方都只剩发行商 token -> 放行 -> 整支基金被改名迁移。
换成 check_fund_identity; 不通过时不纠名不阻断, 只写 discovered_source_name
供前端人工核对。"
```

---

## Task 8: 新建 `llm_ingest/grok.py` —— Grok 客户端

**背景（Spec G 二、2.5、2.6）：** Grok 是 agentic search，不是检索 API。它自己在后台跑几十次搜索、读页面，然后由 LLM 给出**答案**。返回体里 `search_sources` 是探索轨迹（含噪声），`content` prose 才是答案。

**硬约束 —— Grok 只回答"东西在哪一页"，绝不问它要 PDF 文件链接。**

实测（Spec G 2.5）：问它 GCI 归档页上的月报 PDF，3 轮都返回 5 个
`gci-inv-update-{jun,may,apr,mar,feb}-2026.pdf`，并断言 *"retrieved directly from the Document Library on that page"*。而实际抓取该页数得的月报只有 **1 份**（`gci-inv-update-jun-2026.pdf`）—— 另外 4 份是它把文件名里的月份 token 替换推出来的。**最危险的是这 4 个编造 URL 全部 HTTP 200 且确实是 PDF**（该站把旧文件留在可预测路径），所以"能下载成功"完全挡不住这类捏造。

> **本任务对 Spec G 4.3/4.4 的修正：** Spec 4.3 的 `ArchiveAnswer` 草案含 `pdf_urls` 字段（当时的设计是"让它报但我们不用，只记 evidence_log"）。后经讨论改为**根本不问** —— 不给它这个题目，它就没机会编。因此 `ArchiveAnswer` **不含 `pdf_urls` 字段**，prompt 里也不得出现任何要求列举文件链接的措辞。Spec 4.4 的反捏造闸（PDF 只能来自抓取的页面 HTML）继续有效，作为纵深防御。

**Files:**
- Create: `llm_ingest/grok.py`
- Create: `llm_ingest/prompts/grok_archive.md`
- Create: `llm_ingest/prompts/grok_fundmonitors.md`
- Test: `tests/test_grok.py`

**Interfaces:**
- Consumes: `llm_ingest.client.load_env()`
- Produces:
  - `class GrokError(RuntimeError)`
  - `GrokAnswer(content: str, sources: List[str], raw: Dict[str, Any])`
  - `ArchiveAnswer(issuer_domain: Optional[str], archive_url: Optional[str], sources: List[str])`
  - `grok_ask(prompt: str, *, timeout: int = 180, retries: int = 3) -> GrokAnswer`
  - `answer_archive(fund_name: str, issuer: str, asx_code: Optional[str] = None) -> ArchiveAnswer`
  - `answer_fundmonitors_id(fund_name: str) -> Optional[Tuple[int, str]]`

- [ ] **Step 1: 写失败测试（RED）**

创建 `tests/test_grok.py`：

```python
"""Spec G: Grok agentic search 客户端. 全部 mock HTTP, 不打网络。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _resp(status: int, payload: dict | None = None, text: str = ""):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload or {}
    m.text = text or json.dumps(payload or {})
    return m


def _ok_payload(content: str, sources: list[str]):
    return {
        "choices": [{"message": {"content": content, "annotations": []}}],
        "search_sources": [{"title": "", "type": "web", "url": u} for u in sources],
    }


class TestGrokAsk:
    def test_parses_content_and_sources(self, monkeypatch):
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        payload = _ok_payload("answer text", ["https://a.com", "https://b.com"])
        with patch.object(grok.requests, "post", return_value=_resp(200, payload)) as p:
            ans = grok.grok_ask("q")
        assert ans.content == "answer text"
        assert ans.sources == ["https://a.com", "https://b.com"]
        assert p.call_count == 1

    def test_retries_on_503_then_succeeds(self, monkeypatch):
        """503 upstream_unavailable = 中转站账号额度耗尽, 重试换账号即成功
        (Spec G 2.7 实测)。"""
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        monkeypatch.setattr(grok.time, "sleep", lambda s: None)
        payload = _ok_payload("ok", ["https://a.com"])
        seq = [_resp(503, {}, "upstream_unavailable"), _resp(200, payload)]
        with patch.object(grok.requests, "post", side_effect=seq) as p:
            ans = grok.grok_ask("q")
        assert ans.content == "ok"
        assert p.call_count == 2

    def test_raises_after_retries_exhausted(self, monkeypatch):
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        monkeypatch.setattr(grok.time, "sleep", lambda s: None)
        seq = [_resp(503, {}, "upstream_unavailable")] * 4
        with patch.object(grok.requests, "post", side_effect=seq) as p:
            with pytest.raises(grok.GrokError):
                grok.grok_ask("q", retries=3)
        assert p.call_count == 4  # 首次 + 3 次重试

    def test_missing_key_raises(self, monkeypatch):
        from llm_ingest import grok
        monkeypatch.delenv("GROK_API_KEY", raising=False)
        monkeypatch.setattr(grok, "load_env", lambda: None)
        with pytest.raises(grok.GrokError):
            grok.grok_ask("q")


class TestAnswerArchive:
    def test_parses_json_answer(self, monkeypatch):
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        content = json.dumps({
            "issuer_domain": "https://gcapinvest.com",
            "archive_url": "https://gcapinvest.com/our-lit",
        })
        payload = _ok_payload(content, ["https://gcapinvest.com/our-lit"])
        with patch.object(grok.requests, "post", return_value=_resp(200, payload)):
            a = grok.answer_archive("Gryphon Capital Income Trust", "Gryphon Capital")
        assert a.archive_url == "https://gcapinvest.com/our-lit"
        assert a.issuer_domain == "https://gcapinvest.com"

    def test_falls_back_to_regex_when_not_json(self, monkeypatch):
        """Grok 有时不听话直接说人话, 正则兜底抽 URL。"""
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        content = "月报归档页是 https://gcapinvest.com/our-lit , 请前往下载。"
        payload = _ok_payload(content, ["https://gcapinvest.com/our-lit"])
        with patch.object(grok.requests, "post", return_value=_resp(200, payload)):
            a = grok.answer_archive("Gryphon Capital Income Trust", "Gryphon Capital")
        assert a.archive_url == "https://gcapinvest.com/our-lit"

    def test_answer_has_no_pdf_field(self, monkeypatch):
        """Spec G 2.5 硬约束: 绝不问 Grok 要 PDF 链接, 它会按文件名规律编造
        且编造出的 URL 能 200 下载成功。ArchiveAnswer 不得有 pdf 字段。"""
        from llm_ingest import grok
        assert not hasattr(grok.ArchiveAnswer, "pdf_urls")
        assert "pdf_urls" not in grok.ArchiveAnswer.__dataclass_fields__

    def test_prompt_does_not_ask_for_pdf_links(self):
        """prompt 里不得出现要求列举 PDF 文件链接的措辞。"""
        from pathlib import Path
        import llm_ingest
        p = Path(llm_ingest.__file__).parent / "prompts" / "grok_archive.md"
        text = p.read_text().lower()
        for bad in ("list the pdf", "pdf urls", "pdf links", "列出.*pdf"):
            assert bad not in text, f"prompt 不得索要 PDF 链接: {bad!r}"


class TestAnswerFundmonitorsId:
    def test_parses_fundid_and_acccode(self, monkeypatch):
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        content = json.dumps({"fund_id": 1512, "acc_code": "fresnjxju"})
        payload = _ok_payload(content, ["https://www.fundmonitors.com/x"])
        with patch.object(grok.requests, "post", return_value=_resp(200, payload)):
            got = grok.answer_fundmonitors_id("Yarra Enhanced Income Fund")
        assert got == (1512, "fresnjxju")

    def test_returns_none_when_not_found(self, monkeypatch):
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        payload = _ok_payload(json.dumps({"fund_id": None}), [])
        with patch.object(grok.requests, "post", return_value=_resp(200, payload)):
            assert grok.answer_fundmonitors_id("Nonexistent Fund") is None

    def test_grok_error_returns_none(self, monkeypatch):
        """上游失败不抛给调用方, 返 None 让 probe 走既有的 no_fundid 分支。"""
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        monkeypatch.setattr(grok.time, "sleep", lambda s: None)
        seq = [_resp(503, {}, "x")] * 4
        with patch.object(grok.requests, "post", side_effect=seq):
            assert grok.answer_fundmonitors_id("Any Fund") is None
```

- [ ] **Step 2: 跑测试确认失败（RED）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_grok.py','-v']))"
```

预期：全部 FAIL —— `ModuleNotFoundError: No module named 'llm_ingest.grok'`。

- [ ] **Step 3: 写两个 prompt 文件**

创建 `llm_ingest/prompts/grok_archive.md`：

```markdown
Find where {fund_name} (issuer: {issuer}{asx_hint}) publishes its MONTHLY performance
reports / investor updates on the issuer's own official website.

I need the PAGE, not files. Specifically:
- the issuer's official website domain
- the URL of the page that hosts the monthly report archive, document library,
  downloads centre, or investor updates section

Rules:
- Only the issuer's own official site. Not aggregators, not third-party
  republishers, not research houses, not news sites.
- If there is no dedicated archive page, give the product/fund page on the
  official site that links to the reports.
- Report only what you actually found. If you cannot find it, use null.

Answer with JSON only, no other text:
{"issuer_domain": "https://...", "archive_url": "https://...", "evidence": "one sentence"}
```

创建 `llm_ingest/prompts/grok_fundmonitors.md`：

```markdown
On fundmonitors.com, find the fund profile page for the Australian fund
named "{fund_name}".

fundmonitors.com fund URLs carry a FundID and an AccCode, for example:
  fund-factsheet.php?FundID=1512&AccCode=fresnjxju

I need the FundID and AccCode for this exact fund. Be careful: many Australian
funds have sibling share classes with near-identical names — return the one whose
name matches "{fund_name}" most exactly, and if you are not confident it is the
same fund, return null rather than guessing.

Answer with JSON only, no other text:
{"fund_id": <int or null>, "acc_code": "<string or empty>", "page_fund_name": "<name as shown on the page, or null>"}
```

- [ ] **Step 4: 实现 `llm_ingest/grok.py`**

```python
"""Grok agentic search 客户端 (Spec G).

Grok 与 Tavily 是两种东西, 接入时切勿按"再加一个检索 API"来理解:
  - Tavily 是检索: 给 query 返回一批 URL + snippet, 无 LLM 参与, 结果确定性高
  - Grok 是 agentic search: 自己在后台跑几十次搜索、读页面, 再由 LLM 给出**答案**。
    返回体里 search_sources 是**探索轨迹**(含噪声, 实测一次查询 24 条源里混进过
    完全无关的 SEC filing), content prose 才是**答案**。

硬约束 -- 只问"东西在哪一页", 绝不问它要 PDF 文件链接:
  实测 (Spec G 2.5) 问它 GCI 归档页上的月报 PDF, 3 轮都返回 5 个
  gci-inv-update-{jun,may,apr,mar,feb}-2026.pdf 并断言"retrieved directly from
  the Document Library on that page", 而该页真实只挂 1 份 -- 另外 4 份是把文件名
  里的月份 token 替换推出来的。**这 4 个编造 URL 全部 HTTP 200 且确实是 PDF**
  (该站把旧文件留在可预测路径), 所以"能下载成功"完全挡不住这类捏造。
  不给它这个题目, 它就没机会编。PDF 枚举一律走 discover2.probe_urls 抓页 + 正则。

503 upstream_unavailable 是中转站账号额度耗尽, 不是 Grok 能力问题 --
实测重试换账号即成功, 故 429/502/503 重试有效。
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .client import load_env

DEFAULT_BASE_URL = "https://grok2api.supernip.site"
DEFAULT_MODEL = "grok-chat-fast"
DEFAULT_TIMEOUT = 180
RETRY_STATUS = (429, 502, 503, 504)
RETRY_SLEEP = 5
PROMPT_DIR = Path(__file__).parent / "prompts"

_URL_RE = re.compile(r"https?://[^\s\)\]<>\"'，、）]+")


class GrokError(RuntimeError):
    pass


@dataclass(frozen=True)
class GrokAnswer:
    content: str                 # prose 正文 = 答案
    sources: List[str]           # search_sources = 探索轨迹 (含噪声)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArchiveAnswer:
    """Grok 对"月报归档页在哪"的回答.

    刻意不含 pdf_urls -- 见模块 docstring 的硬约束。
    """
    issuer_domain: Optional[str]
    archive_url: Optional[str]
    sources: List[str] = field(default_factory=list)
    evidence: str = ""


def _config() -> Tuple[str, str, str]:
    """返 (base_url, api_key, model). key 缺失抛 GrokError."""
    load_env()
    base = os.environ.get("GROK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    key = os.environ.get("GROK_API_KEY", "").strip()
    model = os.environ.get("GROK_MODEL", DEFAULT_MODEL)
    if not key:
        raise GrokError("GROK_API_KEY 未设置 (检查 .env)")
    return base, key, model


def grok_ask(
    prompt: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 3,
) -> GrokAnswer:
    """单次 Grok 调用. 429/502/503/504 重试, 耗尽抛 GrokError.

    注意: Grok 单次调用内部就会 fan-out 多条 query (实测一次给 24-37 条源),
    因此**不要**照抄 multi_query_search 的三次 query 模式, 一次调用即可,
    否则延迟三倍且无收益 (Spec G 2.7)。
    """
    base, key, model = _config()
    url = f"{base}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {"model": model, "messages": [{"role": "user", "content": prompt}]}

    last = ""
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.RequestException as e:
            last = f"网络错误: {e}"
            if attempt < retries:
                time.sleep(RETRY_SLEEP)
                continue
            raise GrokError(last) from e
        if r.status_code == 200:
            data = r.json()
            try:
                content = data["choices"][0]["message"].get("content") or ""
            except (KeyError, IndexError, TypeError) as e:
                raise GrokError(f"返回体结构异常: {str(data)[:200]}") from e
            sources = [
                s.get("url", "") for s in (data.get("search_sources") or [])
                if s.get("url")
            ]
            return GrokAnswer(content=content, sources=sources, raw=data)
        last = f"HTTP {r.status_code}: {r.text[:200]}"
        if r.status_code in RETRY_STATUS and attempt < retries:
            time.sleep(RETRY_SLEEP)
            continue
        raise GrokError(last)
    raise GrokError(last or "retries_exhausted")


def _load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text()


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    """从回文剥出第一个 JSON 对象, 失败返 None."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def answer_archive(
    fund_name: str,
    issuer: str,
    asx_code: Optional[str] = None,
) -> ArchiveAnswer:
    """问 Grok: 这支基金的月报归档页/下载中心在哪, 官网域名是什么.

    只问页面, 不问文件 (见模块 docstring)。
    """
    tmpl = _load_prompt("grok_archive.md")
    prompt = (
        tmpl.replace("{fund_name}", fund_name)
            .replace("{issuer}", issuer)
            .replace("{asx_hint}", f", ASX code: {asx_code}" if asx_code else "")
    )
    ans = grok_ask(prompt)
    obj = _parse_json(ans.content) or {}
    archive_url = obj.get("archive_url") or None
    issuer_domain = obj.get("issuer_domain") or None
    # 兜底: Grok 不听话直接说人话时, 正则从 prose 抽第一个 URL
    if not archive_url:
        urls = [u.rstrip(".,;") for u in _URL_RE.findall(ans.content)]
        archive_url = urls[0] if urls else None
    return ArchiveAnswer(
        issuer_domain=issuer_domain,
        archive_url=archive_url,
        sources=ans.sources,
        evidence=str(obj.get("evidence") or ""),
    )


def answer_fundmonitors_id(fund_name: str) -> Optional[Tuple[int, str]]:
    """问 Grok: 这支基金在 fundmonitors 的 FundID + AccCode.

    上游失败返 None (不抛), 让 fundmonitors.probe 走既有的 no_fundid 分支。

    注意 (Spec G 2.4): 多份额类别基金上 Grok 会给错编号 (实测 Bentham 3 轮
    给了 3315/622/3315, 而 DB 真值是 3312; Tavily 同题也拿不到 3312 --
    这是数据源本身的歧义)。下游的 name-fuzzy 闸必须保留兜错源。
    """
    tmpl = _load_prompt("grok_fundmonitors.md")
    prompt = tmpl.replace("{fund_name}", fund_name)
    try:
        ans = grok_ask(prompt)
    except GrokError:
        return None
    obj = _parse_json(ans.content) or {}
    fid = obj.get("fund_id")
    if not isinstance(fid, int):
        return None
    acc = obj.get("acc_code") or ""
    return (fid, str(acc))
```

- [ ] **Step 5: 跑测试确认通过（GREEN）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_grok.py','-v']))"
```

预期：全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add llm_ingest/grok.py llm_ingest/prompts/grok_archive.md llm_ingest/prompts/grok_fundmonitors.md tests/test_grok.py
git commit -m "feat(grok): 新增 Grok agentic search 客户端 (Spec G)

只问"东西在哪一页", 绝不索要 PDF 文件链接 -- 实测 Grok 会按文件名月份规律
编造 PDF URL 并断言是从页面取到的, 且编造出的 URL 能 200 下载成功
(该站旧文件留在可预测路径), 下载成功挡不住这类捏造。

429/502/503/504 重试 3 次 (503 是中转站账号额度耗尽, 换账号即成功)。
JSON 解析失败时正则从 prose 兜底抽 URL。"
```

---

## Task 9: 抽出 `locate_candidates()` —— 按引擎分派「定位候选页面」

**背景（Spec G 4.1）：** `find_archive_v2` 现有四步中，第 1–2 步（搜索 + Gemini 排序）本质是在产出 `(issuer_domain, 已排序的候选页面列表)`，第 3–4 步（并发抓页 + 正则抽 PDF + 打样验证）消费它。这里天然有一条接缝。

按引擎分派**只切第 1–2 步**，第 3–4 步一行不改。三个好处：
1. 拿到 Grok 的答案能力，跳过对它冗余的 Gemini 排序 —— 实测 Grok 答归档页 3 轮全对（`gcapinvest.com/our-lit`），再让 Gemini 重排是多此一举
2. 第 3–4 步成为两引擎共用的**确定性链路**，同时天然是反捏造闸 —— Grok 若在 prose 里主动提了 PDF，那些 URL 不在抓下来的页面 `<a href>` 里，自动被丢弃
3. 以后加新功能加在第 3 步之后的共用段，两个引擎自动都有

**Files:**
- Modify: `llm_ingest/discover2.py`（新增 `locate_candidates`；改 `find_archive_v2` 步 1–2）
- Test: `tests/test_discover2.py`

**Interfaces:**
- Consumes: `grok.answer_archive()`、`grok.GrokError`（Task 8）；`multi_query_search`、`_pick_issuer_domain`、`rank_urls`
- Produces:
  - `locate_candidates(fund_name: str, issuer: str, issuer_domain: Optional[str] = None, asx_code: Optional[str] = None, *, engine: str = "tavily", client: Optional[Client] = None) -> Tuple[Optional[str], List[Dict[str, Any]], Dict[str, Any]]`
  - 返回 `(issuer_domain, ranked, evidence)`；`ranked` 元素为 `{"url": str, "score": int, "reason": str}`；`evidence` 含 `engine_requested` / `engine_used` / `fallback_reason` / `sources`
  - `find_archive_v2(..., engine: str = "tavily")`

- [ ] **Step 1: 写失败测试（RED）**

在 `tests/test_discover2.py` 末尾追加：

```python
class TestLocateCandidates:
    """Spec G 4.1: 按引擎分派"定位候选页面", 下游抓页/抽 PDF/打样两引擎共用。"""

    def test_tavily_engine_uses_search_and_rank(self, monkeypatch):
        from llm_ingest import discover2 as d2
        calls = {"search": 0, "rank": 0, "grok": 0}

        def _search(*a, **k):
            calls["search"] += 1
            return ["https://issuer.com/reports"]

        def _rank(urls, *a, **k):
            calls["rank"] += 1
            return [{"url": urls[0], "score": 90, "reason": "r"}]

        monkeypatch.setattr(d2, "multi_query_search", _search)
        monkeypatch.setattr(d2, "rank_urls", _rank)
        domain, ranked, ev = d2.locate_candidates(
            "Some Fund", "Some Issuer", engine="tavily", client=object())
        assert calls["search"] == 1 and calls["rank"] == 1
        assert ranked[0]["url"] == "https://issuer.com/reports"
        assert ev["engine_used"] == "tavily"

    def test_grok_engine_skips_gemini_rank(self, monkeypatch):
        """本设计的核心收益: Grok 已排好序, 不再调 Gemini rank_urls。"""
        from llm_ingest import discover2 as d2
        from llm_ingest import grok

        called = {"rank": 0}
        monkeypatch.setattr(
            d2, "rank_urls",
            lambda *a, **k: called.__setitem__("rank", called["rank"] + 1) or [])
        monkeypatch.setattr(
            d2, "_grok_answer_archive",
            lambda *a, **k: grok.ArchiveAnswer(
                issuer_domain="https://gcapinvest.com",
                archive_url="https://gcapinvest.com/our-lit",
                sources=["https://gcapinvest.com/our-lit"],
            ))
        domain, ranked, ev = d2.locate_candidates(
            "Gryphon Capital Income Trust", "Gryphon Capital",
            engine="grok", client=object())
        assert called["rank"] == 0, "engine=grok 时不得调用 Gemini rank_urls"
        assert ranked[0]["url"] == "https://gcapinvest.com/our-lit"
        assert domain == "https://gcapinvest.com"
        assert ev["engine_used"] == "grok"

    def test_grok_failure_falls_back_to_tavily_visibly(self, monkeypatch):
        """降级必须可见 (Spec G 4.5): evidence 要记 engine_used 与 fallback_reason。"""
        from llm_ingest import discover2 as d2
        from llm_ingest import grok

        def _boom(*a, **k):
            raise grok.GrokError("HTTP 503: upstream_unavailable")

        monkeypatch.setattr(d2, "_grok_answer_archive", _boom)
        monkeypatch.setattr(
            d2, "multi_query_search", lambda *a, **k: ["https://issuer.com/reports"])
        monkeypatch.setattr(
            d2, "rank_urls",
            lambda urls, *a, **k: [{"url": urls[0], "score": 90, "reason": "r"}])

        domain, ranked, ev = d2.locate_candidates(
            "Some Fund", "Some Issuer", engine="grok", client=object())
        assert ranked, "降级后应仍有候选"
        assert ev["engine_requested"] == "grok"
        assert ev["engine_used"] == "tavily"
        assert "503" in ev["fallback_reason"]

    def test_default_engine_is_tavily(self, monkeypatch):
        from llm_ingest import discover2 as d2
        monkeypatch.setattr(d2, "multi_query_search", lambda *a, **k: ["https://x.com/a"])
        monkeypatch.setattr(
            d2, "rank_urls", lambda urls, *a, **k: [{"url": urls[0], "score": 1, "reason": ""}])
        _d, _r, ev = d2.locate_candidates("F", "I", client=object())
        assert ev["engine_used"] == "tavily"
```

- [ ] **Step 2: 跑测试确认失败（RED）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover2.py::TestLocateCandidates','-v']))"
```

预期：全部 FAIL —— `AttributeError: module 'llm_ingest.discover2' has no attribute 'locate_candidates'`。

- [ ] **Step 3: 实现 `locate_candidates`**

在 `llm_ingest/discover2.py` 的 `rank_urls` 函数之后插入：

```python
# ---- 定位候选页面: 按引擎分派 (Spec G 4.1) ----
#
# find_archive_v2 四步中, 第 1-2 步 (搜索 + Gemini 排序) 本质是产出
# (issuer_domain, 已排序候选页面), 第 3-4 步 (抓页 + 正则抽 PDF + 打样) 消费它。
# 按引擎分派只切第 1-2 步:
#   - Tavily 是检索, 给的是一堆 URL, 需要 Gemini 排序
#   - Grok 是 agentic search, 直接给答案, 已排好序, 再让 Gemini 排是多此一举
#     (实测答 GCI 归档页 3 轮全对 https://gcapinvest.com/our-lit)
# 第 3-4 步保持共用, 天然是反捏造闸: Grok 若主动提了 PDF, 那些 URL 不在抓下来的
# 页面 <a href> 里, 自动被丢弃。

def _grok_answer_archive(fund_name: str, issuer: str, asx_code: Optional[str]):
    """薄封装, 便于测试 monkeypatch (避免 patch 到 grok 模块全局)."""
    from .grok import answer_archive
    return answer_archive(fund_name, issuer, asx_code)


def _locate_via_tavily(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str],
    client: Optional[Client],
) -> Tuple[Optional[str], List[Dict[str, Any]], List[str]]:
    """Tavily 路径: 三次 query 拿 URL 池 -> 挑 issuer 域 -> Gemini 排序."""
    try:
        sources = multi_query_search(
            [fund_name, f"{fund_name} performance", f"{fund_name} monthly report"],
            max_results_per_query=5,
            exclude_aggregators=True,
        )
    except TavilyError:
        sources = []
    if not sources:
        return (issuer_domain, [], [])
    domain = _pick_issuer_domain(sources, issuer, fund_name) or issuer_domain
    ranked = rank_urls(sources, fund_name, issuer, domain, client=client)
    return (domain, ranked, sources)


def locate_candidates(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    *,
    engine: str = "tavily",
    client: Optional[Client] = None,
) -> Tuple[Optional[str], List[Dict[str, Any]], Dict[str, Any]]:
    """返 (issuer_domain, ranked, evidence).

    ranked 元素形如 {"url": str, "score": int, "reason": str}, 已排序。
    evidence 供 evidence_log 记录: engine_requested / engine_used /
    fallback_reason / sources。

    engine="grok" 且 Grok 失败时自动降级 Tavily, 但**降级必须可见** --
    evidence 里记 engine_used 与 fallback_reason, 上层再写进 job 日志。
    (Spec G 4.5: 旧代码注释禁止 SearXNG->Tavily 自动降级, 顾虑是"静默烧额度且
    故障不可见"; 这里的降级不静默, 且 Grok 的 503 是 15-20% 的高频瞬时故障,
    不降级会让相应比例的摄取直接失败。)
    """
    ev: Dict[str, Any] = {
        "engine_requested": engine,
        "engine_used": engine,
        "fallback_reason": "",
        "sources": [],
    }

    if engine == "grok":
        try:
            ans = _grok_answer_archive(fund_name, issuer, asx_code)
        except Exception as e:  # noqa: BLE001  (GrokError 及网络异常一并降级)
            ev["engine_used"] = "tavily"
            ev["fallback_reason"] = f"{type(e).__name__}: {e}"
        else:
            ev["sources"] = list(ans.sources)
            ev["grok_evidence"] = ans.evidence
            if ans.archive_url:
                ranked = [{
                    "url": ans.archive_url, "score": 100, "reason": "grok_answer",
                }]
                return (ans.issuer_domain or issuer_domain, ranked, ev)
            ev["engine_used"] = "tavily"
            ev["fallback_reason"] = "grok_no_archive_url"

    domain, ranked, sources = _locate_via_tavily(
        fund_name, issuer, issuer_domain, client)
    ev["engine_used"] = "tavily" if engine != "tavily" else "tavily"
    ev["sources"] = sources
    if not ranked:
        ev["reason"] = "搜索无结果"
    return (domain, ranked, ev)
```

- [ ] **Step 4: 让 `find_archive_v2` 改用 `locate_candidates`**

`llm_ingest/discover2.py`，把 `find_archive_v2` 的签名加上 `engine`：

```python
def find_archive_v2(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    *,
    client: Optional[Client] = None,
    top_n: int = TOP_N_PROBE,
    engine: str = "tavily",
) -> ArchivePointer:
```

把步 1 / 步 1.5 / 步 2 三段（从 `# ---- 步 1: Tavily 拿 URL ----` 到 `top_urls = [r["url"] for r in ranked[:top_n]]`）整体替换为：

```python
    # ---- 步 1+2: 定位候选页面 (按引擎分派, Spec G 4.1) ----
    domain, ranked, locate_ev = locate_candidates(
        fund_name, issuer, issuer_domain, asx_code, engine=engine, client=client,
    )
    real_sources = list(locate_ev.get("sources") or [])
    if not ranked:
        return ArchivePointer(
            archive_url=None, pagination_param=None, no_archive=True,
            latest_pdf_url=None, issuer_domain_confirmed=domain or issuer_domain,
            evidence=str(locate_ev.get("reason") or "定位无候选页面"),
            raw={"locate": locate_ev},
            search_sources=real_sources, search_queries=[],
        )
    top_urls = [r["url"] for r in ranked[:top_n]]
```

在该函数后续三处 `raw={"ranked": ranked, ...}` 里各加一项 `"locate": locate_ev`，例如：

```python
                raw={"ranked": ranked, "locate": locate_ev, "probes": [
                    {"url": p["url"], "pdf_count": len(p["pdf_urls"])} for p in probes
                ]},
```

- [ ] **Step 5: 跑测试确认通过（GREEN）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover2.py','-v']))"
```

预期：全部 PASS（含 Task 4 的兄弟基金测试）。

- [ ] **Step 6: Commit**

```bash
git add llm_ingest/discover2.py tests/test_discover2.py
git commit -m "feat(discover2): 抽出 locate_candidates 按引擎分派定位候选页 (Spec G 4.1)

只切 find_archive_v2 的第 1-2 步(搜索+排序), 第 3-4 步(抓页/抽 PDF/打样)
两引擎共用 -- 后者天然是反捏造闸。engine=grok 时跳过 Gemini rank_urls
(Grok 已排好序, 实测答 GCI 归档页 3 轮全对)。
Grok 失败自动降级 Tavily, evidence 记 engine_used + fallback_reason 保证可见。"
```

---

## Task 10: `fundmonitors` L1 加引擎分派

**背景（Spec G 4.7）：** 第三方数据站是 L1 主源（Spec B 反转优先级后），搜索用来拿 FundID + AccCode。

**name-fuzzy 闸必须保留**（`fundmonitors.py:464`）：实测 Grok 在多份额类别基金上会给错编号（Bentham 3 轮给 3315/622/3315，DB 真值 3312；**Tavily 同题也拿不到 3312** —— 数据源本身的歧义，不是 Grok 缺陷）。白名单短路（`funds.fundmonitors_fund_id`）优先级仍最高，两个引擎都绕不过。

**Files:**
- Modify: `llm_ingest/fundmonitors.py:305-330`（`find_fundid_via_tavily` → `find_fundid`）、`probe()` 签名
- Test: `tests/test_fundmonitors_probe_return.py`

**Interfaces:**
- Consumes: `grok.answer_fundmonitors_id()`（Task 8）
- Produces:
  - `find_fundid(fund_name: str, *, engine: str = "tavily") -> Optional[Tuple[int, str]]`
  - `probe(fund_name: str, fund_id: Optional[str] = None, db_conn=None, *, engine: str = "tavily") -> Dict[str, object]`
  - 保留 `find_fundid_via_tavily = find_fundid` 别名，避免打断既有调用/测试

- [ ] **Step 1: 写失败测试（RED）**

在 `tests/test_fundmonitors_probe_return.py` 末尾追加：

```python
class TestFindFundidEngineDispatch:
    """Spec G 4.7: L1 拿 FundID 支持 tavily / grok 两个引擎。"""

    def test_tavily_engine_scans_search_results(self, monkeypatch):
        from llm_ingest import fundmonitors as fm
        from llm_ingest import search as search_mod

        results = [
            search_mod.TavilyResult(
                url="https://www.fundmonitors.com/fund-factsheet.php?FundID=1512&AccCode=fresnjxju",
                title="", content=""),
        ]
        monkeypatch.setattr(search_mod, "tavily_search", lambda *a, **k: results)
        got = fm.find_fundid("Yarra Enhanced Income Fund", engine="tavily")
        assert got == (1512, "fresnjxju")

    def test_grok_engine_asks_grok(self, monkeypatch):
        from llm_ingest import fundmonitors as fm
        called = {"n": 0}

        def _ask(name):
            called["n"] += 1
            return (1512, "fresnjxju")

        monkeypatch.setattr(fm, "_grok_fundmonitors_id", _ask)
        got = fm.find_fundid("Yarra Enhanced Income Fund", engine="grok")
        assert got == (1512, "fresnjxju")
        assert called["n"] == 1

    def test_grok_returns_none_falls_through(self, monkeypatch):
        """Grok 拿不到 -> 返 None, probe 走既有 no_fundid 分支, 不抛异常。"""
        from llm_ingest import fundmonitors as fm
        monkeypatch.setattr(fm, "_grok_fundmonitors_id", lambda name: None)
        assert fm.find_fundid("Nope Fund", engine="grok") is None

    def test_default_engine_is_tavily(self, monkeypatch):
        from llm_ingest import fundmonitors as fm
        from llm_ingest import search as search_mod
        monkeypatch.setattr(search_mod, "tavily_search", lambda *a, **k: [])
        monkeypatch.setattr(
            fm, "_grok_fundmonitors_id",
            lambda name: pytest.fail("默认引擎不应调用 Grok"))
        assert fm.find_fundid("Any Fund") is None
```

在该测试文件顶部确保有 `import pytest`。

- [ ] **Step 2: 跑测试确认失败（RED）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_fundmonitors_probe_return.py::TestFindFundidEngineDispatch','-v']))"
```

预期：FAIL —— `AttributeError: module 'llm_ingest.fundmonitors' has no attribute 'find_fundid'`。

- [ ] **Step 3: 改造 `find_fundid_via_tavily` 为 `find_fundid`**

`llm_ingest/fundmonitors.py`，把 305-330 行整个函数替换为：

```python
def _grok_fundmonitors_id(fund_name: str) -> Optional[Tuple[int, str]]:
    """薄封装, 便于测试 monkeypatch."""
    from .grok import answer_fundmonitors_id
    return answer_fundmonitors_id(fund_name)


def find_fundid(
    fund_name: str,
    *,
    engine: str = "tavily",
) -> Optional[Tuple[int, str]]:
    """拿 fundmonitors FundID + AccCode. 支持 tavily / grok 两个引擎.

    fundmonitors 页面结构:
      - fund-factsheet.php?FundID=1512&AccCode=fresnjxju     (摘要, 无逐月表)
      - _ajax/_fund-profile.php?FundID=1512&AccCode=fresnjxju (AJAX, 含逐月表)
    两个 URL 里 FundID + AccCode 一致, 抓 factsheet URL 也能推出 profile URL。

    Spec G 2.4: 多份额类别基金上两个引擎都可能给错编号 (实测 Bentham,
    Grok 给 3315/622, Tavily 给 3315/622, DB 真值 3312 -- 数据源本身歧义)。
    下游 probe() 的 name-fuzzy 闸必须保留兜错源。

    返回 (fund_id, acc_code) 或 None (搜不到)。
    """
    if engine == "grok":
        return _grok_fundmonitors_id(fund_name)
    from . import search as _search
    try:
        results = _search.tavily_search(
            f"site:fundmonitors.com {fund_name}",
            max_results=8,
            search_depth="basic",
        )
    except _search.TavilyError:
        return None
    for r in results:
        m = _FUNDID_URL_RE.search(r.url)
        if m:
            fid = int(m.group(1))
            acc = m.group(2) or ""
            return (fid, acc)
    return None


# 向后兼容别名 (既有调用点/测试仍用旧名)
find_fundid_via_tavily = find_fundid
```

- [ ] **Step 4: 给 `probe()` 加 `engine` 参数并透传**

`llm_ingest/fundmonitors.py` 的 `probe()`，签名改为：

```python
def probe(
    fund_name: str,
    fund_id: Optional[str] = None,
    db_conn: Optional[sqlite3.Connection] = None,
    *,
    engine: str = "tavily",
) -> Dict[str, object]:
```

函数体里把

```python
    if hit is None:
        hit = find_fundid_via_tavily(fund_name)
```

改为

```python
    if hit is None:
        hit = find_fundid(fund_name, engine=engine)
```

- [ ] **Step 5: 跑测试确认通过（GREEN）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_fundmonitors_probe_return.py','tests/test_fundmonitors_page_name.py','-v']))"
```

预期：全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add llm_ingest/fundmonitors.py tests/test_fundmonitors_probe_return.py
git commit -m "feat(fundmonitors): L1 拿 FundID 支持 tavily/grok 引擎分派 (Spec G 4.7)

find_fundid_via_tavily 改名 find_fundid 并加 engine 参数, 保留旧名别名。
probe() 透传 engine。name-fuzzy 闸与白名单短路一律保留 --
实测多份额类别基金上两个引擎都会给错编号(数据源本身歧义)。"
```

---

## Task 11: `engine` 参数贯穿 discovery → 后端 schema → 路由

**背景（Spec G 4.6）：** 引擎选择是**每次摄取请求级**的，不落库。用显式函数参数贯穿，不用全局状态/环境变量 —— 后者在并发 job 下会串味。默认值 `"tavily"` 保证所有既有调用方（CLI、测试、旧前端）行为完全不变。

**Files:**
- Modify: `llm_ingest/discover.py`（`probe_l1_official`、`run_discovery` 加 `engine`）
- Modify: `webapp/backend/app/schemas.py:74`（`IngestRequest`）
- Modify: `webapp/backend/app/routers/ingest.py`（`fm_mod.probe` 与 `disc_mod.run_discovery` 调用点、job 日志）
- Test: `tests/test_discover.py`、`webapp/backend/tests/`

**Interfaces:**
- Consumes: `discover2.find_archive_v2(..., engine=)`（Task 9）、`fundmonitors.probe(..., engine=)`（Task 10）
- Produces:
  - `discover.probe_l1_official(..., engine: str = "tavily")`
  - `discover.run_discovery(..., engine: str = "tavily")`
  - `IngestRequest.search_engine: Literal["tavily", "grok"] = "tavily"`

- [ ] **Step 1: 写失败测试（RED）**

在 `tests/test_discover.py` 末尾追加：

```python
class TestEngineThreading:
    """Spec G 4.6: engine 参数逐层透传, 默认 tavily 保证既有行为不变。"""

    def test_run_discovery_passes_engine_to_v2(self, monkeypatch):
        from llm_ingest import discover as disc
        seen = {}

        def _fake_v2(fund_name, issuer, issuer_domain=None, asx_code=None,
                     *, client=None, top_n=4, engine="tavily"):
            seen["engine"] = engine
            return disc.ArchivePointer(
                archive_url=None, pagination_param=None, no_archive=True,
                latest_pdf_url=None, issuer_domain_confirmed=None,
                evidence="", raw={},
            )

        from llm_ingest import discover2 as d2
        monkeypatch.setattr(d2, "find_archive_v2", _fake_v2)
        monkeypatch.setattr(
            disc, "find_archive_via_search",
            lambda *a, **k: disc.ArchivePointer(
                archive_url=None, pagination_param=None, no_archive=True,
                latest_pdf_url=None, issuer_domain_confirmed=None,
                evidence="", raw={}))

        disc.run_discovery("F", "I", "fid", engine="grok", client=object())
        assert seen["engine"] == "grok"

    def test_run_discovery_default_engine_is_tavily(self, monkeypatch):
        from llm_ingest import discover as disc
        from llm_ingest import discover2 as d2
        seen = {}

        def _fake_v2(fund_name, issuer, issuer_domain=None, asx_code=None,
                     *, client=None, top_n=4, engine="tavily"):
            seen["engine"] = engine
            return disc.ArchivePointer(
                archive_url=None, pagination_param=None, no_archive=True,
                latest_pdf_url=None, issuer_domain_confirmed=None,
                evidence="", raw={})

        monkeypatch.setattr(d2, "find_archive_v2", _fake_v2)
        monkeypatch.setattr(
            disc, "find_archive_via_search",
            lambda *a, **k: disc.ArchivePointer(
                archive_url=None, pagination_param=None, no_archive=True,
                latest_pdf_url=None, issuer_domain_confirmed=None,
                evidence="", raw={}))
        disc.run_discovery("F", "I", "fid", client=object())
        assert seen["engine"] == "tavily"
```

新建 `webapp/backend/tests/test_search_engine_field.py`：

```python
"""Spec G 4.6: IngestRequest.search_engine 字段。"""
import pytest
from pydantic import ValidationError

from app.schemas import IngestRequest


def test_default_is_tavily():
    r = IngestRequest(fund_name="Some Fund")
    assert r.search_engine == "tavily"


def test_accepts_grok():
    r = IngestRequest(fund_name="Some Fund", search_engine="grok")
    assert r.search_engine == "grok"


def test_rejects_unknown_engine():
    with pytest.raises(ValidationError):
        IngestRequest(fund_name="Some Fund", search_engine="bing")
```

- [ ] **Step 2: 跑测试确认失败（RED）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover.py::TestEngineThreading','webapp/backend/tests/test_search_engine_field.py','-v']))"
```

预期：FAIL —— `TypeError: run_discovery() got an unexpected keyword argument 'engine'` 与 `ValidationError` 未抛出。

- [ ] **Step 3: `discover.py` 两个函数加 `engine`**

`probe_l1_official` 签名加参数并透传给 v2：

```python
def probe_l1_official(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    *,
    client: Optional[Client] = None,
    max_pagination: int = 8,
    engine: str = "tavily",
) -> Tuple[List[Tuple[str, str]], ArchivePointer, int]:
```

函数体里把

```python
    pointer = d2.find_archive_v2(fund_name, issuer, issuer_domain, asx_code, client=client)
```

改为

```python
    pointer = d2.find_archive_v2(
        fund_name, issuer, issuer_domain, asx_code, client=client, engine=engine,
    )
```

（v1 兜底 `find_archive_via_search` 保持 Tavily-only，不加 `engine` —— 它是 v2 完全失手时的最后兜底，多一条引擎分支只增加维护面不增加覆盖。）

`run_discovery` 签名加参数：

```python
def run_discovery(
    fund_name: str,
    issuer: str,
    fund_id: str,
    *,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    inception_ym: Optional[str] = None,
    latest_ym: Optional[str] = None,
    client: Optional[Client] = None,
    engine: str = "tavily",
) -> DiscoveryReport:
```

函数体里把

```python
    l1_links, pointer, unp = probe_l1_official(
        fund_name, issuer, issuer_domain, asx_code, client=client,
    )
```

改为

```python
    l1_links, pointer, unp = probe_l1_official(
        fund_name, issuer, issuer_domain, asx_code, client=client, engine=engine,
    )
```

并在 L1 的 `report.evidence_log.append({...})` 里加上引擎记录：

```python
    report.evidence_log.append({
        "level": "L1", "count": len(l1_links),
        "archive_url": pointer.archive_url,
        "no_archive": pointer.no_archive,
        "evidence": pointer.evidence,
        "locate": (pointer.raw or {}).get("locate", {}),
    })
```

- [ ] **Step 4: `IngestRequest` 加字段**

`webapp/backend/app/schemas.py`，在文件顶部 import 区确保有 `from typing import Literal`，然后在 `IngestRequest` 的 `inception_month` 之后加：

```python
    # Spec G 4.6: 搜索引擎选择, 每次摄取请求级, 不落库。
    # tavily = 检索型 (快, 确定性高); grok = agentic search (慢 15-20s, 但直接给答案)
    search_engine: Literal["tavily", "grok"] = "tavily"
```

- [ ] **Step 5: 路由透传**

`webapp/backend/app/routers/ingest.py`：

L1 调用点（约 203 行）：
```python
            l1_result = fm_mod.probe(req.fund_name, fund_id=req.fund_id, db_conn=conn)
```
改为
```python
            l1_result = fm_mod.probe(
                req.fund_name, fund_id=req.fund_id, db_conn=conn,
                engine=req.search_engine,
            )
```

L1 日志（约 200 行）：
```python
        _job_log(jid, "L1 fundmonitors: probing ...")
```
改为
```python
        _job_log(jid, f"L1 fundmonitors: probing (engine={req.search_engine}) ...")
```

L2 调用点（约 337 行）：
```python
            rep = disc_mod.run_discovery(
                fund_name=req.fund_name,
                issuer=issuer_for_search,
                fund_id=req.fund_id,
                issuer_domain=req.issuer_domain,
                asx_code=req.asx_code,
            )
```
改为
```python
            rep = disc_mod.run_discovery(
                fund_name=req.fund_name,
                issuer=issuer_for_search,
                fund_id=req.fund_id,
                issuer_domain=req.issuer_domain,
                asx_code=req.asx_code,
                engine=req.search_engine,
            )
```

L2 之后加降级可见性日志（紧跟 `links = rep.links` 之后）：
```python
            # Spec G 4.5: 引擎降级必须可见 -- evidence_log 之外还要写 job 日志
            for _e in rep.evidence_log:
                _loc = _e.get("locate") or {}
                if _loc.get("engine_requested") and \
                        _loc.get("engine_used") != _loc.get("engine_requested"):
                    _job_log(
                        jid,
                        f"engine fallback: {_loc['engine_requested']} -> "
                        f"{_loc['engine_used']} ({_loc.get('fallback_reason', '')})",
                    )
```

- [ ] **Step 6: 跑测试确认通过（GREEN）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/','webapp/backend/tests/','-q','--no-header']))"
```

预期：全绿。

- [ ] **Step 7: Commit**

```bash
git add llm_ingest/discover.py webapp/backend/app/schemas.py webapp/backend/app/routers/ingest.py tests/test_discover.py webapp/backend/tests/test_search_engine_field.py
git commit -m "feat(ingest): engine 参数贯穿 discovery/schema/路由 (Spec G 4.6)

IngestRequest 加 search_engine (Literal tavily|grok, 默认 tavily),
经 run_discovery -> probe_l1_official -> find_archive_v2 逐层透传;
fundmonitors.probe 同样透传。每次请求级不落库, 用显式参数不用全局状态
(并发 job 会串味)。引擎降级写 job 日志保证可见。"
```

---

## Task 12: 前端摄取表单加引擎单选

**Files:**
- Modify: `webapp/frontend/src/pages/FundManagement.tsx:21-29`（`addForm` 初始 state）+ 提交处 + 表单 JSX
- Modify: `webapp/frontend/src/types/index.ts`（若 `IngestRequest` 类型在此定义）

**Interfaces:**
- Consumes: 后端 `IngestRequest.search_engine`（Task 11）
- Produces: 摄取请求体带 `search_engine` 字段

- [ ] **Step 1: `addForm` 初始 state 加字段**

`webapp/frontend/src/pages/FundManagement.tsx` 第 21-29 行，把

```tsx
  const [addForm, setAddForm] = useState({
    fund_id: '',
    fund_name: '',
    apir_code: '',
    confirmed_url: '',
    issuer: '',
    issuer_domain: '',
    asx_code: '',
  })
```

改为

```tsx
  const [addForm, setAddForm] = useState({
    fund_id: '',
    fund_name: '',
    apir_code: '',
    confirmed_url: '',
    issuer: '',
    issuer_domain: '',
    asx_code: '',
    // Spec G: 搜索引擎选择, 每次摄取生效, 不记在基金上
    search_engine: 'tavily' as 'tavily' | 'grok',
  })
```

- [ ] **Step 2: 表单里加单选控件**

在摄取弹窗的基金名输入框之后（`showAdvanced` 折叠区**之外**，让它默认可见）插入：

```tsx
          <div className="mb-3">
            <label className="block text-sm font-medium mb-1">搜索引擎</label>
            <div className="flex gap-4">
              <label className="flex items-center gap-1 text-sm">
                <input
                  type="radio"
                  name="search_engine"
                  value="tavily"
                  checked={addForm.search_engine === 'tavily'}
                  onChange={() => setAddForm({ ...addForm, search_engine: 'tavily' })}
                />
                Tavily（快，确定性高）
              </label>
              <label className="flex items-center gap-1 text-sm">
                <input
                  type="radio"
                  name="search_engine"
                  value="grok"
                  checked={addForm.search_engine === 'grok'}
                  onChange={() => setAddForm({ ...addForm, search_engine: 'grok' })}
                />
                Grok（慢 15–20 秒，直接给答案）
              </label>
            </div>
          </div>
```

- [ ] **Step 3: 提交时带上该字段**

找到摄取提交处（构造 POST body 的地方，搜 `fund_name: addForm.fund_name`），在 body 里加：

```tsx
        search_engine: addForm.search_engine,
```

若 `webapp/frontend/src/types/index.ts` 里有 `IngestRequest` 接口，给它加：

```ts
  search_engine?: 'tavily' | 'grok'
```

- [ ] **Step 4: 类型检查 + 构建**

```bash
cd webapp/frontend && npx tsc --noEmit && npm run build
```

预期：无类型错误，构建成功。

- [ ] **Step 5: Commit**

```bash
git add webapp/frontend/src
git commit -m "feat(frontend): 摄取表单加搜索引擎单选 (tavily/grok)

默认 tavily。每次摄取生效, 不记在基金上。"
```

---

## Task 13: 端到端验证（三场景）

**前置硬条件：** Task 1–12 全部完成，且全量单元测试绿（`CLAUDE.md` 三.2：先跑单元测试，全绿才允许跑端到端）。

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/','webapp/backend/tests/','-q','--no-header']))"
```

**隔离：** 全部跑在生产库的临时副本上，`FUND_DB_PATH` 指向副本（`llm_ingest/store.py:55` 支持该环境变量，优先级最高）。**绝不写生产库。**

```bash
cp data/fund_analysis.db /tmp/e2e_grok.db
export FUND_DB_PATH=/tmp/e2e_grok.db
```

**Files:**
- Create: `scripts/e2e_grok.py`（一次性验证脚本，跑完保留供复现）

- [ ] **Step 1: E2E-1 —— L1 fundmonitors 经 Grok（有 DB 真值可对分）**

对象：Yarra Enhanced Income Fund。生产库真值：`fundmonitors_fund_id=1512`、`acc_code='fresnjxju'`、月度数据 **276 行，2003-07-31 ~ 2026-06-30**。

先在临时库里清掉白名单，强制走搜索而非短路：

```bash
python3 - <<'PY'
import os, sqlite3
conn = sqlite3.connect(os.environ["FUND_DB_PATH"])
conn.execute("UPDATE funds SET fundmonitors_fund_id=NULL, fundmonitors_acc_code=NULL "
             "WHERE fund_id='yarra_enhanced_income_fund'")
conn.commit()
print("白名单已清")
PY
```

再跑 Grok 引擎的 L1：

```bash
python3 - <<'PY'
from llm_ingest import fundmonitors as fm
got = fm.find_fundid("Yarra Enhanced Income Fund", engine="grok")
print("FundID/AccCode:", got)
assert got is not None, "Grok 未拿到 FundID"
assert got[0] == 1512, f"FundID 应为 1512, 实得 {got[0]}"
print("E2E-1 判据 1 通过")
PY
```

判据：
1. Grok 独立重新发现 `FundID=1512`（Spec G 2.4 实测 3/3 命中）
2. `probe(..., engine="grok")` 返回 `status="ok"`，name-fuzzy 闸通过
3. 写入月份数与区间对齐生产库的 276 行 / 2003-07 ~ 2026-06

- [ ] **Step 2: E2E-2 —— L2 官网归档经 Grok + 反捏造闸（最重要的一条）**

对象：Gryphon Capital Income Trust（不在 `funds` 表，走全新摄取）。

**必须先规避 L2.6 本地缓存陷阱：** `run_discovery` 末尾在 `aggregate` 为空时会扫
`data/pdf_cache/{fund_id}/*.pdf` 全量入库，而 `data/pdf_cache/gryphon_capital_income/`
**已有 88 份 PDF**。若 `fund_id` 撞名且 Grok 通路返空，会静默灌入 88 个月，
判据随即失效且**失败会伪装成成功**。因此本测试一律用不撞名的 `fund_id`：

```bash
python3 - <<'PY'
import json
from llm_ingest import discover as disc

rep = disc.run_discovery(
    fund_name="Gryphon Capital Income Trust",
    issuer="Gryphon Capital Investments",
    fund_id="gci_e2e_probe",          # 刻意不撞 pdf_cache/gryphon_capital_income
    engine="grok",
)
print("archive_url:", rep.archive_pointer.archive_url if rep.archive_pointer else None)
print("links:", len(rep.links))
for ym, url in rep.links:
    print("   ", ym, url)
print("evidence_log:", json.dumps(rep.evidence_log, ensure_ascii=False, indent=2)[:2000])

# 判据 1: 定位到官方归档页 (Spec G 2.3 实测 3/3)
assert rep.archive_pointer and rep.archive_pointer.archive_url, "未定位到归档页"
assert "gcapinvest.com" in rep.archive_pointer.archive_url, \
    f"归档页应在 gcapinvest.com, 实得 {rep.archive_pointer.archive_url}"

# 判据 2: 未走本地缓存兜底 (走了说明 Grok 通路其实返空, 结果不可信)
levels = [e.get("level") for e in rep.evidence_log]
assert "L_local" not in levels, "走了 L2.6 本地缓存兜底, 本测试结果不可信"

# 判据 3 (反捏造闸): 入库的每一份 PDF 都必须能在抓下来的页面 <a href> 里找到
html = disc._fetch(rep.archive_pointer.archive_url) or ""
whitelist = disc._extract_href_whitelist(html, rep.archive_pointer.archive_url)
for ym, url in rep.links:
    assert url in whitelist or url.lower() in whitelist, \
        f"{ym} 的 PDF 不在页面 href 白名单里, 疑似捏造: {url}"
print("E2E-2 全部判据通过: 每份 PDF 均可追溯到页面真实链接")
PY
```

判据：
1. `archive_url` 落在 `gcapinvest.com`（Spec G 2.3 实测 3 轮全对 `/our-lit`）
2. `evidence_log` 里**没有** `L_local`（未走本地缓存兜底）
3. **入库的每一份 PDF 都能在抓下来的页面 `<a href>` 白名单里找到** —— 这是反捏造闸真正起作用的证明

> 判据 3 是本次唯一能证明反捏造机制有效的手段。Grok 编造的 URL 是**能 200 下载成功的真 PDF**（该站旧文件留在可预测路径），只看"下载没报错"必然漏过去。

- [ ] **Step 3: E2E-3 —— Tavily 通路回归**

```bash
python3 -c "import os; assert os.environ.get('SEARCH_BACKEND', 'tavily') == 'tavily', '必须确认默认后端已是 tavily (Task 1)'"
```

同样两个对象各用 `engine="tavily"` 跑一次，确认改动没破坏原有通路：

```bash
python3 - <<'PY'
from llm_ingest import fundmonitors as fm
from llm_ingest import discover as disc

got = fm.find_fundid("Yarra Enhanced Income Fund", engine="tavily")
print("Tavily FundID:", got)
assert got and got[0] == 1512, f"Tavily 通路回归失败: {got}"

rep = disc.run_discovery(
    fund_name="Gryphon Capital Income Trust",
    issuer="Gryphon Capital Investments",
    fund_id="gci_e2e_probe_tavily",
    engine="tavily",
)
print("Tavily archive_url:",
      rep.archive_pointer.archive_url if rep.archive_pointer else None)
print("Tavily links:", len(rep.links))
print("E2E-3 通过")
PY
```

- [ ] **Step 4: HTML→PDF 通道未被破坏的验证**

**用户明确提醒的一条：** 只改了搜索，返回给下游的清单形状必须一模一样，HTML 转 PDF、CSV、`file://` 三条通道都要照常运行。

清单契约：`run_discovery` 交给下游的只有 `links = [(ym, url), ...]`，下游 `ingest.py` 按 URL 后缀分流（`.csv` → CSV 通道；`.html`/`.htm` → 渲染成 PDF 再走 Gemini 提取；其余当 PDF；`file://` 跳过下载）。

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_ingest_html_render_channel.py','tests/test_extract_html.py','tests/test_extract_csv.py','tests/test_extract_dispatch.py','tests/test_html_to_pdf.py','-v']))"
```

预期：全部 PASS。

- [ ] **Step 5: 清理与记录**

```bash
unset FUND_DB_PATH
rm -f /tmp/e2e_grok.db
```

把三个场景的实际输出（FundID、archive_url、links 数、判据通过情况）记进
`docs/superpowers/logs/2026-07-26-grok-e2e.md` 并提交。

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/logs/2026-07-26-grok-e2e.md scripts/e2e_grok.py
git commit -m "test(e2e): Grok 通路端到端验证三场景通过

E2E-1 Yarra 经 Grok 重新发现 FundID 1512 (清白名单强制走搜索)
E2E-2 GCI 经 Grok 定位 gcapinvest.com 归档页, 入库每份 PDF 均可追溯到
      页面 href 白名单 (反捏造闸生效; 编造的 URL 能 200 下载, 只看下载
      成功挡不住), 且未走 L2.6 本地缓存兜底
E2E-3 Tavily 通路回归无变化; HTML/CSV/file:// 三条下游通道单测全绿"
```

**若任一判据失败：** 按 `CLAUDE.md` 三.2，优先判断能否把该报错场景补成新的单元测试，而不是直接改代码再跑一遍猜测。

---

## Task 14: 删除 SearXNG 与 sub2api web_search

**前置硬条件：Task 13 三个场景全部通过。** 不通过不得进入本任务。

**背景：** SearXNG 服务已死（Spec G 2.8）；sub2api web_search 命中率仅 53%、会幻觉 URL、grounding 跳板国内卡死（Spec G 3.1）。

**Files:**
- Modify: `llm_ingest/search.py`（删 SearXNG）
- Modify: `llm_ingest/client.py`（删 grounding 相关）
- Modify: `llm_ingest/discover.py`（删 sub2api 兜底）
- Modify: `tests/test_search.py`、`tests/test_client.py`

- [ ] **Step 1: 先确认待删测试当前是通过的**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_search.py','tests/test_client.py','-v']))"
```

记录通过数。**目的是避免把本来就红的测试误当成"被我删掉的"。**

- [ ] **Step 2: 删 SearXNG**

`llm_ingest/search.py`：
- 删除 `_searxng_impl()` 整个函数
- 删除 `_host_blocked()`（仅被 SearXNG 的客户端过滤使用；删前 `grep -rn "_host_blocked" llm_ingest webapp tests` 确认无其他调用方）
- `tavily_search()` 简化为直接调 `_tavily_impl`，删掉 `SEARCH_BACKEND` 分派与 `over_fetch` 客户端过滤分支（Tavily 服务端原生支持 `exclude_domains`）：

```python
def tavily_search(
    query: str,
    *,
    max_results: int = 8,
    search_depth: str = "basic",
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> List[TavilyResult]:
    """单次 Tavily 搜索.

    Spec G: SearXNG 后端与 SEARCH_BACKEND 分派已删 -- 该服务已下线
    (localhost:8081 不通, 无 docker 进程), 且该环境变量全仓库从未设置过,
    旧默认值让每次搜索都抛 TavilyError 静默降级到 sub2api web_search。
    exclude_domains 由 Tavily 服务端原生支持, 无需客户端过滤 + over_fetch。
    """
    return _tavily_impl(
        query, max_results=max_results, search_depth=search_depth,
        include_domains=include_domains, exclude_domains=exclude_domains,
        timeout=timeout,
    )
```

- 改写模块 docstring：删掉 SearXNG 选型说明，改为说明 Tavily 与 Grok 的分工

`tests/test_search.py`：删除 `TestSearxngImpl` 类、`TestTavilySearchDispatch` 里所有 SearXNG 相关用例，保留 `test_default_backend_is_tavily`（改为断言直接调 `_tavily_impl`）与 `TestTavilyImplUnchanged`。

- [ ] **Step 3: 删 sub2api web_search**

`llm_ingest/client.py` 删除：`Client.messages_with_search()`、`_parse_grounding()`、`_is_grounding_redirect_label()`、`follow_redirect()`、`resolve_sources()`。

`llm_ingest/discover.py` 删除：`_search_and_resolve()`、`_search_with_retry()`、第 30 行的 `resolve_sources` import，以及 `find_archive_via_search()` 里的 sub2api 兜底块（`p_variants` 那段，约 372–393 行）。该函数保留，只走 Tavily：

```python
    real_sources: List[str] = []
    queries: List[str] = []
    try:
        tavily_queries = [
            fund_name,
            f"{fund_name} performance",
            f"{fund_name} monthly report",
        ]
        real_sources = multi_query_search(
            tavily_queries,
            max_results_per_query=5,
            exclude_aggregators=True,
        )
        queries = tavily_queries if real_sources else []
    except TavilyError:
        real_sources = []
    # Spec G: 此处原有 sub2api web_search 兜底 (messages_with_search + grounding
    # 展开), 已删 -- 命中率仅 53%, 会幻觉 URL (Yarra 一测抓到 yarracapital.com,
    # 真域是 yarracm.com), grounding 跳板需直连 Google 1e100.net 国内 20s 卡死。
```

`tests/test_client.py`：删除覆盖 grounding 解析的用例。

- [ ] **Step 4: 确认全仓库无残留引用**

```bash
grep -rniI "searxng\|SEARCH_BACKEND\|messages_with_search\|resolve_sources\|_parse_grounding\|follow_redirect" llm_ingest webapp tests tools 2>/dev/null | grep -v __pycache__
```

预期：无输出。`tools/rotate_proxy.py` 若命中，确认是否仅为注释 —— 是则一并清理注释。

- [ ] **Step 5: 跑全量单元测试**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/','webapp/backend/tests/','-q','--no-header']))"
```

预期：全绿。

- [ ] **Step 6: 端到端复验**

重跑 Task 13 的 E2E-1 / E2E-2 / E2E-3（同样用 `FUND_DB_PATH` 临时副本），确认删除没打断任何通路。

- [ ] **Step 7: 更新 `.env.example`**

删掉 `SEARCH_BACKEND` / `SEARXNG_URL` / `SEARXNG_ENGINES` 相关行（若有），确认 Tavily 与 Grok 两段说明齐全。

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(search): 删除 SearXNG 与 sub2api web_search (Spec G 5)

SearXNG 服务已下线(localhost:8081 不通, 无 docker 进程)且 SEARCH_BACKEND
全仓库从未设置过; sub2api web_search 命中率仅 53%、会幻觉 URL、grounding
跳板需直连 Google 国内卡死。两者均已被 Tavily + Grok 取代。

删: _searxng_impl / _host_blocked / SEARCH_BACKEND 分派 / over_fetch 客户端过滤
    Client.messages_with_search / _parse_grounding / _is_grounding_redirect_label
    follow_redirect / resolve_sources / _search_and_resolve / _search_with_retry
    find_archive_via_search 的 p_variants 兜底块

删除前后端到端三场景各跑一遍, 全部通过。"
```

---

## 完成判据

全部 Task 完成后，逐条核对 Spec G 第八节「验收标准」：

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/','webapp/backend/tests/','-q','--no-header']))"
grep -rniI "searxng\|SEARCH_BACKEND\|messages_with_search\|resolve_sources" llm_ingest webapp tests tools 2>/dev/null | grep -v __pycache__
```

预期：测试全绿；grep 无输出。
