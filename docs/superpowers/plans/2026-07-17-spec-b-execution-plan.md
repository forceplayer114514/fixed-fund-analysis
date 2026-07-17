# Spec B Implementation Plan (数据源优先级反转 + 全清重爬 + 透明展示)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 反转 fundmonitors 与 PDF 通路优先级 (fundmonitors 提为 L1 主源), 全清 `monthly_returns` 等 6 张表并重爬 8 支基金, 删 name guard 换 `discovered_source_name` 透明展示列。

**Architecture:** 5 处代码改动 (fundmonitors.py 删 guard 加 page_name / migrations 加迁移 / ingest.py 反转顺序 / models+schemas+types 加字段 / FundManagement.tsx 加列) + 1 个清库脚本 (7 步) + 1 次数据迁移 (Y.5 不可逆点)。测试 TDD 先行, 每个 task 独立可 commit 可回滚。

**Tech Stack:** Python 3.9.6 + FastAPI + SQLAlchemy + pytest + curl_cffi (chrome124 CF 伪装) + React + TypeScript + Tailwind

## Global Constraints

- **Python 3.9.6 语法**: 用 `Optional[X]`、`List[X]`, 禁 PEP 604 `X | None`
- **数据完整性**: 禁捏造金融数据; 缺口零容忍, 落 `confirmed_gaps` 而非插值/backfill
- **摄取层禁 backfill/forward-fill**: 只做文本->数字映射, 不猜
- **API key 进 `.env`**, 不进仓库; `.env.example` 只留占位符
- **回复语言**: 简体中文
- **DB 路径**: `data/fund_analysis.db` (`FUND_DB_PATH` 覆盖)
- **权威源白名单**: `_AUTHORITATIVE_TAGS = frozenset({"fundmonitors_table", "llm"})` 已定, 不改
- **Y.5 前置**: Y.1-Y.4 全绿 + backup 完成后方可执行
- **测试禁用 python3 -m pytest** (RTK 拦截), 用 `python3 -c "import pytest; pytest.main(['tests/xxx.py', '-v'])"`
- **本期作用范围**: 8 支基金 (bentham_global_income / jcb_active_bond / macquarie_australian_fixed_interest / smarter_money_lscf / yarra_enhanced_income_fund / kkr_credit_income_fund / gryphon_capital_income / stake_accumulate); Coolabah × 2 跳过 (Spec C)

---

## 文件结构 (11 代码 + 7 测试)

| 文件 | 动作 | 责任 |
|-----|-----|-----|
| `llm_ingest/fundmonitors.py` | 修改 | 删 `_STOPWORDS`/`_tokenize`/`_name_match`/probe guard 分支; 加 `_extract_page_fund_name`; probe 返回加 `page_fund_name` |
| `llm_ingest/migrations/__init__.py` | 新建 | 空模块 |
| `llm_ingest/migrations/spec_b_20260717.py` | 新建 | `apply(conn)` -- ALTER funds ADD COLUMN discovered_source_name (幂等) |
| `webapp/backend/app/main.py` | 修改 | lifespan 内追加 migrations.apply 调用 |
| `webapp/backend/app/routers/ingest.py` | 修改 | 反转 L1/L2, fundmonitors 先跑, 成功即跳 PDF |
| `webapp/backend/app/models.py` | 修改 | Fund ORM 加 `discovered_source_name` 列 |
| `webapp/backend/app/schemas.py` | 修改 | FundResponse 加字段 |
| `webapp/frontend/src/types/index.ts` | 修改 | Fund interface 加字段 |
| `webapp/frontend/src/pages/FundManagement.tsx` | 修改 | 表格加"数据源基金名"列 + 不一致标红 |
| `llm_ingest/scripts/__init__.py` | 新建/确认 | 空模块 |
| `llm_ingest/scripts/spec_b_wipe_and_rescrape.py` | 新建 | 7 步清库脚本 (前置检查/备份/清表/读 funds/并发触发/轮询/汇总) |
| `tests/test_fundmonitors_name_guard.py` | 删除 | 24 用例, name guard 全套 |
| `tests/test_fundmonitors_page_name.py` | 新建 | 8 用例, `_extract_page_fund_name` |
| `tests/test_fundmonitors_probe_return.py` | 新建 | 4 用例, probe 返回结构 |
| `tests/test_ingest_priority_l1_l2.py` | 新建 | 6 用例, L1/L2 反转集成 |
| `tests/test_migration_spec_b.py` | 新建 | 4 用例, 迁移幂等 |
| `tests/test_spec_b_wipe_script.py` | 新建 | 5 用例, 清库脚本 |

---

## Task 1: 迁移脚本 + 单测 (Y.1)

**Files:**
- Create: `llm_ingest/migrations/__init__.py`
- Create: `llm_ingest/migrations/spec_b_20260717.py`
- Create: `tests/test_migration_spec_b.py`

**Interfaces:**
- Consumes: `sqlite3.Connection` (stdlib)
- Produces: `apply(conn: sqlite3.Connection) -> None` -- 幂等 ALTER TABLE funds ADD COLUMN discovered_source_name TEXT

- [ ] **Step 1: 写失败测试**

Create `tests/test_migration_spec_b.py`:

```python
"""Spec B 迁移: ALTER TABLE funds ADD COLUMN discovered_source_name (幂等)."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from llm_ingest.migrations import spec_b_20260717 as mig


@pytest.fixture
def empty_db():
    """建一个仅有 funds 表的空 DB (无 discovered_source_name 列)。"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute("""
        CREATE TABLE funds (
            fund_id TEXT PRIMARY KEY,
            fund_name TEXT NOT NULL,
            confirmed_url TEXT NOT NULL,
            fetch_method TEXT NOT NULL,
            url_type TEXT NOT NULL
        )
    """)
    conn.commit()
    yield conn
    conn.close()
    Path(tmp.name).unlink(missing_ok=True)


def test_apply_adds_column(empty_db):
    mig.apply(empty_db)
    cur = empty_db.execute("PRAGMA table_info(funds)")
    cols = {row[1] for row in cur.fetchall()}
    assert "discovered_source_name" in cols


def test_apply_idempotent(empty_db):
    mig.apply(empty_db)
    mig.apply(empty_db)  # 第二次不该抛
    cur = empty_db.execute("PRAGMA table_info(funds)")
    dsn_rows = [r for r in cur.fetchall() if r[1] == "discovered_source_name"]
    assert len(dsn_rows) == 1  # 只加一次


def test_column_type_is_text(empty_db):
    mig.apply(empty_db)
    cur = empty_db.execute("PRAGMA table_info(funds)")
    for row in cur.fetchall():
        if row[1] == "discovered_source_name":
            assert row[2] == "TEXT"
            assert row[3] == 0  # NOT NULL = 0 (nullable)
            return
    pytest.fail("discovered_source_name 列不存在")


def test_existing_data_untouched(empty_db):
    empty_db.execute(
        "INSERT INTO funds (fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES ('x', 'Fund X', 'https://a.com', 'code', 'archive')"
    )
    empty_db.commit()
    mig.apply(empty_db)
    row = empty_db.execute(
        "SELECT fund_name, discovered_source_name FROM funds WHERE fund_id='x'"
    ).fetchone()
    assert row[0] == "Fund X"
    assert row[1] is None  # 新列默认 NULL
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -c "import pytest; pytest.main(['tests/test_migration_spec_b.py', '-v'])"
```
Expected: `ImportError: No module named 'llm_ingest.migrations'` 或类似

- [ ] **Step 3: 写模块骨架**

Create `llm_ingest/migrations/__init__.py`:

```python
"""llm_ingest 数据库迁移集. 每个迁移一个模块, 幂等 apply(conn)."""
```

Create `llm_ingest/migrations/spec_b_20260717.py`:

```python
"""Spec B 迁移 (2026-07-17): funds 表加 discovered_source_name 列.

用于透明展示 fundmonitors 页面上实际抓到的基金名, 前端与输入名不一致时标红。
幂等: PRAGMA table_info 探测列存在则跳过 ALTER。
"""
from __future__ import annotations

import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    """ALTER TABLE funds ADD COLUMN discovered_source_name TEXT (幂等)。

    - 探测列已存在 -> 直接返回 (支持二次调用)
    - 列不存在 -> ALTER + commit
    - 不动 funds 表既有数据 (SQLite ADD COLUMN 默认 NULL 填充旧行)
    """
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(funds)")
    cols = {row[1] for row in cur.fetchall()}
    if "discovered_source_name" not in cols:
        cur.execute("ALTER TABLE funds ADD COLUMN discovered_source_name TEXT")
        conn.commit()
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python3 -c "import pytest; pytest.main(['tests/test_migration_spec_b.py', '-v'])"
```
Expected: 4 passed

- [ ] **Step 5: 挂 lifespan (webapp/backend/app/main.py)**

修改 `webapp/backend/app/main.py` 的 lifespan 函数, `init_db()` 后追加:

```python
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            """启动：建表 + 迁移 + 可选启动 RBA 调度器；关闭：停止调度器。"""
            init_db()
            # Spec B: 幂等迁移 (加 discovered_source_name 列)
            import sqlite3
            from pathlib import Path
            from llm_ingest.migrations import spec_b_20260717 as _mig_b
            _db_path = Path(__file__).resolve().parents[3] / "data" / "fund_analysis.db"
            if _db_path.exists():
                _mc = sqlite3.connect(str(_db_path))
                try:
                    _mig_b.apply(_mc)
                finally:
                    _mc.close()
            scheduler = None
            # ... (以下不变)
```

**用 Edit 工具, `old_string`**:
```
        init_db()
        scheduler = None
```
**`new_string`**:
```
        init_db()
        # Spec B: 幂等迁移 (加 discovered_source_name 列)
        import sqlite3
        from pathlib import Path
        from llm_ingest.migrations import spec_b_20260717 as _mig_b
        _db_path = Path(__file__).resolve().parents[3] / "data" / "fund_analysis.db"
        if _db_path.exists():
            _mc = sqlite3.connect(str(_db_path))
            try:
                _mig_b.apply(_mc)
            finally:
                _mc.close()
        scheduler = None
```

- [ ] **Step 6: 手动核 webapp 启动不炸**

```bash
python3 -c "from webapp.backend.app.main import create_app; app = create_app(enable_scheduler=False); print('ok')"
```
Expected: `ok` (无 traceback)

- [ ] **Step 7: 生产 DB 幂等验证**

```bash
python3 -c "
import sqlite3
from llm_ingest.migrations import spec_b_20260717 as mig
c = sqlite3.connect('data/fund_analysis.db')
mig.apply(c)
mig.apply(c)  # 第二次
cols = [r[1] for r in c.execute('PRAGMA table_info(funds)').fetchall()]
print('has discovered_source_name:', 'discovered_source_name' in cols)
c.close()
"
```
Expected: `has discovered_source_name: True`

- [ ] **Step 8: Commit (Y.1)**

