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

