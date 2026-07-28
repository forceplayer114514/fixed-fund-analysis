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

