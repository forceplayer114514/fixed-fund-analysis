# Task 1: extract.py 新增 PDF 提取函数（4 个）+ 测试

**阶段 1-A**（优化计划阶段 1 前半）。承接阶段 0（/tmp 垃圾已清理）。

## Files
- Modify: `lib/extract.py`（末尾新增 4 函数，复用现有 `parse_pdf_text`/`clean_spacing`/`MONTH_MAP`/`extract_month_prefix`）
- Test: `tests/test_extract.py`（末尾新增测试用例）

## Interfaces
- Consumes: `parse_pdf_text`（已有）、`clean_spacing`（已有）、`MONTH_MAP`（已有）、`extract_month_prefix`（已有）
- Produces:
  - `extract_commentary_return(text: str) -> Optional[float]`
  - `extract_perf_rolling(text: str) -> dict`
  - `extract_pdf_one(pdf_path: str, max_pages: Optional[int] = None) -> tuple[Optional[float], dict]`
  - `extract_pdf_links_from_archive(markdown: str) -> list[tuple[str, str]]`

## 数据完整性硬约束（最高优先级，不可违反）
1. **Commentary 正文优先于 performance 表 1mo**：复利验证已证明 performance 表 1mo 口径错误，Commentary 正文值才是当月真实收益
2. **负号强制捕获**：所有百分比正则统一用 `[+-]?\d+\.\d+%`（正数可省略正号，负号 `-0.26%` 必须捕获）
3. **extract_perf_rolling 按列标题对应值，不靠位置**：解决"5 列 4 值"错位根因；显式处理 `-` 空列 -> `None`
4. **不捏造、不插值**：无匹配返回 `None`，绝不猜测；异常值保留不纠正
5. 提取层只做纯文本到数字的映射，禁止 backfill/forward-fill

## Stake 真实数据口径（供测试参考）
- Commentary 正文格式：`The Fund returned 0.53% in April 2025.` / `returned -0.26% in March 2026.`
- performance 表列标题顺序：`1 month | 3 months | 6 months | 12 months | Since inception`
- 数据行以 `Class A` 开头
- Nov 2025 特例：12mo 与 inception 值相同（5.89%），表呈现 5 列标题但 Class A 行仅 4 个值 -> `parse_error=True`（不致命，因 Commentary 优先，performance 表仅用于复利验证）

## Steps

- [ ] **Step 1: 在 `tests/test_extract.py` 末尾新增测试用例**

在文件末尾（`test_parse_pdf_text_max_pages` 之后）追加：

