# Task 1 报告：SEARCH_BACKEND 默认值改 tavily

## 实现内容

按 brief 精确执行：

1. `tests/test_tavily.py`：把 `TestTavilySearchDispatch::test_default_backend_is_searxng`
   替换为 `test_default_backend_is_tavily`（未设 SEARCH_BACKEND 时断言走
   `_tavily_impl`、不走 `_searxng_impl`），按 brief 给的代码逐字写入。
2. `llm_ingest/tavily.py:163`：`os.environ.get("SEARCH_BACKEND", "searxng")`
   改为 `os.environ.get("SEARCH_BACKEND", "tavily")`，并加注释说明原因
   （SearXNG 已下线，旧默认值静默降级到 sub2api）。同时把 `tavily_search()`
   函数 docstring 里的 `(默认 searxng)` 改成 `(默认 tavily)`。

### 超出 brief 字面范围的一处小改动

`tests/test_tavily.py` 顶部模块 docstring（第 1-5 行）原文写着
"SearXNG 换血做主搜索，Tavily 降级为应急回退"，这与刚改的默认值/测试直接
矛盾（同一文件里几行之后的测试就在断言默认走 Tavily）。为避免这段过时说明
误导后续任务的 agent，顺手补了一句说明现状（SearXNG 已下线、默认值改回
tavily）。**未触碰** `llm_ingest/tavily.py` 顶部那段更大的选型说明
（第 1-20 行，"默认后端 SearXNG..."），因为 Task 2（改名 search.py）和
Task 14（删除 SearXNG）会整体重写这段，现在动它是过度设计。

## 测试

**RED（Step 2，改默认值之前）：**
```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_tavily.py::TestTavilySearchDispatch::test_default_backend_is_tavily','-v']))"
```
```
FAILED tests/test_tavily.py::TestTavilySearchDispatch::test_default_backend_is_tavily
AssertionError: Expected 'mock' to have been called once. Called 0 times.
```
（`mock_tavily.assert_called_once()` 失败，因为默认值仍是 "searxng"）

**GREEN（Step 4，改默认值之后，全量跑）：**
```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_tavily.py','-v']))"
```
```
17 passed, 1 warning in 0.05s
```
全部 17 个用例（包括 `TestHostBlocked`/`TestSearxngImpl`/
`TestTavilySearchDispatch`/`TestTavilyImplUnchanged`）通过，无回归。

## 改动文件

- `llm_ingest/tavily.py`（+4 -2）
- `tests/test_tavily.py`（+13 -5，含上述模块 docstring 微调）

## 自查（Self-Review）

- **完整性**：brief 的 5 个 step 全部按顺序执行，RED→GREEN 流程完整。
- **YAGNI**：只改了默认值这一处逻辑 + 两处文档字符串（函数级按 brief 要求，
  模块级为避免直接矛盾顺手改），没有借机重构 `tavily_search`/`_searxng_impl`
  等其他逻辑，没有碰 `llm_ingest/tavily.py` 顶部大段选型说明。
- **下游影响排查**：`grep -rn SEARCH_BACKEND` 全仓库确认——除了
  `llm_ingest/tavily.py`、`tests/test_tavily.py`、以及计划/spec 文档外，
  没有任何 `.env`/webapp 后端配置/shell 脚本设置过这个变量，符合 brief 里
  "都没设置过" 的前提，改默认值不会与任何显式配置冲突。
- **测试有效性**：新测试通过 mock `_searxng_impl`/`_tavily_impl` 并断言
  调用/未调用关系，直接验证分派行为而非只测返回值，能在默认值改回
  "searxng" 时真实失败（已用 RED 阶段验证过）。
- **提交范围**：`git add` 只显式加了 `llm_ingest/tavily.py` 和
  `tests/test_tavily.py` 两个文件；工作区里另外存在的
  `.superpowers/sdd/progress.md`、`.superpowers/sdd/task-1-brief.md` 的改动
  （SDD 编排流程在本任务开始前已写入，非本次代码改动产物）以及两个
  `data/fund_analysis.db.spec_b_*_backup_*` 未跟踪备份文件，均未纳入本次
  commit。

## 遗留/后续任务的关注点

- 无阻塞性问题。
- `llm_ingest/tavily.py` 顶部模块 docstring（"默认后端 SearXNG，可切回
  Tavily"）现在与运行时默认值不一致，但这是 Task 2/14 计划内要处理的范围，
  这里特意没有动，留给后续 task 一并重写。
