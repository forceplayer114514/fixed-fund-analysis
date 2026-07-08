# Task 3: 实现增量解析与自适应进程池 `parse_factsheet.py`


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