```bash
git add llm_ingest/migrations/ tests/test_migration_spec_b.py webapp/backend/app/main.py
git commit -m "feat(migrations): Spec B Y.1 加 discovered_source_name 列 (幂等)

llm_ingest/migrations/spec_b_20260717.py 新, apply(conn) 通过 PRAGMA table_info 探测幂等 ALTER。

webapp/backend/app/main.py lifespan 内追加, 与 init_db() 并列, DB 文件不存在跳过 (测试环境安全)。

4 单测覆盖 add / idempotent / column type / existing data untouched。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: fundmonitors.py 删 name guard + 加 page_name 提取 (Y.2 前半)

**Files:**
- Modify: `llm_ingest/fundmonitors.py`
- Create: `tests/test_fundmonitors_page_name.py`

**Interfaces:**
- Consumes: `Optional`, `re` (stdlib)
- Produces: `_extract_page_fund_name(markdown: str) -> Optional[str]`

- [ ] **Step 1: 写失败测试**

Create `tests/test_fundmonitors_page_name.py`:

```python
"""_extract_page_fund_name: 从 fundmonitors markdown 抽页面基金名 (Spec B 透明展示)."""
from __future__ import annotations

from llm_ingest.fundmonitors import _extract_page_fund_name


def test_h1_extracted():
    md = "# Yarra Enhanced Income Fund\n\nSome body text..."
    assert _extract_page_fund_name(md) == "Yarra Enhanced Income Fund"


def test_h2_extracted():
    md = "## KKR Credit Income Fund\n\nBody..."
    assert _extract_page_fund_name(md) == "KKR Credit Income Fund"


def test_h3_extracted():
    md = "### Bentham Global Income Fund\n\nRest..."
    assert _extract_page_fund_name(md) == "Bentham Global Income Fund"


def test_bold_fallback_when_no_heading():
    md = "Some prefix text **Macquarie Fixed Interest Fund** with more content"
    assert _extract_page_fund_name(md) == "Macquarie Fixed Interest Fund"


def test_empty_markdown_returns_none():
    assert _extract_page_fund_name("") is None
    assert _extract_page_fund_name(None) is None


def test_no_heading_no_bold_returns_none():
    md = "just plain text with no markdown formatting anywhere"
    assert _extract_page_fund_name(md) is None


def test_heading_takes_priority_over_bold():
    md = "# Real Title\n\nBody with **bold text** later"
    assert _extract_page_fund_name(md) == "Real Title"


def test_share_class_variant_preserved():
    """Wholesale/Assisted 等 share class 后缀原样保留 (前端标红核对)。"""
    md = "# Coolabah Short Term Income Fund (Wholesale)"
    assert _extract_page_fund_name(md) == "Coolabah Short Term Income Fund (Wholesale)"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -c "import pytest; pytest.main(['tests/test_fundmonitors_page_name.py', '-v'])"
```
Expected: `ImportError: cannot import name '_extract_page_fund_name'` 或 FAIL

- [ ] **Step 3: 删 name guard 三函数**

用 Edit 从 `llm_ingest/fundmonitors.py` 删掉:

1. `_STOPWORDS = frozenset({...})` 整块 (含开头注释 `# ---- name guard: 防 Tavily 词面搜返错源 ----`)
2. `_tokenize(name)` 整函数
3. `_name_match(query_name, markdown, head_chars=2000)` 整函数

**Edit `old_string`** (从 `# ---- name guard` 开始到 `_name_match` 函数 return 语句结束):

```python
# ---- name guard: 防 Tavily 词面搜返错源 ----
# 停用词: 品牌无关或跨基金通用词, 匹配时忽略
_STOPWORDS = frozenset({
    "the", "fund", "trust", "pty", "ltd", "limited", "unit", "class",
    "wholesale", "retail", "institutional", "capital", "management",
})


def _tokenize(name: str) -> List[str]:
    """把基金名切成 name-guard 用的 token 列表。

    规则:
      - 大小写不敏感 (统一转小写)
      - 剔括号内容 (含 share class 变体: `(Wholesale)`, `(Assisted)` 等)
      - 保留连字符复合词 (`floating-rate` 视为单 token, 不再拆)
      - 剔停用词 (`_STOPWORDS`)
      - 剔长度 < 3 的短 token (如 `us`, `au`)
    """
    if not name:
        return []
    # 1. 先剔括号内容 (包含 share class 后缀)
    s = re.sub(r"\([^)]*\)", " ", name).lower()
    # 2. 只用非 [a-z0-9-] 的字符切分, 让连字符复合词整块保留
    raw = re.split(r"[^a-z0-9\-]+", s)
    out: List[str] = []
    for tok in raw:
        tok = tok.strip("-").strip()
        if not tok:
            continue
        if tok in _STOPWORDS:
            continue
        if len(tok) < 3:
            continue
        out.append(tok)
    return out


def _name_match(
    query_name: str,
    markdown: str,
    head_chars: int = 2000,
) -> Tuple[bool, str]:
    """严格 AND: query_name 的所有 token 必须在 markdown 首 head_chars 字全命中。

    返回 (matched, reason):
      - ('ok') 命中
      - ('missing_tokens:xxx,yyy') 缺 token
      - ('no_tokens_after_stopword_filter') 查询名剔完停用词后空
    大小写不敏感; 连字符复合词按整块子串匹配 (查 `floating-rate`, md 里
    只有 `floating rate` 空格分开 -> 不认, 避免 SmarterMoney 类误判)。
    """
    tokens = _tokenize(query_name)
    if not tokens:
        return (False, "no_tokens_after_stopword_filter")
    head = (markdown or "")[:head_chars].lower()
    missing = [t for t in tokens if t not in head]
    if missing:
        return (False, "missing_tokens:" + ",".join(missing))
    return (True, "ok")
```

**`new_string`** (空 -- 直接删掉):

```python
```

(Edit 工具 `new_string` 传空字符串就是删除)

- [ ] **Step 4: 加 `_extract_page_fund_name`**

用 Edit 在 `_lookup_whitelist` 函数**之前**插入新函数。找到:

```python
def _lookup_whitelist(
    db_conn: sqlite3.Connection,
    fund_id: str,
) -> Optional[Tuple[int, str]]:
```

**Edit `old_string`**:
```python
def _lookup_whitelist(
    db_conn: sqlite3.Connection,
    fund_id: str,
) -> Optional[Tuple[int, str]]:
```

**`new_string`**:
```python
# ---- Spec B: 页面基金名提取 (透明展示, 替代 Spec A name guard) ----
_H1_RE = re.compile(r"^\s*#{1,3}\s+([^\n]+)", re.M)
_BOLD_FIRST_RE = re.compile(r"\*\*([^*]{5,120})\*\*")


def _extract_page_fund_name(markdown):
    """从 fundmonitors Full Profile markdown 抽页面上的基金名 (供透明展示)。

    顺序:
      1. 首个 h1/h2/h3 标题 (最常见, `# Yarra Enhanced Income Fund`)
      2. 首个粗体串 (`**Yarra Enhanced Income Fund**`, 5~120 字符)
      3. 找不到返 None (前端 fallback 到 fund_name)

    不做名字匹配, 不做过滤 -- 只把页面上的字面串拿出来给前端展示。

    Args:
        markdown: fundmonitors 页面 markdown, 可能为 None/空串

    Returns:
        Optional[str] 页面基金名 (原样保留 share class 后缀), 找不到返 None
    """
    if not markdown:
        return None
    m = _H1_RE.search(markdown)
    if m:
        return m.group(1).strip()
    m = _BOLD_FIRST_RE.search(markdown)
    if m:
        return m.group(1).strip()
    return None


def _lookup_whitelist(
    db_conn: sqlite3.Connection,
    fund_id: str,
) -> Optional[Tuple[int, str]]:
```

- [ ] **Step 5: 跑测试确认通过**

```bash
python3 -c "import pytest; pytest.main(['tests/test_fundmonitors_page_name.py', '-v'])"
```
Expected: 8 passed

- [ ] **Step 6: 核对既有测试仍绿 (name guard 测试要删, 但 fundmonitors 其他测试要活)**

```bash
python3 -c "import pytest; pytest.main(['tests/', '-v', '--ignore=tests/test_fundmonitors_name_guard.py', '-k', 'fundmonitors'])"
```
Expected: 全 pass (page_name 8 + probe/其他 剩余项)

- [ ] **Step 7: Commit**

```bash
git add llm_ingest/fundmonitors.py tests/test_fundmonitors_page_name.py
git commit -m "feat(fundmonitors): Spec B Y.2 删 name guard + 加 _extract_page_fund_name

删除:
- _STOPWORDS frozenset (11 停用词)
- _tokenize(name) 函数
- _name_match(query, md, head_chars) 严格 AND 校验

新增:
- _H1_RE / _BOLD_FIRST_RE 正则
- _extract_page_fund_name(md) -> Optional[str]: h1/h2/h3 优先, 粗体 fallback, 找不到返 None

用户反馈 name guard 太严, 8 类误杀 (连字符/空格/缩写/后缀词/share class/别名/大小写/复合词)。改透明展示: 抓到啥前端显啥, 与输入名不一致标红核对。

8 单测覆盖 h1/h2/h3/粗体 fallback/空/无匹配/优先级/share class 变体保留。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: fundmonitors.probe() 移除 name_mismatch + 加 page_fund_name (Y.2 后半)

**Files:**
- Modify: `llm_ingest/fundmonitors.py` (probe 函数)
- Create: `tests/test_fundmonitors_probe_return.py`
- Delete: `tests/test_fundmonitors_name_guard.py`

**Interfaces:**
- Consumes: `_extract_page_fund_name`, `_lookup_whitelist`, `find_fundid_via_tavily`, `build_profile_url`, `fetch_profile_markdown`, `parse_html_monthly_table`, `gate_check_table`
- Produces: `probe(fund_name, fund_id=None, db_conn=None) -> Dict[str, object]` 返回 dict, 新增 key `page_fund_name: Optional[str]`, 移除 status='name_mismatch'

- [ ] **Step 1: 写失败测试**

Create `tests/test_fundmonitors_probe_return.py`:

```python
"""probe() 返回结构 (Spec B): 新加 page_fund_name, 移除 name_mismatch 状态."""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from llm_ingest import fundmonitors as fm


@pytest.fixture
def db_with_whitelist():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE funds (
            fund_id TEXT PRIMARY KEY,
            fund_name TEXT,
            fundmonitors_fund_id INTEGER,
            fundmonitors_acc_code TEXT
        )
    """)
    conn.execute(
        "INSERT INTO funds (fund_id, fund_name, fundmonitors_fund_id, fundmonitors_acc_code) "
        "VALUES ('yarra_x', 'Yarra Fund X', 1512, 'fresnjxju')"
    )
    conn.commit()
    yield conn
    conn.close()


