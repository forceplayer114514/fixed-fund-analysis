# Task 4: 实现跨基金并行流水线 `run_all.py`


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