# 设计规约 — 固收对比计算流水线性能优化与增量缓存

此设计规约旨在解决当前流水线（Pipeline）全量处理历史 PDF 文件导致的耗时长、网络和 CPU 开销冗余的问题。遵循第一性原理，我们将引入增量更新机制与并发流，使日常运行时间从几分钟缩短至几秒。

## 1. 核心设计原则与架构优化

### 1.1 增量数据缓存 (Incremental Data Caching)
在每个基金的数据目录中引入 `history_cache.json` 充当历史数据“真理之源”。
- **文件路径**：`data/raw/{fund_id}/history_cache.json`
- **格式规范**：与解析器输出的 structured json 保持一致，包含 `fund_id`、`fund_name`、`apir_code`、`last_updated` 以及排好序的 `time_series` 列表。
- **作用**：记录以前各月份已被验证无缺口的收益率，避免重复下载和解析。

### 1.2 差异化下载 (Fetch Diffing)
优化 `scripts/fetch_web.py`：
- 读取本地 `history_cache.json` 中已存的日期集合。
- 扫描网页上暴露的 PDF 链接列表，解析链接中代表年份月份的标识（如 `YYMM` 或 `YYYYMMDD`）。
- **过滤逻辑**：仅下载本地缓存中未记录的月份 PDF，已存在的直接忽略。

### 1.3 增量解析与自适应进程池 (Incremental Parsing)
优化 `scripts/parse_factsheet.py`：
- 加载 `history_cache.json`，只对新下载的 PDF 进行 PyMuPDF 解析。
- 将新解析得到的收益率条目与缓存合并，并执行 gap（缺失月份）校验。
- 基于合并后的完整序列，重新连乘计算 NAV 曲线以保证一致性。
- **进程池开销优化**：当新解析的 PDF 数量 $\le 1$ 时，直接在主进程中同步执行，跳过 `ProcessPoolExecutor` 以节约进程创建开销。

### 1.4 URL 验证秒级跳过 (Skip Verified URLs)
优化 `scripts/discover_source.py`：
- 启动时优先尝试对 `confirmed_url` 进行轻量级 HEAD/GET 探测。
- 如果请求返回 200 且关键特征匹配，直接跳过后续 DDG / Yahoo 搜索。

### 1.5 跨基金并行流 (Cross-Fund Pipeline Concurrency)
优化 `scripts/run_all.py`：
- 利用 `ThreadPoolExecutor` 并行启动多支基金的流水线计算任务（Step 0 到 Step 4）。
- 主线程收集并行执行状态，所有基金执行成功后，再串行运行 Step 5（生成最终对比报告）。

---

## 2. 详细实现路线

### Phase 1: 基础工具类与数据校验优化
- 改进 `discover_source.py` 的首选直连校验。
- 在 `validate_data.py` 和 `metrics.py` 中适配增量数据的读写。

### Phase 2: 差异下载与增量解析
- 修改 `fetch_web.py`，加入读取 `history_cache.json` 差分下载 PDF 逻辑。
- 修改 `parse_factsheet.py`，实现增量 PyMuPDF 解析、自适应进程池和缓存回写合并。

### Phase 3: 多基金并行与端到端回归
- 改造 `run_all.py` 支持 `ThreadPoolExecutor` 并行子进程。
- 补充对应的 pytest 单元测试，验证增量合并与缺口检验的鲁棒性。

---

## 3. 测试与验证计划
1. **单元测试**：针对缓存合并、差异计算、自适应进程池逻辑，在 `tests/` 中编写对应的单元测试。
2. **端到端测试**：
   - 第一次运行：清理本地 raw 目录后运行 `python3 scripts/run_all.py`，验证全量下载并成功生成 `history_cache.json`。
   - 第二次运行：直接再次运行 `python3 scripts/run_all.py`，验证是否瞬间跳过下载解析，在 3 秒内完成，且最终指标和报告内容与全量时完全一致。