def test_probe_ok_returns_page_fund_name(db_with_whitelist):
    """白名单短路 + fetch ok -> 返回 page_fund_name."""
    fake_md = "# Yarra Enhanced Income Fund\n\n| Year | Jan % | Feb % |\n|---|---|---|"
    fake_records = [("2024-01-31", 0.005), ("2024-02-29", 0.006)]
    with patch.object(fm, "fetch_profile_markdown",
                      return_value=(fake_md, "ok")), \
         patch.object(fm, "parse_html_monthly_table",
                      return_value=(fake_records, {})), \
         patch.object(fm, "gate_check_table",
                      return_value=(True, [])):
        result = fm.probe("Yarra Fund X", fund_id="yarra_x", db_conn=db_with_whitelist)
    assert result["status"] == "ok"
    assert result["page_fund_name"] == "Yarra Enhanced Income Fund"
    assert result["records"] == fake_records


def test_probe_fetch_fail_returns_none_page_name(db_with_whitelist):
    with patch.object(fm, "fetch_profile_markdown",
                      return_value=(None, "fetch_fail")):
        result = fm.probe("Yarra Fund X", fund_id="yarra_x", db_conn=db_with_whitelist)
    assert result["status"] == "fetch_fail"
    assert result.get("page_fund_name") is None


def test_probe_paywall_returns_none_page_name(db_with_whitelist):
    with patch.object(fm, "fetch_profile_markdown",
                      return_value=(None, "paywall")):
        result = fm.probe("Yarra Fund X", fund_id="yarra_x", db_conn=db_with_whitelist)
    assert result["status"] == "paywall"
    assert result.get("page_fund_name") is None


def test_probe_no_name_mismatch_status():
    """Spec B: name_mismatch 状态彻底移除, 就算 fund_name 与 page 不符也不再挡。"""
    fake_md = "# Some Completely Different Fund Name"
    fake_records = [("2024-01-31", 0.005), ("2024-02-29", 0.006)]
    with patch.object(fm, "find_fundid_via_tavily",
                      return_value=(9999, "abc")), \
         patch.object(fm, "fetch_profile_markdown",
                      return_value=(fake_md, "ok")), \
         patch.object(fm, "parse_html_monthly_table",
                      return_value=(fake_records, {})), \
         patch.object(fm, "gate_check_table",
                      return_value=(True, [])):
        # 无 fund_id + 无 db_conn -> 走 Tavily 通路, 无白名单短路
        result = fm.probe("Yarra Fund X")
    # 关键: 状态不是 name_mismatch, 数据入库
    assert result["status"] == "ok"
    assert result["page_fund_name"] == "Some Completely Different Fund Name"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -c "import pytest; pytest.main(['tests/test_fundmonitors_probe_return.py', '-v'])"