```python
from lib.extract import (
    extract_commentary_return,
    extract_perf_rolling,
    extract_pdf_one,
    extract_pdf_links_from_archive,
)


# --- 11. extract_commentary_return ---
def test_extract_commentary_return_positive():
    """正数（省略正号）。"""
    text = "The Fund returned 0.53% in April 2025."
    assert extract_commentary_return(text) == 0.0053


def test_extract_commentary_return_negative():
    """负数（捕获符号）。"""
    text = "The Fund returned -0.26% in March 2026."
    assert extract_commentary_return(text) == -0.0026


def test_extract_commentary_return_with_plus():
    """显式正号。"""
    text = "returned +1.05% for the month"
    assert extract_commentary_return(text) == 0.0105


def test_extract_commentary_return_no_match():
    """无匹配返回 None。"""
    assert extract_commentary_return("No percentage here.") is None


def test_extract_commentary_return_first_match():
    """取第一个 returned 匹配（当月声明，非后续滚动收益）。"""
    text = "returned 0.53% in April. Over 3 months returned 1.50%."
    assert extract_commentary_return(text) == 0.0053


# --- 12. extract_perf_rolling ---
def test_extract_perf_rolling_normal():
    """正常 5 列 5 值。"""
    text = (
        "Performance (after fees) 1 month 3 months 6 months "
        "12 months Since inception\n"
        "Class A 0.53% 1.50% 3.00% 5.89% 6.91%"
    )
    r = extract_perf_rolling(text)
    assert r["1mo"] == 0.0053
    assert r["3mo"] == 0.0150
    assert r["6mo"] == 0.0300
    assert r["12mo"] == 0.0589
    assert r["inception"] == 0.0691
    assert r["parse_error"] is False


def test_extract_perf_rolling_with_dash():
    """含 '-' 空列（基金未满 12 个月）。"""
    text = (
        "Performance 1 month 3 months 6 months 12 months Since inception\n"
        "Class A 0.53% 1.50% - - 2.00%"
    )
    r = extract_perf_rolling(text)
    assert r["1mo"] == 0.0053
    assert r["3mo"] == 0.0150
    assert r["6mo"] is None
    assert r["12mo"] is None
    assert r["inception"] == 0.0200
    assert r["parse_error"] is False


def test_extract_perf_rolling_misaligned():
    """Nov 2025 特例：4 值 5 列（12mo=inception 合并）-> parse_error 但部分填充。"""
    text = (
        "Performance 1 month 3 months 6 months 12 months Since inception\n"
        "Class A 0.42% 1.20% 2.50% 5.89%"
    )
    r = extract_perf_rolling(text)
    assert r["parse_error"] is True
    assert r["1mo"] == 0.0042
    assert r["12mo"] == 0.0589
    assert r["inception"] is None


def test_extract_perf_rolling_no_table():
    """无 performance 表 -> parse_error。"""
    r = extract_perf_rolling("No table here.")
    assert r["parse_error"] is True


def test_extract_perf_rolling_negative_value():
    """滚动收益含负值。"""
    text = (
        "Performance 1 month 3 months 6 months 12 months Since inception\n"
        "Class A -0.26% -0.50% 1.00% 2.00% 3.00%"
    )
    r = extract_perf_rolling(text)
    assert r["1mo"] == -0.0026
    assert r["3mo"] == -0.0050
    assert r["parse_error"] is False


# --- 13. extract_pdf_one（mock parse_pdf_text）---
def test_extract_pdf_one(monkeypatch):
    """组合：parse_pdf_text -> commentary + rolling。"""
    fake_text = (
        "The Fund returned 0.53% in April. "
        "Performance 1 month 3 months 6 months 12 months Since inception "
        "Class A 0.53% 1.50% 3.00% 5.89% 6.91%"
    )
    monkeypatch.setattr(
        "lib.extract.parse_pdf_text", lambda p, max_pages=None: fake_text
    )
    commentary, rolling = extract_pdf_one("/fake/path.pdf")
    assert commentary == 0.0053
    assert rolling["12mo"] == 0.0589
    assert rolling["parse_error"] is False


def test_extract_pdf_one_no_commentary(monkeypatch):
    """Commentary 缺失返回 None，rolling 仍解析。"""
    fake_text = (
        "Performance 1 month 3 months 6 months 12 months Since inception "
        "Class A 0.53% 1.50% 3.00% 5.89% 6.91%"
    )
    monkeypatch.setattr(
        "lib.extract.parse_pdf_text", lambda p, max_pages=None: fake_text
    )
    commentary, rolling = extract_pdf_one("/fake/path.pdf")
    assert commentary is None
    assert rolling["1mo"] == 0.0053


# --- 14. extract_pdf_links_from_archive ---
def test_extract_pdf_links_from_archive_markdown_links():
    """markdown 链接 [text](url.pdf)。"""
    md = (
        "# Monthly Performance Reports\n"
        "- April 2025: [Report](https://example.com/apr-2025.pdf)\n"
        "- March 2025: [Report](https://example.com/mar-2025.pdf)\n"
    )
    links = extract_pdf_links_from_archive(md)
    assert ("2025-04", "https://example.com/apr-2025.pdf") in links
    assert ("2025-03", "https://example.com/mar-2025.pdf") in links
    assert len(links) == 2


def test_extract_pdf_links_from_archive_bare_url():
    """裸 url.pdf。"""
    md = "May 2025 Report: https://example.com/may-2025.pdf"
    links = extract_pdf_links_from_archive(md)
    assert ("2025-05", "https://example.com/may-2025.pdf") in links


def test_extract_pdf_links_from_archive_empty():
    """空输入返回空列表。"""
    assert extract_pdf_links_from_archive("") == []


def test_extract_pdf_links_from_archive_dedup():
    """重复链接去重。"""
    md = (
        "April 2025: https://example.com/apr-2025.pdf\n"
        "April 2025 again: https://example.com/apr-2025.pdf"
    )
    links = extract_pdf_links_from_archive(md)
    assert len(links) == 1
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/chong/Desktop/fixed_fund_analysis/skills && python3 -m pytest tests/test_extract.py -v`
Expected: FAIL（`ImportError: cannot import name 'extract_commentary_return'`）

