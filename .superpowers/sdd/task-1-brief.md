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

