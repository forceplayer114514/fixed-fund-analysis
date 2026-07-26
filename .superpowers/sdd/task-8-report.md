# Task 8 Report: 新建 `llm_ingest/grok.py` —— Grok 客户端

## 状态: DONE

## 实现内容

按 brief 逐字转写创建了 4 个文件：

1. `tests/test_grok.py` — 11 个测试用例，覆盖 `grok_ask` (成功解析/503重试成功/重试耗尽抛错/缺 key 抛错)、`answer_archive` (JSON 解析/正则兜底/无 pdf_urls 字段/prompt 不索要 PDF 链接)、`answer_fundmonitors_id` (解析成功/未找到返 None/GrokError 吞掉返 None)。
2. `llm_ingest/prompts/grok_archive.md` — 只问"月报归档页在哪"，明确 "I need the PAGE, not files"，不索要文件链接。
3. `llm_ingest/prompts/grok_fundmonitors.md` — 只问 fundmonitors 的 FundID/AccCode。
4. `llm_ingest/grok.py` — 客户端实现：`GrokError`、`GrokAnswer`、`ArchiveAnswer`（无 `pdf_urls` 字段）、`grok_ask`（429/502/503/504 重试，`RETRY_SLEEP=5`）、`answer_archive`（JSON 优先 + 正则兜底抽 URL）、`answer_fundmonitors_id`（吞 `GrokError` 返 `None`）。

全部与 brief 给出的代码逐字一致，未做任何修改——brief 代码本身机制正确，无需调整。

## RED/GREEN 证据

**RED**（先建好测试文件，`grok.py` 尚不存在时跑）：
```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_grok.py', '-v']))"
```
结果：11 个测试全部 FAIL，报错均为
`ImportError: cannot import name 'grok' from 'llm_ingest'`
（因为 `tests/test_grok.py` 建好但 `llm_ingest/grok.py` 尚未创建，而非预期的 `ModuleNotFoundError` 措辞，但本质一致——模块不存在）。

**GREEN**（创建两个 prompt 文件 + `grok.py` 后重跑同一命令）：
```
============================== 11 passed, 1 warning in 0.05s =========================
```
全部 PASS（唯一 warning 是环境级 urllib3/LibreSSL 兼容性提示，与本次改动无关）。

## Files Created

- `/Users/chong/Desktop/fixed_fund_analysis/llm_ingest/grok.py`
- `/Users/chong/Desktop/fixed_fund_analysis/llm_ingest/prompts/grok_archive.md`
- `/Users/chong/Desktop/fixed_fund_analysis/llm_ingest/prompts/grok_fundmonitors.md`
- `/Users/chong/Desktop/fixed_fund_analysis/tests/test_grok.py`

## Commit

`fd3781e` — `feat(grok): 新增 Grok agentic search 客户端 (Spec G)`
（仅这 4 个文件，未误带其他工作区改动）

## Self-Review 结果

1. **`ArchiveAnswer` 无 `pdf_urls` 字段**：用 `__dataclass_fields__` 验证（非仅 `hasattr` 视觉检查）：
   `list(grok.ArchiveAnswer.__dataclass_fields__.keys())` = `['issuer_domain', 'archive_url', 'sources', 'evidence']`，确认无 `pdf_urls`。

2. **两个 prompt 文件不含索要 PDF 链接的措辞**：逐一检测 `'list the pdf'`、`'pdf urls'`、`'pdf links'`、`'the pdf'`、`'file link'` 等短语均不在 `grok_archive.md` 全文（小写化）中出现。`grok_archive.md` 明确写 "I need the PAGE, not files"，语义上主动排除文件链接请求，不是靠字符串技巧规避检测。

3. **`grok_ask` 重试语义**：`RETRY_STATUS = (429, 502, 503, 504)`，`RETRY_SLEEP = 5`；`retries=3` 时循环 `range(retries+1)` = 4 次尝试（首次 + 3 次重试），耗尽后抛 `GrokError`。测试 `test_raises_after_retries_exhausted` 验证 `call_count == 4`，`test_retries_on_503_then_succeeds` 验证第 2 次成功即返回。均通过。

4. **`answer_fundmonitors_id` 吞 `GrokError`**：函数体内 `try: ans = grok_ask(prompt) except GrokError: return None`，不传播异常。测试 `test_grok_error_returns_none` 验证（4 次 503 耗尽重试后应抛 `GrokError`，函数捕获后返回 `None`）。