- [ ] **Step 3: 在 `lib/extract.py` 末尾新增 4 函数实现**

在 `parse_pdf_text` 函数之后追加：

```python
# ---------------------------------------------------------------------------
# PDF 提取：Commentary 当月收益 + performance 表滚动收益
# 数据完整性：Commentary 正文优先（复利验证已证明 performance 表 1mo 口径
# 错误）；负号强制捕获；按列标题对应值不靠位置；不捏造不插值。
# ---------------------------------------------------------------------------


def extract_commentary_return(text: str) -> Optional[float]:
    """从 PDF 文本提取 Commentary 当月收益（after fees，返回小数）。

    正则 r'returned\\s+([+-]?\\d+\\.\\d+)%'，捕获符号。正数省略正号正常
    （"0.53%"），负号必须捕获（"-0.26%"）。取第一个匹配（当月声明，非后续
    滚动收益数字）。无匹配返回 None（绝不猜测）。

    Commentary 正文优先于 performance 表 1mo：复利交叉验证已证明 performance
    表 1mo 口径错误（列错位/合并），Commentary 正文值才是当月真实收益。
    """
    if not text:
        return None
    m = re.search(r"returned\s+([+-]?\d+\.\d+)%", text)
    if not m:
        return None
    return float(m.group(1)) / 100.0


# performance 表列标题 -> 结果 key 映射（按 Stake 月报固定顺序）
_PERF_COL_KEYS = [
    ("1 month", "1mo"),
    ("3 months", "3mo"),
    ("6 months", "6mo"),
    ("12 months", "12mo"),
    ("since inception", "inception"),
]


def extract_perf_rolling(text: str) -> dict:
    """提取 performance 表 Class A 滚动收益。

    按列标题对应值（不靠位置，解决"5 列 4 值"错位根因）；显式处理 '-' 空列
    -> None。返回 {'1mo':..,'3mo':..,'6mo':..,'12mo':..,'inception':..,
    'parse_error':bool}。

    parse_error=True 表示表结构异常（如 Nov 2025 的 12mo=inception 合并致
    值数!=列数），此时仍按顺序部分填充已知值。parse_error 不致命：Commentary
    正文才是当月收益来源，performance 表仅用于复利交叉验证。
    """
    result = {
        "1mo": None, "3mo": None, "6mo": None,
        "12mo": None, "inception": None, "parse_error": False,
    }
    if not text:
        result["parse_error"] = True
        return result

    # 按行处理，每行压缩空白（保留行结构以分离列标题行与数据行）
    lines = [clean_spacing(ln).strip() for ln in text.split("\n")]

    # 找列标题行（同时含 "1 month" 与 "since inception"）
    header_idx = -1
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "1 month" in low and "since inception" in low:
            header_idx = i
            break
    if header_idx < 0:
        result["parse_error"] = True
        return result

    # 解析列标题在行中的出现顺序
    header_low = lines[header_idx].lower()
    col_specs = []
    for label, key in _PERF_COL_KEYS:
        pos = header_low.find(label)
        if pos >= 0:
            col_specs.append((pos, key))
    col_specs.sort()
    keys_in_order = [s[1] for s in col_specs]
    if not keys_in_order:
        result["parse_error"] = True
        return result

    # 找 Class A 数据行（标题行之后首个含 "class a" 的行）
    data_line = None
    for ln in lines[header_idx + 1:]:
        if re.search(r"class\s*a", ln, re.IGNORECASE):
            data_line = ln
            break
    if not data_line:
        result["parse_error"] = True
        return result

    # 从数据行提取值 token：[+-]?\d+\.\d+% 或 "-"（先去掉 "Class A" 标签）
    data_clean = re.sub(r"class\s*a", " ", data_line, flags=re.IGNORECASE)
    tokens = re.findall(r"[+-]?\d+\.\d+%|-", data_clean)

    if len(tokens) != len(keys_in_order):
        result["parse_error"] = True

    # 按顺序对应（多余 token 忽略，不足留 None）
    for i, key in enumerate(keys_in_order):
        if i < len(tokens):
            tok = tokens[i]
            result[key] = None if tok == "-" else float(tok.rstrip("%")) / 100.0
    return result


def extract_pdf_one(
    pdf_path: str, max_pages: Optional[int] = None
) -> tuple[Optional[float], dict]:
    """单 PDF 提取纯函数（顶层，可被 ThreadPool/ProcessPool 调用）。

    parse_pdf_text -> extract_commentary_return + extract_perf_rolling。
    返回 (commentary_return, rolling)。失败返回 (None, {'parse_error':True})。

    顶层纯函数设计：未来可一行切 ProcessPoolExecutor 应对大批量（100+ PDF）。
    """
    try:
        text = parse_pdf_text(pdf_path, max_pages=max_pages)
    except Exception:
        return (None, {"1mo": None, "3mo": None, "6mo": None,
                       "12mo": None, "inception": None, "parse_error": True})
    return (extract_commentary_return(text), extract_perf_rolling(text))


def extract_pdf_links_from_archive(markdown: str) -> list[tuple[str, str]]:
    """从归档页 markdown 提取 [(YYYY-MM, pdf_url), ...]。

    匹配所有 .pdf URL，用 extract_month_prefix 从 URL 识别月份。去重保持顺序。
    无法识别月份的 URL 跳过（不猜测）。
    """
    if not markdown:
        return []
    urls = re.findall(r"https?://[^\s\)]+\.pdf", markdown, re.IGNORECASE)
    results: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for url in urls:
        ym = extract_month_prefix(url)
        if ym is None:
            continue
        key = (ym, url)
        if key not in seen:
            seen.add(key)
            results.append(key)
    return results
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/chong/Desktop/fixed_fund_analysis/skills && python3 -m pytest tests/test_extract.py -v`
Expected: 全部 PASS（原有 + 新增约 15 用例）