```
Expected: 部分 FAIL (page_fund_name key 缺 / name_mismatch 状态仍存在)

- [ ] **Step 3: 改 probe() 函数**

Read `llm_ingest/fundmonitors.py` 定位 `def probe(` 到函数末尾。

用 Edit **老 probe 整段替换**。

**`old_string`** (`def probe(` 到函数最末 return):

```python
def probe(
    fund_name: str,
    fund_id: Optional[str] = None,
    db_conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, object]:
    """L3 兜底端到端. 输入 fund_name -> 输出结构化结果。

    流程 (Spec A):
      A. 白名单短路: 给了 fund_id+db_conn 且 funds 表命中 fundmonitors_fund_id
         -> 直接拼 URL fetch, 跳过 Tavily, 跳过 name guard (人工背书)。
      B. 否则走 Tavily: `site:fundmonitors.com <fund_name>` 拿 FundID+AccCode。
      C. Tavily 通路 fetch 成功后, 用 `_name_match` 严格 AND 校验 markdown 首
         2000 字含 fund_name 所有 token, 缺 token -> status='name_mismatch'
         (防 SmarterMoney 类词面相近的错源)。

    返回 dict:
      status: 'ok' | 'no_fundid' | 'paywall' | 'fetch_fail' | 'no_table'
              | 'gate_fail' | 'name_mismatch'
      records: List[(date, net_return)] (成功时)
      ytd_map: Dict[year, ytd_decimal]
      url: 抓的 AJAX URL
      errors: List[str] (gate_fail / name_mismatch 时)
    """
    # A. 白名单短路
    whitelisted = False
    hit: Optional[Tuple[int, str]] = None
    if fund_id and db_conn is not None:
        wl = _lookup_whitelist(db_conn, fund_id)
        if wl is not None:
            hit = wl
            whitelisted = True
    # B. 未白名单 -> Tavily 通路
    if hit is None:
        hit = find_fundid_via_tavily(fund_name)
        if not hit:
            return {"status": "no_fundid", "records": [], "ytd_map": {},
                    "url": None, "errors": []}
    fid, acc = hit
    url = build_profile_url(fid, acc)
    md, status = fetch_profile_markdown(url)
    if status != "ok":
        return {"status": status, "records": [], "ytd_map": {},
                "url": url, "errors": []}
    # C. Tavily 通路才跑 name guard; 白名单命中信任 URL, 跳过 guard
    if not whitelisted:
        matched, reason = _name_match(fund_name, md or "")
        if not matched:
            return {"status": "name_mismatch", "records": [], "ytd_map": {},
                    "url": url, "errors": [reason]}
    records, ytd_map = parse_html_monthly_table(md or "")
    if not records:
        return {"status": "no_table", "records": [], "ytd_map": ytd_map,
                "url": url, "errors": []}
    ok, errs = gate_check_table(records, ytd_map)
    if not ok:
        return {"status": "gate_fail", "records": records, "ytd_map": ytd_map,
                "url": url, "errors": errs}
    return {"status": "ok", "records": records, "ytd_map": ytd_map,
            "url": url, "errors": []}
```

**`new_string`**:

```python
def probe(
    fund_name: str,
    fund_id: Optional[str] = None,
    db_conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, object]:
    """L1 主源端到端 (Spec B: fundmonitors 从 L3 提到 L1). 输入 fund_name -> 输出结构化结果。

    流程:
      A. 白名单短路: 给了 fund_id+db_conn 且 funds 表命中 fundmonitors_fund_id
         -> 直接拼 URL fetch (人工背书, 信任 URL 匹配)。
      B. 否则走 Tavily: `site:fundmonitors.com <fund_name>` 拿 FundID+AccCode。
      C. 抓到 markdown -> 抽 page_fund_name (供前端透明展示), 不做名字匹配 gate。
         (Spec B 反 Spec A: 抓到啥就展示啥, 与输入名不一致由用户核对, 不再软挡。)

    返回 dict:
      status: 'ok' | 'no_fundid' | 'paywall' | 'fetch_fail' | 'no_table' | 'gate_fail'
      records: List[(date, net_return)] (成功时)
      ytd_map: Dict[year, ytd_decimal]
      url: 抓的 AJAX URL
      page_fund_name: Optional[str] -- 页面上抓到的基金名, 供 UI 透明展示与输入名核对
      errors: List[str] (gate_fail 时)
    """
    # A. 白名单短路
    hit: Optional[Tuple[int, str]] = None
    if fund_id and db_conn is not None:
        wl = _lookup_whitelist(db_conn, fund_id)
        if wl is not None:
            hit = wl
    # B. 未白名单 -> Tavily 通路
    if hit is None:
        hit = find_fundid_via_tavily(fund_name)
        if not hit:
            return {"status": "no_fundid", "records": [], "ytd_map": {},
                    "url": None, "page_fund_name": None, "errors": []}
    fid, acc = hit
    url = build_profile_url(fid, acc)
    md, status = fetch_profile_markdown(url)
    if status != "ok":
        return {"status": status, "records": [], "ytd_map": {},
                "url": url, "page_fund_name": None, "errors": []}
    # C. 抽页面基金名 (透明展示, 不做 gate)
    page_name = _extract_page_fund_name(md)
    records, ytd_map = parse_html_monthly_table(md or "")
    if not records:
        return {"status": "no_table", "records": [], "ytd_map": ytd_map,
                "url": url, "page_fund_name": page_name, "errors": []}
    ok, errs = gate_check_table(records, ytd_map)
    if not ok:
        return {"status": "gate_fail", "records": records, "ytd_map": ytd_map,
                "url": url, "page_fund_name": page_name, "errors": errs}
    return {"status": "ok", "records": records, "ytd_map": ytd_map,
            "url": url, "page_fund_name": page_name, "errors": []}
```

- [ ] **Step 4: 跑测试**

```bash
python3 -c "import pytest; pytest.main(['tests/test_fundmonitors_probe_return.py', '-v'])"
```
Expected: 4 passed

- [ ] **Step 5: 删旧 name guard 测试**

```bash
rm tests/test_fundmonitors_name_guard.py
```

确认剩余测试整套还绿 (跳过 `test_fundmonitors_full.py` 可能引用 `_name_match` 的地方):

```bash
python3 -c "import pytest; pytest.main(['tests/', '-v', '-k', 'fundmonitors'])"
```
Expected: page_name (8) + probe_return (4) + 其他不引用 name guard 的 全 pass; **如果有其他测试 import `_name_match` / `_tokenize` / `_STOPWORDS` 报 ImportError, 手工修**

- [ ] **Step 6: Grep 兜底 -- 全库 dangling reference 核**

```bash
grep -rn "_name_match\|_tokenize\|_STOPWORDS\|name_mismatch" --include="*.py" llm_ingest/ tests/ webapp/
```
Expected: 无输出 (若有 -- 修完再走 Step 7)

- [ ] **Step 7: Commit**

```bash
git add llm_ingest/fundmonitors.py tests/test_fundmonitors_probe_return.py
git rm tests/test_fundmonitors_name_guard.py
git commit -m "refactor(fundmonitors): Spec B Y.2b probe 移除 name_mismatch + 加 page_fund_name

probe() 变更:
- 移除 status='name_mismatch' 分支 (Spec A name guard 太严, 用户主张透明展示)
- 移除 whitelisted flag (不再需要跳过 guard)
- 所有返回 dict 加 page_fund_name: Optional[str] key
- Tavily 通路和白名单通路都跑 _extract_page_fund_name

同步删 tests/test_fundmonitors_name_guard.py (24 用例, Spec A 已过时)。
4 单测覆盖 ok/fetch_fail/paywall/无 name_mismatch 状态。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: models.py + schemas.py + types 加字段 (Y.4 前半)

**Files:**
- Modify: `webapp/backend/app/models.py`
- Modify: `webapp/backend/app/schemas.py`
- Modify: `webapp/frontend/src/types/index.ts`

**Interfaces:**
- Consumes: SQLAlchemy `Mapped`, `mapped_column`, `Text`; Pydantic `BaseModel`
- Produces: `Fund.discovered_source_name: Optional[str]` ORM + `FundResponse.discovered_source_name: Optional[str]` + TS `Fund.discovered_source_name?: string | null`

- [ ] **Step 1: 修 models.py**

用 Edit 在 `Fund` 类 `created_at` 行**之后**、`monthly_returns` relationship 前**插入**:

**`old_string`**:
```python
    created_at: Mapped[Optional[str]] = mapped_column(String, server_default=text("(datetime('now'))"))

    monthly_returns: Mapped[list["MonthlyReturn"]] = relationship(
```

**`new_string`**:
```python
    created_at: Mapped[Optional[str]] = mapped_column(String, server_default=text("(datetime('now'))"))
    # Spec B: fundmonitors 页面上实际抓到的基金名 (透明展示, 与 fund_name 不一致前端标红核对)
    discovered_source_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    monthly_returns: Mapped[list["MonthlyReturn"]] = relationship(
```

- [ ] **Step 2: 修 schemas.py FundResponse**

**`old_string`**:
```python
class FundResponse(BaseModel):
    fund_id: str
    fund_name: str
    apir_code: Optional[str] = None
    confirmed_url: str
    fetch_method: str
    url_type: str
    max_pdf_pages: Optional[int] = None
    data_cutoff_month: Optional[str] = None  # 来自 fund_metrics.date_period 或最新 monthly_return
    has_metrics: bool = False
    gap_count: int = 0  # confirmed_gaps 表该基金行数（数据完整性标记）
    pending_count: int = 0  # pending_review 表 state='pending' 行数（LLM 摄取两闸未过待人工审核）

    model_config = {"from_attributes": True}
```

**`new_string`**:
```python
class FundResponse(BaseModel):
    fund_id: str
    fund_name: str
    apir_code: Optional[str] = None
    confirmed_url: str
    fetch_method: str
    url_type: str
    max_pdf_pages: Optional[int] = None
    data_cutoff_month: Optional[str] = None  # 来自 fund_metrics.date_period 或最新 monthly_return
    has_metrics: bool = False
    gap_count: int = 0  # confirmed_gaps 表该基金行数（数据完整性标记）
    pending_count: int = 0  # pending_review 表 state='pending' 行数（LLM 摄取两闸未过待人工审核）
    # Spec B: fundmonitors 页面实际抓到的基金名 (透明展示; 与 fund_name 不同时前端标红)
    discovered_source_name: Optional[str] = None

    model_config = {"from_attributes": True}
```

- [ ] **Step 3: 修 frontend/src/types/index.ts Fund**

**`old_string`**:
```typescript
export interface Fund {
  fund_id: string
  fund_name: string
  apir_code: string | null
  confirmed_url: string
  fetch_method: string
  url_type: string
  max_pdf_pages: number | null
  data_cutoff_month: string | null
  has_metrics: boolean
  /** confirmed_gaps 表该基金行数（数据完整性标记，>0 表示有缺口） */
  gap_count: number
  /** pending_review 表 state='pending' 行数（LLM 摄取两闸未过待人工审核） */
  pending_count: number
}
```

**`new_string`**:
```typescript
export interface Fund {
  fund_id: string
  fund_name: string
  apir_code: string | null
  confirmed_url: string
  fetch_method: string
  url_type: string
  max_pdf_pages: number | null
  data_cutoff_month: string | null
  has_metrics: boolean
  /** confirmed_gaps 表该基金行数（数据完整性标记，>0 表示有缺口） */
  gap_count: number
  /** pending_review 表 state='pending' 行数（LLM 摄取两闸未过待人工审核） */
  pending_count: number
  /** Spec B: fundmonitors 页面实际抓到的基金名 (与 fund_name 不同时前端标红) */
  discovered_source_name?: string | null
}
```

- [ ] **Step 4: 确认 backend/frontend build 无 type error**

```bash
python3 -c "from webapp.backend.app.models import Fund; f = Fund(fund_id='x', fund_name='X', confirmed_url='https://a.com', fetch_method='c', url_type='a', discovered_source_name='Y'); print(f.discovered_source_name)"
```
Expected: `Y`

```bash
python3 -c "from webapp.backend.app.schemas import FundResponse; r = FundResponse(fund_id='x', fund_name='X', confirmed_url='u', fetch_method='c', url_type='a', discovered_source_name='Y'); print(r.model_dump()['discovered_source_name'])"
```
Expected: `Y`

frontend TS 编译核对 (可选, Y.8 会跑):
```bash
cd webapp/frontend && npx tsc --noEmit 2>&1 | head -5
```
Expected: 无 discovered_source_name 相关错; 其他既有错可容忍

- [ ] **Step 5: Commit**

```bash
git add webapp/backend/app/models.py webapp/backend/app/schemas.py webapp/frontend/src/types/index.ts
git commit -m "feat(models+schemas+types): Spec B Y.4a Fund 加 discovered_source_name 字段

- models.py Fund ORM: discovered_source_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
- schemas.py FundResponse: discovered_source_name: Optional[str] = None (响应返出)
- frontend types/index.ts: Fund.discovered_source_name?: string | null

Y.1 迁移已加 DB 列, 这一 task 补 ORM/Pydantic/TS 三层字段, 让 GET /api/funds 能把值 serialize 出去。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: ingest.py 反转 L1/L2 优先级 (Y.3)

**Files:**
- Modify: `webapp/backend/app/routers/ingest.py` (`_run_ingest_job` 函数)
- Create: `tests/test_ingest_priority_l1_l2.py`

**Interfaces:**
- Consumes: `probe(fund_name, fund_id, db_conn) -> Dict` (Task 3 输出); `store_mod.write_table_records`, `store_mod.upsert_fund`
- Produces: `_run_ingest_job(jid, req)` 内部逻辑变: fundmonitors 先跑, 覆盖成功即跳 PDF 循环, UPDATE funds.discovered_source_name

- [ ] **Step 1: 写失败测试**

Create `tests/test_ingest_priority_l1_l2.py`:

```python
"""L1/L2 优先级反转 (Spec B): fundmonitors 先跑, 覆盖成功即跳 PDF 循环."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def tmp_db(monkeypatch):
    """临时 DB, 走既有 schema + 加 discovered_source_name."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    monkeypatch.setenv("FUND_DB_PATH", tmp.name)
    conn = sqlite3.connect(tmp.name)
    from llm_ingest import store
    store.ensure_tables_if_missing(conn)
    conn.execute("ALTER TABLE funds ADD COLUMN discovered_source_name TEXT")
    conn.execute("ALTER TABLE funds ADD COLUMN fundmonitors_fund_id INTEGER")
    conn.execute("ALTER TABLE funds ADD COLUMN fundmonitors_acc_code TEXT")
    conn.execute(
        "INSERT INTO funds (fund_id, fund_name, confirmed_url, fetch_method, "
        "url_type, fundmonitors_fund_id) VALUES (?, ?, ?, ?, ?, ?)",
        ("test_fund", "Test Fund", "https://a.com", "code", "archive", 1234),
    )
    conn.commit()
    conn.close()
    yield tmp.name
    Path(tmp.name).unlink(missing_ok=True)


def _make_req():
    from webapp.backend.app.schemas import IngestRequest
    return IngestRequest(
        fund_id="test_fund", fund_name="Test Fund",
        issuer=None, confirmed_url=None, issuer_domain=None,
        asx_code=None, apir_code=None, max_pdf_pages=None, limit=None,
    )


def test_l1_ok_skips_pdf_and_records_discovered_source_name(tmp_db):
    """L1 fundmonitors 成功 -> PDF 循环整段跳过, discovered_source_name 落库."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm

    fake_records = [("2024-01-31", 0.005), ("2024-02-29", 0.006)]
    with patch.object(fm, "probe", return_value={
        "status": "ok",
        "records": fake_records,
        "ytd_map": {},
        "url": "https://fundmonitors.com/x",
        "page_fund_name": "Test Fund From Page",
        "errors": [],
    }), patch("llm_ingest.discover._fetch", return_value=None) as mock_fetch:
        jid = ing._job_new("test_fund")
        ing._run_ingest_job(jid, _make_req())

    # PDF discovery 不该被调 (L1 已覆盖)
    assert mock_fetch.call_count == 0
    # discovered_source_name 落库
    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT discovered_source_name FROM funds WHERE fund_id='test_fund'"
    ).fetchone()
    conn.close()
    assert row[0] == "Test Fund From Page"


def test_l1_fail_falls_back_to_pdf(tmp_db):
    """L1 status!=ok -> 走 L2 PDF 通路 (既有 discovery)."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm

    with patch.object(fm, "probe", return_value={
        "status": "fetch_fail", "records": [], "ytd_map": {},
        "url": None, "page_fund_name": None, "errors": [],
    }), patch("llm_ingest.discover._fetch", return_value=None) as mock_fetch:
        # discovery 会尝试跑 (返 None 让 discover fallback 到 raise)
        jid = ing._job_new("test_fund")
        try:
            ing._run_ingest_job(jid, _make_req())
        except Exception:
            pass  # discovery 失败可容忍, 本测试关心 fetch 被调
    assert mock_fetch.call_count >= 1  # L2 PDF 通路启动


def test_l1_paywall_no_discovered_source_name(tmp_db):
    """L1 paywall -> discovered_source_name 保持 NULL."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm

    with patch.object(fm, "probe", return_value={
        "status": "paywall", "records": [], "ytd_map": {},
        "url": "https://fundmonitors.com/x",
        "page_fund_name": None, "errors": [],
    }), patch("llm_ingest.discover._fetch", return_value=None):
        jid = ing._job_new("test_fund")
        try:
            ing._run_ingest_job(jid, _make_req())
        except Exception:
            pass
    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT discovered_source_name FROM funds WHERE fund_id='test_fund'"
    ).fetchone()
    conn.close()
    assert row[0] is None


def test_l1_exception_records_status(tmp_db):
    """probe 抛异常 -> 状态 exception:ExceptionType, 走 L2, 不崩 job."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm

    with patch.object(fm, "probe", side_effect=RuntimeError("boom")), \
         patch("llm_ingest.discover._fetch", return_value=None):
        jid = ing._job_new("test_fund")
        try:
            ing._run_ingest_job(jid, _make_req())
        except Exception:
            pass
    log = "\n".join(ing._JOBS[jid]["log_tail"])
    assert "exception" in log.lower() or "boom" in log.lower()


def test_no_l1_threshold_gate():
    """Spec B: 不再有 len(links)<24 门槛, L1 无条件先跑."""
    import inspect
    from webapp.backend.app.routers import ingest as ing
    src = inspect.getsource(ing._run_ingest_job)
    # 老 gate 已删
    assert "len(links) < 24" not in src


def test_l1_ok_stats_monthly_count(tmp_db):
    """L1 覆盖 N 月 -> stats["monthly"] == N."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm

    fake_records = [
        ("2024-01-31", 0.005), ("2024-02-29", 0.006),
        ("2024-03-31", 0.007),
    ]
    with patch.object(fm, "probe", return_value={
        "status": "ok", "records": fake_records, "ytd_map": {},
        "url": "https://fundmonitors.com/x",
        "page_fund_name": "Test Fund", "errors": [],
    }), patch("llm_ingest.discover._fetch", return_value=None):
        jid = ing._job_new("test_fund")
        ing._run_ingest_job(jid, _make_req())
    assert ing._JOBS[jid]["stats"]["monthly"] == 3
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -c "import pytest; pytest.main(['tests/test_ingest_priority_l1_l2.py', '-v'])"
```
Expected: 全 FAIL (L1 未反转, PDF 先跑)

- [ ] **Step 3: 改 ingest.py**

Read `webapp/backend/app/routers/ingest.py` 定位 `_run_ingest_job` 现有 L3 fundmonitors 分支 (`if not req.confirmed_url and len(links) < 24:` 及其后 20 行)。

**主体重构**: 把 L3 分支移到 discovery 之**前**, 且**成功即 return** 跳 PDF 循环。

用 Edit **老 discovery + L3 分支整段替换**。

**`old_string`** (从 `_job_update(jid, state="discovering"...)` 起, 到既有 L3 分支尾 `_job_log(jid, f"L3 fundmonitors: {n_written} 月入库 (bulk)")` 上一行的关键点):

先读文件精确定位:

```bash
grep -n "L3 fundmonitors\|len(links) < 24\|_job_update(jid, state=\"discovering\"" webapp/backend/app/routers/ingest.py
```

**改写为**: (基于 Spec 2.3 节代码; 实际 Edit 用文件中的精确串)

**新 `_run_ingest_job` 主体骨架** (仅示 L1 反转段, 保留其他既有代码):

```python
        _job_update(jid, state="ingesting_l1_fundmonitors",
                    started_at=datetime.utcnow().isoformat())
        _job_log(jid, "job start")

        # 懒 import 保持原样
        import os
        _repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
        )
        if _repo_root not in sys.path:
            sys.path.insert(0, _repo_root)
        from llm_ingest import cli as llm_cli
        from llm_ingest import discover as disc_mod
        from llm_ingest import extract as ex_mod
        from llm_ingest import fundmonitors as fm_mod
        from llm_ingest import pdf as pdf_mod
        from llm_ingest import store as store_mod
        from llm_ingest import verify

        # upsert fund (需先建行才能 UPDATE discovered_source_name)
        conn = store_mod.open_conn()
        store_mod.ensure_tables_if_missing(conn)
        # 迁移幂等: 若 lifespan 没跑 (直调 job), 自补一次
        try:
            from llm_ingest.migrations import spec_b_20260717 as _mig_b
            _mig_b.apply(conn)
        except Exception:
            pass  # 库已迁移则忽略
        cu = req.confirmed_url or req.issuer_domain or ""
        store_mod.upsert_fund(
            conn, fund_id=req.fund_id, fund_name=req.fund_name,
            confirmed_url=cu, apir_code=req.apir_code,
            max_pdf_pages=req.max_pdf_pages,
        )
        _job_log(jid, f"upsert_fund: {req.fund_id}")

        # ---- L1: fundmonitors 主源 (Spec B 反转优先级) ----
        _job_log(jid, "L1 fundmonitors: probing ...")
        l1_result: Dict[str, Any] = {"status": "skipped"}
        try:
            l1_result = fm_mod.probe(req.fund_name, fund_id=req.fund_id, db_conn=conn)
        except Exception as e:  # noqa: BLE001
            l1_result = {"status": f"exception:{type(e).__name__}",
                         "page_fund_name": None, "records": [],
                         "url": None, "errors": [str(e)]}
        _job_log(jid, f"L1 fundmonitors: status={l1_result.get('status')}, "
                      f"records={len(l1_result.get('records', []))}, "
                      f"page_name={l1_result.get('page_fund_name')}")

        stats = {"monthly": 0, "pending": 0, "gap": 0, "download_fail": 0}

        if l1_result.get("status") == "ok":
            n_written = store_mod.write_table_records(
                conn, fund_id=req.fund_id,
                records=l1_result["records"], source_url=l1_result["url"],
            )
            _job_log(jid, f"L1 fundmonitors: {n_written} 月入库")
            # 记 discovered_source_name (供前端透明展示)
            conn.execute(
                "UPDATE funds SET discovered_source_name=? WHERE fund_id=?",
                (l1_result.get("page_fund_name"), req.fund_id),
            )
            conn.commit()
            stats["monthly"] = n_written
            _job_log(jid, "L1 覆盖成功, 跳过 L2 PDF 通路")
            conn.close()
            _job_update(jid, stats=stats)
            # 触发指标重算
            _trigger_recompute_if_needed(jid, req.fund_id, stats, True)
            _job_update(jid, state="succeeded",
                        finished_at=datetime.utcnow().isoformat())
            _job_log(jid, f"done: {stats}")
            return  # <-- Spec B: L1 覆盖成功即 return, 无 L2 补差

        _job_log(jid, "L1 未覆盖, 走 L2 PDF 通路 ...")

        # ---- L2: 官网 PDF discovery + 循环 (原 L1 通路降级为 L2) ----
        _job_update(jid, state="discovering_l2_pdf")
        links: List[tuple] = []
        # (以下 discovery + PDF 循环整段沿用既有代码, 略)
```

**注**: `_trigger_recompute_if_needed` 是重构提取; 见 Step 4。

**具体 Edit 落地**: 用 3 次 Edit:

**Edit 1**: `_run_ingest_job` 开头到 upsert_fund 段 (把 upsert 提前)

**Edit 2**: 中间插入 L1 fundmonitors 段 (紧跟 upsert_fund)

**Edit 3**: 删原 L3 分支 (`if not req.confirmed_url and len(links) < 24:` 到该 if 尾), 保留 PDF 循环

- [ ] **Step 4: 抽出 `_trigger_recompute_if_needed` helper**

在 `_run_ingest_job` 之前加辅助函数 (避免 L1/L2 两条通路复制 recompute 代码):

```python
def _trigger_recompute_if_needed(
    jid: str, fund_id: str, stats: Dict[str, int], l1_wrote_any: bool,
) -> None:
    """L1 或 L2 只要写了新月度就触发既有 recompute."""
    if not (stats.get("monthly", 0) > 0 or l1_wrote_any):
        return
    _job_log(jid, "recompute metrics ...")
    try:
        from app.database import SessionLocal
        from app.metrics_pipeline import compute_and_store_metrics
        sess = SessionLocal()
        try:
            compute_and_store_metrics(sess, fund_id)
            _job_log(jid, "recompute ok")
        except ValueError as e:
            _job_log(jid, f"recompute skip: {e}")
        finally:
            sess.close()
    except Exception as e:  # noqa: BLE001
        _job_log(jid, f"recompute failed (ingest 已成功): {e}")
```

L2 段末尾原有 recompute 块也换成调用 `_trigger_recompute_if_needed(jid, req.fund_id, stats, False)`。

- [ ] **Step 5: 跑单测**

```bash
python3 -c "import pytest; pytest.main(['tests/test_ingest_priority_l1_l2.py', '-v'])"
```
Expected: 6 passed

跑全部相关测试:
```bash
python3 -c "import pytest; pytest.main(['tests/', '-v', '-k', 'ingest or fundmonitors'])"
```
Expected: 全 pass

- [ ] **Step 6: smoke test 单支基金 probe (脱离 webapp)**

```bash
python3 -c "
import sqlite3
from llm_ingest import fundmonitors as fm
c = sqlite3.connect('data/fund_analysis.db')
r = fm.probe('Yarra Enhanced Income Fund', fund_id='yarra_enhanced_income_fund', db_conn=c)
print('status:', r['status'])
print('records:', len(r.get('records', [])))
print('page_name:', r.get('page_fund_name'))
c.close()
"
```
Expected: `status: ok` + `records > 0` + `page_name` 非 None (Yarra 白名单命中过 -- 若 fundmonitors 网络挂, `fetch_fail` 也可接受, Y.5 前会集中处理)

- [ ] **Step 7: Commit**

```bash
git add webapp/backend/app/routers/ingest.py tests/test_ingest_priority_l1_l2.py
git commit -m "feat(ingest): Spec B Y.3 反转 L1/L2 优先级, fundmonitors 先跑

_run_ingest_job 主要变更:
- upsert_fund 提前 (需早于 L1 UPDATE discovered_source_name)
- L1 fundmonitors probe 移到 discovery/PDF 循环之前, 无 len(links)<24 门槛
- L1 status=ok -> write_table_records + UPDATE discovered_source_name + return (跳 L2)
- L1 status!=ok -> 走 L2 PDF 通路 (老 L1 discovery + 循环整段降为 L2)
- 抽出 _trigger_recompute_if_needed helper 避免两通路复制代码

6 集成测试覆盖:
- L1 ok skip PDF + records discovered_source_name
- L1 fail fallback PDF
- L1 paywall no discovered_source_name
- L1 exception 兜底
- 无 len(links)<24 门槛
- stats.monthly count 正确

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: FundManagement.tsx 加"数据源基金名"列 (Y.4 后半)

**Files:**
- Modify: `webapp/frontend/src/pages/FundManagement.tsx`

**Interfaces:**
- Consumes: `Fund.discovered_source_name?: string | null` (Task 4 输出)
- Produces: 表格新增 5th 列, 不一致标红 + tooltip

- [ ] **Step 1: 读现有表格结构**

Read `webapp/frontend/src/pages/FundManagement.tsx` 定位 `<thead>` 表头段和 `<tbody>` 行渲染段 (about 171-220 行)。

- [ ] **Step 2: 表头加新列**

用 Edit **`old_string`**:
```tsx
                <th className="text-left py-3 px-4 text-gray-500 font-medium">基金名称</th>
                <th className="text-left py-3 px-4 text-gray-500 font-medium">APIR</th>
```

**`new_string`**:
```tsx
                <th className="text-left py-3 px-4 text-gray-500 font-medium">基金名称</th>
                <th className="text-left py-3 px-4 text-gray-500 font-medium">数据源基金名</th>
                <th className="text-left py-3 px-4 text-gray-500 font-medium">APIR</th>
```

- [ ] **Step 3: 表体加新 td**

用 Edit **`old_string`**:
```tsx
                    <td className="py-3 px-4 font-medium">{f.fund_name}</td>
                    <td className="py-3 px-4 text-gray-500">{f.apir_code ?? '—'}</td>
```

**`new_string`**:
```tsx
                    <td className="py-3 px-4 font-medium">{f.fund_name}</td>
                    <td
                      className={
                        f.discovered_source_name
                          && f.discovered_source_name !== f.fund_name
                          ? "py-3 px-4 text-red-600 font-semibold"
                          : "py-3 px-4 text-gray-500"
                      }
                      title={
                        f.discovered_source_name
                          && f.discovered_source_name !== f.fund_name
                          ? `输入名: ${f.fund_name}\n抓到名: ${f.discovered_source_name}\n请核对是否为同一基金`
                          : undefined
                      }
                    >
                      {f.discovered_source_name ?? '—'}
                    </td>
                    <td className="py-3 px-4 text-gray-500">{f.apir_code ?? '—'}</td>
```

- [ ] **Step 4: 空状态列数 colSpan 调整 (若有)**

Grep `colSpan`:

```bash
grep -n "colSpan" webapp/frontend/src/pages/FundManagement.tsx
```

若有 `colSpan={7}` 或类似, 加 1 (原 7 -> 8; 原 6 -> 7)。

**如无 colSpan 用法则跳过此步**。

- [ ] **Step 5: 手工核**

```bash
cd webapp/frontend && npx tsc --noEmit 2>&1 | grep -i "FundManagement\|discovered" | head -5
```
Expected: 无相关错

启动前端 (若时间允许, 否则 Y.8 集中做):
```bash
cd webapp/frontend && npm run dev &
```
浏览器 `/funds` 目视核: 新列渲染, 若 fund.discovered_source_name 存在且 != fund_name -> 红字。

- [ ] **Step 6: Commit**

```bash
git add webapp/frontend/src/pages/FundManagement.tsx
git commit -m "feat(frontend): Spec B Y.4b FundManagement 加数据源基金名列 + 不一致标红

表格结构:
- 表头新增第 3 列 '数据源基金名' (在 基金名称/APIR 之间)
- 每行 td 展示 fund.discovered_source_name ?? '—'
- discovered_source_name !== fund_name 时: 红字 semibold + tooltip 显示两个名字供核对
- 一致或为空: gray-500 常规展示

补 colSpan (若有 7 -> 8)。Y.5 清库 + 重跑后, 8 支基金新列会有值; Coolabah × 2 保持 '—' (无 fundmonitors 覆盖)。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: 清库脚本 spec_b_wipe_and_rescrape.py + 单测 (Y.5 前置)

**Files:**
- Create: `llm_ingest/scripts/__init__.py` (若不存在)
- Create: `llm_ingest/scripts/spec_b_wipe_and_rescrape.py`
- Create: `tests/test_spec_b_wipe_script.py`

**Interfaces:**
- Consumes: `sqlite3`, `requests` (or `urllib`), `concurrent.futures.ThreadPoolExecutor`, `argparse`
- Produces: CLI `python llm_ingest/scripts/spec_b_wipe_and_rescrape.py [--yes] [--dry-run] [--fund-id X] [--skip-wipe]`

- [ ] **Step 1: 写失败测试**

Create `tests/test_spec_b_wipe_script.py`:

```python
"""Spec B 清库脚本单测. 主要盖 dry-run 不改 DB / Coolabah 排除 / 备份创建."""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "llm_ingest" / "scripts" / "spec_b_wipe_and_rescrape.py"


@pytest.fixture
def stub_db():
    """建一个有 funds + monthly_returns 数据的临时 DB."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.executescript("""
        CREATE TABLE funds (
            fund_id TEXT PRIMARY KEY,
            fund_name TEXT NOT NULL,
            confirmed_url TEXT NOT NULL,
            fetch_method TEXT NOT NULL,
            url_type TEXT NOT NULL,
            fundmonitors_fund_id INTEGER
        );
        CREATE TABLE monthly_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_id TEXT NOT NULL,
            date TEXT NOT NULL,
            net_return REAL NOT NULL,
            nav REAL NOT NULL
        );
        CREATE TABLE confirmed_gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_id TEXT NOT NULL,
            missing_month TEXT NOT NULL
        );
        CREATE TABLE pending_review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_id TEXT NOT NULL,
            date TEXT NOT NULL,
            net_return REAL NOT NULL,
            extract_method TEXT NOT NULL,
            review_state TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE fund_metrics (fund_id TEXT PRIMARY KEY, date_period TEXT);
        CREATE TABLE anomalies (id INTEGER PRIMARY KEY, fund_id TEXT);
        CREATE TABLE ai_reports (id INTEGER PRIMARY KEY, fund_id TEXT);
    """)
    conn.execute(
        "INSERT INTO funds (fund_id, fund_name, confirmed_url, fetch_method, url_type, fundmonitors_fund_id) "
        "VALUES ('bentham_global_income', 'Bentham Global Income Fund', 'https://a.com', 'code', 'archive', 3312)"
    )
    conn.execute(
        "INSERT INTO funds (fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES ('coolabah_frhy_assisted', 'Coolabah FRHY Assisted', 'https://c.com', 'code', 'archive')"
    )
    conn.execute(
        "INSERT INTO monthly_returns (fund_id, date, net_return, nav) VALUES "
        "('bentham_global_income', '2024-01-31', 0.005, 1.005), "
        "('coolabah_frhy_assisted', '2024-01-31', 0.003, 1.003)"
    )
    conn.commit()
    conn.close()
    yield tmp.name
    Path(tmp.name).unlink(missing_ok=True)


def _run_script(*args, db_path=None, expect_fail=False):
    env = {"FUND_DB_PATH": db_path} if db_path else {}
    import os
    env = {**os.environ, **env}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env,
    )
    if not expect_fail:
        assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    return result


def test_dry_run_does_not_modify_db(stub_db):
    result = _run_script("--dry-run", "--skip-wipe", db_path=stub_db)
    conn = sqlite3.connect(stub_db)
    n = conn.execute("SELECT COUNT(*) FROM monthly_returns").fetchone()[0]
    conn.close()
    assert n == 2  # 未清
    assert "dry-run" in result.stdout.lower() or "dry" in result.stdout.lower()


def test_dry_run_lists_targets(stub_db):
    result = _run_script("--dry-run", db_path=stub_db)
    # bentham 在触发列表, coolabah 排除
    assert "bentham_global_income" in result.stdout
    assert "coolabah_frhy_assisted" not in result.stdout


def test_dry_run_previews_backup_path(stub_db):
    result = _run_script("--dry-run", db_path=stub_db)
    assert "backup" in result.stdout.lower() or ".spec_b_backup_" in result.stdout


def test_fund_id_filter_skips_wipe(stub_db):
    """--fund-id X --skip-wipe -> 只触发单支, 不清表."""
    result = _run_script(
        "--skip-wipe", "--fund-id", "bentham_global_income", "--dry-run",
        db_path=stub_db,
    )
    conn = sqlite3.connect(stub_db)
    n = conn.execute("SELECT COUNT(*) FROM monthly_returns").fetchone()[0]
    conn.close()
    assert n == 2  # 未清


def test_backup_creation_before_wipe(stub_db, tmp_path, monkeypatch):
    """--yes 模式: 应创建 db.spec_b_backup_YYYYMMDD_HHMMSS 文件."""
    monkeypatch.chdir(tmp_path)
    shutil.copy(stub_db, tmp_path / "fund.db")

    # 用 dry-run 也应打印备份路径 (真跑要求 webapp 后端起, 单测不做)
    result = _run_script(
        "--dry-run", db_path=str(tmp_path / "fund.db"),
    )
    assert "backup" in result.stdout.lower()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python3 -c "import pytest; pytest.main(['tests/test_spec_b_wipe_script.py', '-v'])"
```
Expected: 全 FAIL (脚本不存在)

- [ ] **Step 3: 建 scripts/__init__.py (若无)**

```bash
mkdir -p llm_ingest/scripts
touch llm_ingest/scripts/__init__.py
```

- [ ] **Step 4: 写清库脚本**

Create `llm_ingest/scripts/spec_b_wipe_and_rescrape.py`:

```python
#!/usr/bin/env python3
"""Spec B: 清 monthly_returns 等 6 表 + 批量触发 fundmonitors L1 重爬.