5. **`llm_ingest/client.py` 的 `load_env` 可被 `from .client import load_env` 导入**：实地读取 `client.py`，确认模块顶层存在 `def load_env(env_path: Optional[Path] = None) -> None:`，签名兼容 `grok.py` 里的无参调用 `load_env()`（`env_path` 有默认值）。`test_missing_key_raises` 测试通过 `monkeypatch.setattr(grok, "load_env", lambda: None)` 打桩成功，证明 `load_env` 确实被导入进了 `grok` 模块命名空间（而非仅在 `client` 模块内部使用）。

## 顾虑

无。brief 给出的代码逐字转写后一次性 GREEN，未发现设计或实现层面的问题。`requests` 库已在项目中被 `llm_ingest/client.py` 使用（环境已装 2.32.5），与既有代码一致，未引入新依赖问题。

本任务未改动 `llm_ingest/discover.py`、`llm_ingest/fundmonitors.py` 等下游文件——Grok 客户端尚未接入任何调用点，符合任务边界（"这个任务还不接入 discovery pipeline，那是 Task 9"）。

## Fix: answer_archive null 兜底误覆盖

### 问题

审查发现 `answer_archive()` 里的正则兜底触发条件是 `if not archive_url`（原第 163 行），
只要 `archive_url` 是假值（`None`）就触发正则从 `ans.content` 全文抓第一个 URL 顶替。
但 `ans.content` 在 JSON 成功解析场景下就是那段 JSON 文本本身——Grok 严格遵循
`grok_archive.md` prompt 的 "Report only what you actually found... use null" 指示、
正确返回合法 JSON 且诚实给出 `archive_url: null` 时，也会被这条 `if not archive_url`
误判为"该走兜底了"，转而从 `evidence` 字段（或 `issuer_domain`）里正则抓一个毫不相关的
第三方 URL 顶替这个诚实的"未找到"。这与本任务模块 docstring 强调的"不信任 Grok
未经验证输出、绝不给它编造机会"的核心目的直接冲突——只是从 `pdf_urls` 字段换到了
`archive_url` 的 null 处理路径上重新出现。

### 修复

`_parse_json(ans.content)` 的返回值本身就足以区分两种情况：解析成功返回 dict（哪怕
`archive_url` 键值是 `None`），解析失败返回 `None`。原代码把解析结果立即 `or {}` 塌缩掉了
这个区分度。修复后先保留 `parsed = _parse_json(ans.content)` 的原始返回值，正则兜底
的触发条件改为 `if parsed is None`（JSON 解析彻底失败，即 Grok 没听话直接说人话），
不再看 `archive_url` 本身是否为假值。JSON 解析成功且显式给 `null` 时，尊重该 `null`，
不再触发正则兜底。同时更新了误导性注释（原注释"兜底: Grok 不听话直接说人话时"暗示
只在这种情况触发，但实际代码并未如此限定；新注释明确写出两种情况的区分逻辑）。

### RED/GREEN 证据

**RED**（先加测试 `test_respects_explicit_null_archive_url`，在修复前跑）：
```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_grok.py::TestAnswerArchive::test_respects_explicit_null_archive_url', '-v']))"
```
结果：FAILED —
`AssertionError: assert 'https://realissuer.com' is None`
（`archive_url` 被误从 `issuer_domain` 抓的 URL 顶替，复现了审查发现的确切场景：
`{"issuer_domain": "https://realissuer.com", "archive_url": null, "evidence": "Found via https://some-random-thirdparty-aggregator.com/..."}`）。

**GREEN**（应用修复后，跑 `tests/test_grok.py` 全量 12 个用例）：
```
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_grok.py', '-v']))"
```
结果：`12 passed, 1 warning in 0.05s`。新增的 `test_respects_explicit_null_archive_url` 通过，
既有的 `test_falls_back_to_regex_when_not_json`（真·非 JSON prose 场景仍须走正则兜底）
同样通过，未破坏原有行为。

### Files Changed

- `/Users/chong/Desktop/fixed_fund_analysis/llm_ingest/grok.py` — `answer_archive()` 兜底触发条件从 `if not archive_url` 改为 `if parsed is None`，更新注释。
- `/Users/chong/Desktop/fixed_fund_analysis/tests/test_grok.py` — 新增 `TestAnswerArchive::test_respects_explicit_null_archive_url`；`_resp` 补 `-> MagicMock` 返回类型注解；`_ok_payload` 补 `-> dict` 返回类型注解。

### 顾虑

无。修复范围精确匹配审查发现的问题，未触碰其它逻辑分支；`test_falls_back_to_regex_when_not_json`
（真实 prose 场景）验证兜底路径本身仍正常工作，未被误伤。