- [ ] **Step 5: 运行全部测试确认无回归**

Run: `cd /Users/chong/Desktop/fixed_fund_analysis/skills && python3 -m pytest tests/ -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add skills/lib/extract.py skills/tests/test_extract.py
git commit -m "feat(skills): add PDF extraction functions to extract.py

新增 4 个提取函数（Task 1，阶段 1-A）:
- extract_commentary_return: Commentary 当月收益，负号强制捕获
- extract_perf_rolling: 按列标题对应值，处理 '-' 空列与 5列4值错位
- extract_pdf_one: 单 PDF 提取纯函数（可切 ProcessPool）
- extract_pdf_links_from_archive: 归档页提 PDF 链接

数据完整性: Commentary 正文优先于 performance 表 1mo（复利验证证明）,
不捏造不插值, 异常值保留。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

## 验收标准
1. `tests/test_extract.py` 全部 PASS（含新增 ~15 用例）
2. `tests/` 全部 PASS 无回归
3. `lib/extract.py` 末尾有 4 个新函数，签名与 Interfaces 一致
4. extract_commentary_return 捕获负号（`-0.26%` -> -0.0026）
5. extract_perf_rolling 的 Nov 2025 错位用例返回 parse_error=True 且部分填充
6. 已提交（commit message 含 Co-Authored-By）

## 完成后
在 `skills/.superpowers/sdd/task-1-report.md` 写报告：列出新增函数、测试用例数、pytest 输出摘要、commit hash。