7 步:
  1. 前置检查 (webapp 后端 / DB 文件 / .env / 磁盘)
  2. 备份 data/fund_analysis.db -> data/fund_analysis.db.spec_b_backup_{ts}
  3. 单事务清 6 表 (monthly_returns / confirmed_gaps / pending_review /
     fund_metrics / anomalies / ai_reports); funds 表保留
  4. 读 funds 过滤 Coolabah × 2 (延后 Spec C)
  5. ThreadPoolExecutor(max_workers=4) 并发触发 POST /api/ingest/funds
  6. 每 5 秒轮询 GET /api/ingest/jobs/{id} 直到全部 succeeded/failed
  7. 打印汇总

CLI:
  --yes         跳过 YES 确认
  --dry-run     只打印将执行的 SQL / 触发列表, 不实操
  --fund-id X   只跑单支 (跳过清库)
  --skip-wipe   跳步 2-3, 复用现有 DB (调试)

Exit code:
  0 - 所有 job succeeded
  1 - 前置检查失败 / 用户拒 YES
  2 - 至少一支 job failed
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as _dt
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WEBAPP_HOST = os.environ.get("WEBAPP_HOST", "http://127.0.0.1:8000")
HEALTH_URL = f"{WEBAPP_HOST}/health"
INGEST_URL = f"{WEBAPP_HOST}/api/ingest/funds"
JOB_URL_TPL = f"{WEBAPP_HOST}/api/ingest/jobs/{{}}"

