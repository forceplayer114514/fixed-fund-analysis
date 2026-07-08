# Task 2: 实现差异化下载 `fetch_web.py`


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