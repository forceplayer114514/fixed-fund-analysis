# Task 2 报告：`llm_ingest/tavily.py` 改名为 `llm_ingest/search.py`

## 实现内容

纯改名，未改动任何业务逻辑：

1. `git mv llm_ingest/tavily.py llm_ingest/search.py`（保留 git 历史）
2. `git mv tests/test_tavily.py tests/test_search.py`（保留 git 历史）
3. 更新 3 处生产 import：
   - `llm_ingest/discover.py:31`：`from .tavily import ...` → `from .search import ...`
   - `llm_ingest/discover2.py:45`：同上
   - `llm_ingest/fundmonitors.py:315`（`find_fundid_via_tavily` 函数体内）：同上
4. 更新 `tests/test_search.py` 顶部 import：`llm_ingest.tavily` → `llm_ingest.search`，
   `from llm_ingest import tavily as tavily_mod` → `from llm_ingest import search as tavily_mod`。
5. 顺带修复测试文件 docstring 里残留的旧路径引用（`llm_ingest/tavily.py 搜索后端切换...` →
   `llm_ingest/search.py 搜索后端切换...`），保持文档与实际路径一致。
6. 按任务简报里 Task 1 review 反馈，重写了 `search.py` 模块头部 docstring：
   原文声称"默认后端 SearXNG, 可切回 Tavily"，与 Task 1（commit b73ed5f）已把
   `SEARCH_BACKEND` 默认值改回 `tavily` 的运行时行为矛盾。改为如实描述：默认
   Tavily，SearXNG 服务已下线但分支代码原样保留到阶段三删除，用
   `SEARCH_BACKEND` 环境变量手动切换。**只改了文字描述，未改任何可执行代码**。
7. `tools/rotate_proxy.py:6` 注释里也有一处 `llm_ingest/tavily.py` 的旧路径引用
   （grep 全仓库扫描时发现，不在任务简报列出的 3 个生产 import 点范围内，但
   属于"全仓库无残留引用"的检查项），同步改为 `llm_ingest/search.py`。

**符号名一律未改**：`TavilyError`、`TavilyResult`、`tavily_search()`、
`multi_query_search()`、`AGGREGATOR_DOMAINS` 保持原名，只是模块路径变了。

## 测试

### `tests/test_search.py`（重命名后的测试文件单独跑）

```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_search.py', '-v']))"
```

结果：17 passed, 1 warning（0.07s）— 全部通过，无失败。

### 全量测试套件（`tests/`）

```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/','-q','--no-header']))"
```

结果：**2 failed, 352 passed**（176.16s）。失败的两个测试：

- `tests/test_extract_html.py::test_plotly_shrink_hovertext_anchor_takes_narrow_window`
- `tests/test_spec_b_wipe_script.py::test_dry_run_lists_targets`

**验证为改名前既存失败，与本次改动无关**：用 `git stash` 把本次改动全部
暂存回退后，单独重跑这两个测试，结果完全一致（同样 2 failed，报错信息一字
不差）。`git stash pop` 恢复后确认改动完整无误。这两个测试都不涉及
`tavily`/`search` 模块（grep 确认测试文件内容无相关字符串），跟本任务无关，
不在本任务修复范围内。

**结论**：改名前后通过数一致（352 passed），无新增失败，符合简报 Step 5
预期。

## 变更文件清单

- `llm_ingest/tavily.py` → `llm_ingest/search.py`（git mv，模块头部 docstring 重写第 1-9 行）
- `tests/test_tavily.py` → `tests/test_search.py`（git mv，import 与 docstring 首行更新）
- `llm_ingest/discover.py`（1 行 import 改动）
- `llm_ingest/discover2.py`（1 行 import 改动）
- `llm_ingest/fundmonitors.py`（1 行 import 改动，`find_fundid_via_tavily` 函数体内）
- `tools/rotate_proxy.py`（1 处注释路径引用改动，非任务简报列出但全仓库扫描发现）

## 自查（Self-Review）

- **完整性**：3 处生产 import 已全部改；全仓库 grep
  `llm_ingest.tavily|from .tavily|import tavily|llm_ingest/tavily`（含 .py 与非
  .py 文件）确认无残留代码引用，仅剩 `docs/superpowers/specs/`、
  `docs/superpowers/plans/`、`.superpowers/sdd/task-1-*.md`、
  `.superpowers/sdd/task-2-brief.md`、`.superpowers/sdd/review-*.diff` 里的历史
  文档/计划/评审记录提及旧路径——这些是对已完成或计划步骤的历史描述，
  不应回改（尤其 diff 文件是不可变的评审快照），故未动。
- **git 历史保留**：`git mv` 完成，`git log --follow` 可追溯到改名前的
  `tavily.py` 提交历史（rename 相似度检测在 log/show 时按内容计算，与暂存区
  展示为 A/D 而非 R 无关，不影响历史追溯）。
- **纪律**：未触碰任何计算/请求逻辑；`_tavily_impl`、`_searxng_impl`、
  `tavily_search` 分派逻辑、`AGGREGATOR_DOMAINS` 列表内容均逐行核对未改。
  未提交 `.superpowers/sdd/progress.md`、`task-1-brief.md`、`task-1-report.md`
  的现有未暂存改动（这些在我开始本任务前就已是脏状态，不属于本任务范围，
  参考 Task 1 提交 b73ed5f 的先例——该提交也只包含代码+测试文件，不含
  `.superpowers/sdd/` 下的计划文档）。
- **测试有效性**：`test_search.py` 的 17 个用例覆盖 `_host_blocked`、
  `_searxng_impl`、`tavily_search` 后端分派（默认值/环境变量切换）、
  `_tavily_impl` 未改动行为，均为改名前既有测试，改名后原样通过，证明
  行为零回归。

## 关注点

无阻塞性问题。仅供参考：仓库里另外两个未追踪文件
`data/fund_analysis.db.spec_b_backup_20260717_135816`、
`data/fund_analysis.db.spec_b_task8_backup_20260717_134451`
与本任务无关（早于本次会话就存在于 git status 中），未做任何处理。