EXCLUDED_FUNDS = {"coolabah_frhy_assisted", "coolabah_frhy_institutional"}
WIPE_TABLES = [
    "monthly_returns", "confirmed_gaps", "pending_review",
    "fund_metrics", "anomalies", "ai_reports",
]

DEFAULT_DB_PATH = Path("data/fund_analysis.db")


def _db_path() -> Path:
    """DB 路径解析: 优先 FUND_DB_PATH 环境变量, 兜底 data/fund_analysis.db."""
    env = os.environ.get("FUND_DB_PATH")
    return Path(env) if env else DEFAULT_DB_PATH


def _http_get_json(url: str, timeout: int = 30) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def _http_post_json(url: str, body: Dict[str, Any],
                    timeout: int = 30) -> Optional[Dict[str, Any]]:
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def check_prerequisites(dry_run: bool) -> Tuple[bool, str]:
    """前置检查. dry-run 模式跳过 webapp 后端和 .env 检查 (只测脚本自身)."""
    db = _db_path()
    if not db.exists():
        return False, f"DB 文件不存在: {db}"
    if dry_run:
        return True, "ok (dry-run 跳过 webapp/.env 检查)"
    # webapp 后端存活
    r = _http_get_json(HEALTH_URL)
    if r is None or r.get("status") != "ok":
        return False, f"webapp 后端未启 ({HEALTH_URL}). 起动: python webapp/backend/run.py"
    # .env SUB2API_KEY (虽本期 L1 不用, L2 fallback 用)
    if not os.environ.get("SUB2API_KEY"):
        # 尝试 .env 读
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("SUB2API_KEY="):
                    break
            else:
                return False, ".env 无 SUB2API_KEY (L2 PDF 通路需要)"
        else:
            return False, ".env 不存在"
    # 磁盘空间
    free = shutil.disk_usage(db.parent).free
    if free < 100 * 1024 * 1024:  # 100 MB
        return False, f"磁盘空间不足 ({free / 1e6:.1f} MB < 100 MB)"
    return True, "ok"


def make_backup(dry_run: bool) -> Optional[Path]:
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    src = _db_path()
    dst = src.with_name(f"{src.name}.spec_b_backup_{ts}")
    print(f"[backup] {src} -> {dst}")
    if dry_run:
        return dst
    shutil.copy2(src, dst)
    print(f"[backup] done ({dst.stat().st_size / 1e6:.1f} MB)")
    return dst


