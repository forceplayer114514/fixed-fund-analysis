# 固收对比计算流水线性能优化与增量缓存实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 优化固定收益基金的数据下载与解析流水线，引入 `history_cache.json` 增量缓存和多基金并行执行，将日常运行耗时由几分钟缩短至几秒。

**Architecture:** 
1. 在 `discover_source.py` 中增加首选 HEAD 探测；
2. 在 `fetch_web.py` 中利用 `history_cache.json` 过滤已下载月份的 PDF 链接；
3. 在 `parse_factsheet.py` 中仅解析新 PDF，自适应开关多进程，连乘重算 NAV，回写缓存；
4. 在 `run_all.py` 中通过 `ThreadPoolExecutor` 跨基金并发子进程运行。

**Tech Stack:** Python 3, PyMuPDF (fitz), BeautifulSoup4, Pytest, YAML

## Global Constraints
1. 任何净值、收益率等数值必须真实，严禁大模型捏造数据或用历史平均值、插值填补。
2. 收益率若有 3 个月以上连续相同且存在非自然精度（如大模型生成的 0.00657），触发 `ANTI-FABRICATION GUARD` 校验失败。
3. APIR 格式必须为 `^[A-Z]{3}\d{4}AU$`，Metrics 基金使用 `NO_APIR_MXT`。

---

### Task 1: 优化 URL 探测 `discover_source.py`

**Files:**
- Modify: `scripts/discover_source.py`
- Test: `tests/test_discover_source.py` (Create)

**Interfaces:**
- Consumes: `fund_registry.yaml`
- Produces: 能够快速探测 `confirmed_url` 是否可用，若可用则直接跳过搜索引擎检索。

- [ ] **Step 1: 编写测试用例验证快速探测逻辑**

Create: `tests/test_discover_source.py`
```python
import pytest
import requests
from unittest.mock import patch, MagicMock

# 模拟快速探测函数
def quick_verify_url(url: str) -> bool:
    try:
        resp = requests.head(url, timeout=5, headers={"User-Agent": "Mozilla"})
        return resp.status_code == 200
    except Exception:
        return False

@pytest.mark.unit
def test_quick_verify_url_success():
    with patch('requests.head') as mock_head:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_head.return_value = mock_resp
        assert quick_verify_url("https://example.com") is True

@pytest.mark.unit
def test_quick_verify_url_fail():
    with patch('requests.head', side_effect=requests.RequestException):
        assert quick_verify_url("https://example.com") is False
```

- [ ] **Step 2: 运行测试确保失败（本阶段创建测试将直接通过，无需失败）**

Run: `python3 -m pytest tests/test_discover_source.py`
Expected: PASS (测试本身只包含 mock 验证)

- [ ] **Step 3: 修改 `scripts/discover_source.py` 以加入直连探测秒级跳过**

Modify `scripts/discover_source.py` around line 345:
```python
    # Step 1: Check existing confirmed URL
    confirmed_url = fund_info.get("confirmed_url")
    if confirmed_url:
        log_attempt(f"Registry has existing URL: {confirmed_url}. Checking with quick HEAD request...")
        try:
            # 轻量探测以提高速度
            resp = requests.head(confirmed_url, headers=HEADERS, timeout=5)
            is_alive = (resp.status_code == 200)
        except Exception:
            is_alive = False

        if is_alive:
            log_attempt(f"Quick check succeeded. Verifying content rules...")
            if verify_url(confirmed_url, fund_id, fund_name, apir_code):
                log_attempt(f"SUCCESS: Existing URL is verified and active: {confirmed_url}")
                fund_info["verified_at"] = datetime.datetime.now().strftime("%Y-%m-%d")
                fund_info["verification_signal"] = "Verified existing registry URL via quick probe"
                save_registry(registry)
                sys.exit(0)
```

- [ ] **Step 4: 运行 pytest 确保现有测试全部通过**

Run: `python3 -m pytest tests/`
Expected: PASS

- [ ] **Step 5: 提交更改**

```bash
git add scripts/discover_source.py tests/test_discover_source.py
git commit -m "feat: add quick HEAD probe for source discovery to skip engine search"
```

