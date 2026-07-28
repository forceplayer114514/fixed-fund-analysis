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