def wipe_tables(dry_run: bool) -> None:
    db = _db_path()
    print(f"[wipe] target: {db}")
    print(f"[wipe] tables: {WIPE_TABLES}")
    if dry_run:
        for t in WIPE_TABLES:
            print(f"[wipe] DRY-RUN would: DELETE FROM {t}")
        return
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.cursor()
        conn.execute("BEGIN")
        for t in WIPE_TABLES:
            # 表可能不存在 (schema 演化), 忽略
            try:
                cur.execute(f"DELETE FROM {t}")
                print(f"[wipe] {t}: {cur.rowcount} 行删")
            except sqlite3.OperationalError as e:
                print(f"[wipe] {t}: 跳过 ({e})")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_target_funds(fund_id_filter: Optional[str] = None) -> List[Tuple[str, str]]:
    conn = sqlite3.connect(str(_db_path()))
    try:
        rows = conn.execute("SELECT fund_id, fund_name FROM funds").fetchall()
    finally:
        conn.close()
    out = []
    for fid, fname in rows:
        if fid in EXCLUDED_FUNDS:
            continue
        if fund_id_filter and fid != fund_id_filter:
            continue
        out.append((fid, fname))
    return out


def trigger_one(fund_id: str, fund_name: str,
                dry_run: bool) -> Tuple[str, Optional[str], str]:
    """POST /api/ingest/funds; 返 (fund_id, job_id or None, message)."""
    body = {
        "fund_id": fund_id, "fund_name": fund_name,
        "issuer": None, "confirmed_url": None, "issuer_domain": None,
        "asx_code": None, "apir_code": None, "max_pdf_pages": None, "limit": None,
    }
    if dry_run:
        return (fund_id, None, f"DRY-RUN would POST {body}")
    resp = _http_post_json(INGEST_URL, body, timeout=60)
    if resp is None:
        return (fund_id, None, "POST 失败")
    return (fund_id, resp.get("job_id"), "queued")


def poll_jobs(job_ids: List[Tuple[str, str]], poll_sec: int = 5,
              max_wait_sec: int = 1800) -> Dict[str, Dict[str, Any]]:
    """轮询 job 直到全终态或超时. 返 {fund_id: final_job_json}."""
    results: Dict[str, Dict[str, Any]] = {}
    pending = dict(job_ids)  # fund_id -> job_id
    deadline = time.time() + max_wait_sec
    while pending and time.time() < deadline:
        time.sleep(poll_sec)
        done_now = []
        for fid, jid in pending.items():
            r = _http_get_json(JOB_URL_TPL.format(jid))
            if r is None:
                continue
            state = r.get("state", "")
            if state in ("succeeded", "failed"):
                results[fid] = r
                done_now.append(fid)
                print(f"[poll] {fid} -> {state} ({r.get('stats')})")
        for fid in done_now:
            pending.pop(fid)
    if pending:
        for fid, jid in pending.items():
            print(f"[poll] {fid}: timeout, job_id={jid}")
            results[fid] = {"state": "timeout", "job_id": jid}
    return results


def summarize(results: Dict[str, Dict[str, Any]]) -> int:
    """打印汇总. 返 exit code (0 = 全 succeeded, 2 = 有 failed)."""
    print("\n" + "=" * 60)
    print("[summary]")
    print("=" * 60)
    fail_count = 0
    for fid, r in sorted(results.items()):
        state = r.get("state", "?")
        stats = r.get("stats", {})
        err = r.get("error", "")
        if state == "succeeded":
            print(f"[OK]   {fid}: monthly={stats.get('monthly', 0)}, "
                  f"pending={stats.get('pending', 0)}, gap={stats.get('gap', 0)}")
        else:
            fail_count += 1
            print(f"[FAIL] {fid}: state={state}, error={err}")
    print("=" * 60)
    print(f"total: {len(results)}, failed: {fail_count}")
    return 0 if fail_count == 0 else 2


def confirm_prompt(target_count: int) -> bool:
    """交互 YES 确认."""
    print(f"\n将清 6 表 (funds 表保留) 并重跑 {target_count} 支基金。")
    print(f"备份路径: {_db_path()}.spec_b_backup_{{ts}}")
    ans = input("输 YES 继续, 其他任意键退出: ").strip()
    return ans == "YES"


def main() -> int:
    ap = argparse.ArgumentParser(description="Spec B 全清 + 重爬")
    ap.add_argument("--yes", action="store_true", help="跳过交互 YES 确认")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将执行的 SQL / 触发列表, 不实操")
    ap.add_argument("--fund-id", default=None, help="只跑单支 (需与 --skip-wipe 配合)")
    ap.add_argument("--skip-wipe", action="store_true",
                    help="跳过步 2-3 (备份+清表), 复用现有 DB")
    args = ap.parse_args()

    print(f"[spec_b] {'DRY-RUN' if args.dry_run else 'LIVE'} mode")

    ok, msg = check_prerequisites(args.dry_run)
    if not ok:
        print(f"[prereq] FAIL: {msg}")
        return 1
    print(f"[prereq] {msg}")

    targets = load_target_funds(args.fund_id)
    if not targets:
        print(f"[targets] 无匹配基金 (fund_id={args.fund_id})")
        return 1
    print(f"[targets] {len(targets)} 支: {[t[0] for t in targets]}")

    if not args.skip_wipe:
        if not args.yes and not args.dry_run and not confirm_prompt(len(targets)):
            print("[confirm] 用户拒绝, 退出")
            return 1
        make_backup(args.dry_run)
        wipe_tables(args.dry_run)

    if args.dry_run:
        for fid, fname in targets:
            trigger_one(fid, fname, dry_run=True)
        print("[spec_b] DRY-RUN 完成, 未实际触发")
        return 0

    # 并发触发
    triggered: List[Tuple[str, str]] = []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(trigger_one, fid, fname, False): fid
                   for fid, fname in targets}
        for fut in cf.as_completed(futures):
            fid, jid, msg2 = fut.result()
            print(f"[trigger] {fid} -> {jid} ({msg2})")
            if jid:
                triggered.append((fid, jid))
    if not triggered:
        print("[trigger] 全部触发失败")
        return 2

    results = poll_jobs(triggered)
    return summarize(results)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: 跑单测**

```bash
python3 -c "import pytest; pytest.main(['tests/test_spec_b_wipe_script.py', '-v'])"
```
Expected: 5 passed

- [ ] **Step 6: dry-run 手工验证 (生产 DB)**

```bash
python3 llm_ingest/scripts/spec_b_wipe_and_rescrape.py --dry-run
```
Expected:
- `[prereq] ok (dry-run 跳过...)`
- `[targets]` 输出应含 8 支 (不含 coolabah × 2)
- `[wipe] DRY-RUN would: DELETE FROM ...` × 6
- `DRY-RUN would POST {body}` × 8

- [ ] **Step 7: Commit**

```bash
git add llm_ingest/scripts/ tests/test_spec_b_wipe_script.py
git commit -m "feat(scripts): Spec B 清库脚本 spec_b_wipe_and_rescrape.py

7 步流程:
  1. 前置检查 (webapp health / DB 文件 / SUB2API_KEY / 磁盘 100MB)
  2. cp 备份 data/fund_analysis.db.spec_b_backup_{ts}
  3. 单事务清 6 表 (monthly_returns/confirmed_gaps/pending_review/
     fund_metrics/anomalies/ai_reports), funds 表保白名单
  4. 读 funds 排除 Coolabah × 2 (Spec C)
  5. ThreadPoolExecutor(max_workers=4) 并发 POST /api/ingest/funds
  6. 每 5 秒轮询 GET /api/ingest/jobs/{id} 直全终态或超时 30 分
  7. 汇总 monthly/pending/gap 各支 + failed 原因

CLI: --yes/--dry-run/--fund-id X/--skip-wipe
Exit code: 0 全绿 / 1 前置失败 / 2 有 failed

5 单测覆盖 dry-run 不改 DB / Coolabah 排除 / 备份路径 / --fund-id 单支模式 / 备份创建。

尚未 Y.5 执行, 只是脚本落地; Y.5 用 --yes 跑 live 版。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: 单支基金 smoke test (Y.5 预验证)

**Files:** 无代码改动, 只跑手工命令

- [ ] **Step 1: 启动 webapp 后端**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
python3 webapp/backend/run.py &
```
Expected: uvicorn listening on 127.0.0.1:8000; lifespan 触发 migrations.apply 无报错

等 3 秒后核 health:
```bash
curl -s http://127.0.0.1:8000/health
```
Expected: `{"status":"ok"}`

- [ ] **Step 2: 单支 dry-run**

```bash
python3 llm_ingest/scripts/spec_b_wipe_and_rescrape.py --skip-wipe --fund-id yarra_enhanced_income_fund --dry-run
```
Expected:
- prereq ok
- targets 只列 Yarra
- DRY-RUN POST body 打印

- [ ] **Step 3: 单支 live 触发 (backup 备份后)**

```bash
# 先手工备份 (脚本 --skip-wipe 不会自动备份)
cp data/fund_analysis.db data/fund_analysis.db.spec_b_task8_backup_$(date +%Y%m%d_%H%M%S)

# live 触发 (跳清库, 只跑单支)
python3 llm_ingest/scripts/spec_b_wipe_and_rescrape.py --skip-wipe --fund-id yarra_enhanced_income_fund --yes
```
Expected:
- `[trigger] yarra_enhanced_income_fund -> <job_id> (queued)`
- 轮询 60 秒内 `[poll] yarra_enhanced_income_fund -> succeeded (...)`
- summary `[OK] yarra_enhanced_income_fund: monthly=N, pending=0, gap=0`

- [ ] **Step 4: 核对 Yarra 数据**

```bash
sqlite3 data/fund_analysis.db "SELECT COUNT(*), MIN(date), MAX(date) FROM monthly_returns WHERE fund_id='yarra_enhanced_income_fund'"
sqlite3 data/fund_analysis.db "SELECT discovered_source_name FROM funds WHERE fund_id='yarra_enhanced_income_fund'"
sqlite3 data/fund_analysis.db "SELECT COUNT(DISTINCT pattern_tag), pattern_tag FROM monthly_returns WHERE fund_id='yarra_enhanced_income_fund' GROUP BY pattern_tag"
```
Expected:
- COUNT >= 24 月 (fundmonitors 深覆盖)
- discovered_source_name 非 NULL
- pattern_tag = `fundmonitors_table` 单一

**若失败**:
```bash
# 回滚到 task 8 前
cp data/fund_analysis.db.spec_b_task8_backup_* data/fund_analysis.db
```

- [ ] **Step 5: 关后端**

```bash
# 找 pid
pkill -f "uvicorn.*8000" || pkill -f "webapp/backend/run.py"
```

- [ ] **Step 6: Commit (无代码变更, 只 log)**

不 commit. 手工验证阶段结果记录到 plan 检查框即可。

**若 Step 4 失败**: 停下, 检查 `fundmonitors.probe` / `ingest._run_ingest_job` 逻辑。若因 CF/paywall 失败 -> 重跑; 若因代码 bug -> Task 3 或 5 回归修补。

---

## Task 9: Y.5 全量清库 + 8 支重跑 (不可逆)

**Files:** 无代码改动, 大动作 = 数据变更