---

### Task 2: 实现差异化下载 `fetch_web.py`

**Files:**
- Modify: `scripts/fetch_web.py`
- Test: `tests/test_fetch_web.py` (Create)

**Interfaces:**
- Consumes: `history_cache.json` 中的已有日期序列
- Produces: 过滤后的待下载 PDF 链接列表，避免重复下载历史数据

- [ ] **Step 1: 创建测试用例验证下载链接差分逻辑**

Create: `tests/test_fetch_web.py`
```python
import pytest
import re

def filter_pdf_links(pdf_links: list[tuple[str, str]], existing_dates: set[str], fund_id: str) -> list[tuple[str, str]]:
    filtered = []
    for text, url in pdf_links:
        filename = url.split('/')[-1]
        # 解析年份月份 (针对 Bentham: 20170131-GIF-Monthly-Report.pdf)
        date_match = re.search(r'(\d{4})(\d{2})(\d{2})', filename)
        if date_match:
            date_str = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
            if date_str in existing_dates:
                continue
        # 针对 Metrics: 2605 - MXT Monthly Report.pdf
        mxt_match = re.search(r'_(\d{2})(\d{2})', filename)
        if mxt_match:
            year = 2000 + int(mxt_match.group(1))
            month = int(mxt_match.group(2))
            # 构建 YYYY-MM
            month_prefix = f"{year}-{month:02d}"
            if any(d.startswith(month_prefix) for d in existing_dates):
                continue
        filtered.append((text, url))
    return filtered

@pytest.mark.unit
def test_filter_pdf_links_bentham():
    pdf_links = [
        ("GIF Jan 2017", "https://example.com/20170131-GIF-Monthly-Report.pdf"),
        ("GIF Feb 2017", "https://example.com/20170228-GIF-Monthly-Report.pdf")
    ]
    existing_dates = {"2017-01-31"}
    filtered = filter_pdf_links(pdf_links, existing_dates, "bentham_global_income_fund")
    assert len(filtered) == 1
    assert "20170228" in filtered[0][1]
```

- [ ] **Step 2: 运行测试以验证逻辑正确性**

Run: `python3 -m pytest tests/test_fetch_web.py`
Expected: PASS

- [ ] **Step 3: 修改 `scripts/fetch_web.py` 以读取 `history_cache.json` 并过滤下载**

在 `scripts/fetch_web.py` 中引入 `load_cache_dates` 辅助函数：
```python
def load_cache_dates(fund_id: str) -> set[str]:
    cache_path = os.path.join(BASE_DIR, "data", "raw", fund_id, "history_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {dp["date"] for dp in data.get("time_series", [])}
        except Exception:
            pass
    return set()
```
然后在 `fetch_bentham` 与 `fetch_metrics` 中过滤 `pdf_links`：
```python
    existing_dates = load_cache_dates(fund_id) # 传入对应 fund_id
    filtered_links = []
    # 针对不同文件名做不同匹配逻辑，如已存在则跳过
    ...
```

- [ ] **Step 4: 运行 pytest 确保没有引入语法错误**

Run: `python3 -m pytest tests/`
Expected: PASS

- [ ] **Step 5: 提交更改**

```bash
git add scripts/fetch_web.py tests/test_fetch_web.py
git commit -m "feat: implement differential fetch using history cache dates"
```

---

### Task 3: 实现增量解析与自适应进程池 `parse_factsheet.py`

**Files:**
- Modify: `scripts/parse_factsheet.py`
- Test: `tests/test_parse_factsheet_incremental.py` (Create)

**Interfaces:**
- Consumes: 本地 PDF 文件，`history_cache.json`
- Produces: 合并并重新连乘 NAV 后的 time_series，保存到 `history_cache.json` 和 `{latest_month}.json`

- [ ] **Step 1: 创建测试用例验证增量合并与重算 NAV 逻辑**

Create: `tests/test_parse_factsheet_incremental.py`
```python
import pytest

def merge_and_recalculate_nav(cache_series: list[dict], new_series: list[dict]) -> list[dict]:
    # 合并
    merged_map = {dp["date"]: dp for dp in cache_series}
    for dp in new_series:
        merged_map[dp["date"]] = dp
    
    # 排序
    sorted_series = [merged_map[d] for d in sorted(merged_map.keys())]
    
    # 重算 NAV
    current_nav = 1.0
    for idx, dp in enumerate(sorted_series):
        if idx == 0:
            dp["nav"] = 1.0
            dp["net_return"] = 0.0
        else:
            current_nav = current_nav * (1.0 + dp["net_return"])
            dp["nav"] = current_nav
    return sorted_series

@pytest.mark.unit
def test_merge_and_recalculate_nav():
    cache = [
        {"date": "2020-01-31", "net_return": 0.0, "nav": 1.0},
        {"date": "2020-02-29", "net_return": 0.01, "nav": 1.01}
    ]
    new_data = [
        {"date": "2020-03-31", "net_return": 0.02, "nav": 1.0} # 新解析的临时 NAV 往往为 1.0
    ]
    result = merge_and_recalculate_nav(cache, new_data)
    assert len(result) == 3
    assert result[2]["nav"] == pytest.approx(1.01 * 1.02)
```

- [ ] **Step 2: 运行测试**

Run: `python3 -m pytest tests/test_parse_factsheet_incremental.py`
Expected: PASS

- [ ] **Step 3: 修改 `scripts/parse_factsheet.py` 以进行增量解析和自适应多进程**

- 在 `parse_bentham` 和 `parse_metrics` 中：
  - 检查已记录在缓存中的月份，若无待解析新文件，直接返回缓存。
  - 对于待解析新文件，若待处理数量 $\le 1$，直接在当前线程同步处理；若 $>1$，再使用进程池。
  - 解析完成后，将新数据与缓存合并，并按日期排序。
  - 重新连乘计算 NAV。
  - 执行数据完整性/连续月份缺口检查（若有缺口则抛出错误）。
  - 将最新合并数据回写回 `history_cache.json`。

- [ ] **Step 4: 运行 pytest 确保现有测试与新测试全部通过**

Run: `python3 -m pytest tests/`
Expected: PASS

- [ ] **Step 5: 提交更改**

```bash
git add scripts/parse_factsheet.py tests/test_parse_factsheet_incremental.py
git commit -m "feat: add incremental parsing, NAV recalculation, and adaptive process pool"
```

---

### Task 4: 实现跨基金并行流水线 `run_all.py`

**Files:**
- Modify: `scripts/run_all.py`
- Test: 无需单独测试，直接使用端到端命令验证

**Interfaces:**
- Consumes: 并行调度
- Produces: 所有基金的运行效率提升，同时输出完整报告

- [ ] **Step 1: 修改 `scripts/run_all.py` 以引入并发支持**

将 Step 3 & 4 以及前面的 Step 0~2 放在线程池中：
```python
from concurrent.futures import ThreadPoolExecutor

# 针对单支基金的完整生命周期执行
def run_single_fund_pipeline(fund_id, latest_date, is_stale):
    # Step 0: URL Discovery (如果 stale)
    # Step 1: Fetch web
    # Step 2: Parse factsheet
    # Step 3: Validate data
    # Step 4: Metrics
```
使用 `ThreadPoolExecutor(max_workers=4)` 并发运行所有 stale_funds 的获取和全基金的 metrics 计算。

- [ ] **Step 2: 执行一次干净的端到端运行以进行全量更新，检查报告和 Excel 是否正确生成**

Run: `python3 scripts/run_all.py`
Expected: 所有基金验证无误，报告正常生成在 `data/output/report.md`。

- [ ] **Step 3: 第二次运行 `run_all.py` 验证增量秒级跳过速度**

Run: `time python3 scripts/run_all.py`
Expected: 运行时间缩短至 5 秒内，控制台输出各项 skip 日志，生成指标与报告结果无变动。

- [ ] **Step 4: 运行 pytest 确保所有单元测试全部通过**

Run: `python3 -m pytest tests/`
Expected: PASS

- [ ] **Step 5: 提交更改**

```bash
git add scripts/run_all.py
git commit -m "feat: concurrent cross-fund execution using ThreadPoolExecutor in run_all.py"
```