**⚠️ 关键: 这是唯一不可逆 task, 必须 Y.1-Y.8 全绿后执行**

前置核对 (**必须全 checked**):
- [ ] Y.1-Y.8 全部 commit 完 (git log 应见 8 个 commit)
- [ ] `python3 -c "import pytest; pytest.main(['tests/', '-v'])"` 全绿 (预期 113 passed)
- [ ] webapp 后端可启, curl health 返 ok
- [ ] `df -h data/` 有 > 100 MB 空闲
- [ ] `.env` 有 SUB2API_KEY
- [ ] 磁盘上无同名 backup 文件 (避免覆盖)

- [ ] **Step 1: 启动 webapp 后端**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
python3 webapp/backend/run.py > /tmp/uvicorn_spec_b.log 2>&1 &
sleep 3
curl -s http://127.0.0.1:8000/health
```
Expected: `{"status":"ok"}`

- [ ] **Step 2: 全清 + 重爬 (交互 YES 确认)**

```bash
python3 llm_ingest/scripts/spec_b_wipe_and_rescrape.py
```
交互提示时输 `YES` 回车确认。

Expected 输出流:
```
[prereq] ok
[targets] 8 支: ['bentham_global_income', 'jcb_active_bond', ...]
将清 6 表 (funds 表保留) 并重跑 8 支基金。
备份路径: data/fund_analysis.db.spec_b_backup_{ts}
输 YES 继续: YES
[backup] ... done (X.X MB)
[wipe] monthly_returns: 869 行删
[wipe] confirmed_gaps: N 行删
[wipe] pending_review: N 行删
...
[trigger] bentham_global_income -> <jid> (queued)
[trigger] jcb_active_bond -> <jid> (queued)
...
[poll] bentham_global_income -> succeeded (...)
[poll] jcb_active_bond -> succeeded (...)
...
[summary]
[OK]   bentham_global_income: monthly=N, pending=0, gap=0
...
total: 8, failed: 0
```

**预期总耗时**: 30 分钟 (fundmonitors 每支 ~20 秒 + 4 并发)

- [ ] **Step 3: 若有 failed**

看 `[FAIL] xxx: state=..., error=...` 逐支排查。

**GCI + Stake 白名单未填**: 若 status=`no_fundid` -> 该支通过 Tavily 探测, 探测失败即标 confirmed_gap 转 Spec C。手工核:
```bash
sqlite3 data/fund_analysis.db "SELECT fund_id, missing_month FROM confirmed_gaps ORDER BY fund_id"
```

**若 >4 支 gate_fail** (fundmonitors 改版风险):
```bash
# 回滚
cp data/fund_analysis.db.spec_b_backup_* data/fund_analysis.db
```
调试 fundmonitors.py 后重跑 Task 9。

- [ ] **Step 4: 数据完整性 4 项验收**

```bash
# 4.1 pattern_tag 应全 = fundmonitors_table
sqlite3 data/fund_analysis.db "SELECT pattern_tag, COUNT(*) FROM monthly_returns GROUP BY pattern_tag"
```
Expected: 单行 `fundmonitors_table|N`

```bash
# 4.2 每支覆盖情况
sqlite3 data/fund_analysis.db "SELECT fund_id, MIN(date), MAX(date), COUNT(*) FROM monthly_returns GROUP BY fund_id ORDER BY fund_id"
```
Expected: 6~8 支各有 >= 24 月; Coolabah × 2 无记录

```bash
# 4.3 discovered_source_name 覆盖
sqlite3 data/fund_analysis.db "SELECT fund_id, discovered_source_name FROM funds ORDER BY fund_id"
```
Expected: 6 支白名单基金 discovered_source_name 非 NULL; Coolabah × 2 保持 NULL; GCI/Stake 若探测失败也 NULL

```bash
# 4.4 前端 API 返新字段
curl -s http://127.0.0.1:8000/api/funds | python3 -c "
import json, sys
data = json.load(sys.stdin)
for f in data:
    print(f\"{f['fund_id']}: discovered_source_name={f.get('discovered_source_name')}\")
"
```
Expected: 每支基金一行, 白名单基金有值

- [ ] **Step 5: 关后端**

```bash
pkill -f "webapp/backend/run.py"
```

- [ ] **Step 6: Commit 数据变更标记 (不含 DB, DB 不入 git)**

不 commit DB 文件 (已在 .gitignore)。可以 commit 一个 log:

```bash
# 手工留个日志
cat > docs/superpowers/logs/2026-07-17-spec-b-Y5-execution.md << 'EOF'
# Spec B Y.5 执行日志

- **日期**: $(date +%Y-%m-%d %H:%M)
- **备份**: data/fund_analysis.db.spec_b_backup_$(date +%Y%m%d_%H%M%S)
- **清: 6 表**, funds 保留
- **重爬**: 8 支基金 (Coolabah × 2 除外)
- **结果**: [从 Step 4 输出粘贴此处]
EOF

mkdir -p docs/superpowers/logs
git add docs/superpowers/logs/2026-07-17-spec-b-Y5-execution.md
git commit -m "docs(spec-b): Y.5 全清+重爬执行日志 (数据变更, DB 不入 git)"
```

---

## Task 10: GCI inception 重置 + 端到端手工验收 (Y.6-Y.10)

**Files:**
- 手工 SQL UPDATE
- `webapp/frontend/src/pages/FundManagement.tsx` (只验证不改)

- [ ] **Step 1: 探 GCI 第一份真实 PDF 日期**

```bash
ls -la data/pdf_cache/gryphon_capital_income/ | head -20
```

**若 GCI 走的是 L1 fundmonitors 而非 L2 PDF** (Y.5 后应如此), 则第一份"真实数据日期"= fundmonitors 的最早覆盖月:
```bash
sqlite3 data/fund_analysis.db "SELECT MIN(date) FROM monthly_returns WHERE fund_id='gryphon_capital_income'"
```

- [ ] **Step 2: UPDATE inception_date**

假设 MIN date = `2020-05-31`:
```bash
sqlite3 data/fund_analysis.db "UPDATE funds SET inception_date='2020-05-31', inception_assumed=0 WHERE fund_id='gryphon_capital_income'"
```

**核对**:
```bash
sqlite3 data/fund_analysis.db "SELECT fund_id, inception_date, inception_assumed FROM funds WHERE fund_id='gryphon_capital_income'"
```
Expected: `2020-05-31|0` (非 2018-05-21 skills-era 假值)

- [ ] **Step 3: 启动前后端**

```bash
# 后端
python3 webapp/backend/run.py &
# 前端
cd webapp/frontend && npm run dev &
```

- [ ] **Step 4: 浏览器手工核 (Y.8)**

- 浏览器打开 http://localhost:5173/funds (或 vite 分配的端口)
- 核对表格新列"数据源基金名" 渲染
- 若 fund_name 与 discovered_source_name 不同 -> cell 红字 + hover tooltip
- Coolabah × 2 该列显 `—`
- 点"添加基金"弹窗, 填一个新基金名 (如 `Test Fund X`, 不填 URL), 触发 ingest 任务, 轮询进度到 succeeded/failed
- 核基金列表新加行的 discovered_source_name 值

- [ ] **Step 5: 全测试跑一遍**

```bash
python3 -c "import pytest; pytest.main(['tests/', '-v', '--tb=short'])"
```
Expected: 113 passed (删 24 + 新 27 = +3 于 110 基线)

- [ ] **Step 6: 关服务**

```bash
pkill -f "webapp/backend/run.py"
pkill -f "vite"
```

- [ ] **Step 7: Commit inception 修复 (无代码变更, 只 SQL log)**

同 Task 9 Step 6, 追加到 log 文件:
```bash
cat >> docs/superpowers/logs/2026-07-17-spec-b-Y5-execution.md << 'EOF'

## Y.7 GCI inception 重置

- 老值 (skills-era 假): 2018-05-21
- 新值 (fundmonitors 首月): $(sqlite3 data/fund_analysis.db "SELECT inception_date FROM funds WHERE fund_id='gryphon_capital_income'")
EOF

git add docs/superpowers/logs/2026-07-17-spec-b-Y5-execution.md
git commit -m "docs(spec-b): Y.7 GCI inception 重置日志"
```

- [ ] **Step 8: 打 tag spec-b-done (Y.10)**

```bash
git tag spec-b-done
git log --oneline llm-ingest-gemini ^main | head -20
```
Expected: 8+ commit (Y.1-Y.10 各一)

---

## Self-Review Checklist

- [x] **Spec Section 1 (目标 + 三根问题)** 覆盖: Task 3 (删 guard) / Task 5 (反转 L1/L2) / Task 9 (全清重爬)
- [x] **Spec Section 2 (5 处改动)** 全覆盖: Task 1 (迁移) / Task 2 (fundmonitors 加 page_name) / Task 3 (probe 反转) / Task 4 (models+schemas+types) / Task 5 (ingest L1) / Task 6 (FundManagement.tsx) / Task 7 (清库脚本)
- [x] **Spec Section 3 (Y phase 序)** 映射: Task 1=Y.1, Task 2-3=Y.2, Task 4=Y.4a, Task 5=Y.3, Task 6=Y.4b, Task 7=Y.5 前置, Task 8=Y.5 smoke, Task 9=Y.5 全跑, Task 10=Y.6-Y.10
- [x] **Spec Section 4 (测试)**: 删 24 (Task 3 Step 5), 新 27 (Task 1: 4 + Task 2: 8 + Task 3: 4 + Task 5: 6 + Task 7: 5)
- [x] **Spec Section 5 (错误矩阵)**: Task 7 脚本前置检查 + Task 9 Step 3 处理; 回滚见每 task 的 Commit step
- [x] **Spec Section 6 (风险)**: Task 8 (单支 smoke) 挡风险 1-2 (fundmonitors 改版); Task 9 Step 3 挡风险 5 (Yarra/KKR 变少)
- [x] **Spec Section 7 (完成标志 6 项)**: 每项对应 Task 10 Step 4-8 中的具体命令
- [x] **Spec Section 12 (前置条件)**: 明确列在 Global Constraints 和 Task 9 前置核对表

**Placeholder scan**: 无 TBD/TODO/占位符。每个 Step 都有具体命令/代码/预期输出。

**Type consistency**:
- `_extract_page_fund_name(markdown) -> Optional[str]` -- Task 2 定义, Task 3 使用一致
- `probe(...) -> Dict[str, object]` with `page_fund_name` key -- Task 3 定义, Task 5 集成使用
- `Fund.discovered_source_name` -- Task 4 定义 (ORM/schema/TS), Task 5 (SQL UPDATE) 和 Task 6 (JSX) 使用同名
- `_run_ingest_job(jid, req)` -- 既有签名不变; `_JOBS[jid]["stats"]` 结构保持 `{"monthly", "pending", "gap", "download_fail"}`

**Scope check**: 与 Spec 一致 -- 单一 subsystem (llm_ingest + webapp 数据源架构)。Coolabah/Macquarie CSV/L2 补差/cron 全列 Spec C。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-17-spec-b-execution-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - Fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
