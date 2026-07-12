# PDF 提取流程优化计划

## 背景与目标
单基金（Stake Accumulate）入库耗时 27'35"。根因：
1. **inspect 遮蔽试错** 6'（/tmp/inspect.py 遮蔽标准库，环境 bug）
2. **口径识别来回** 8'（performance 表 1mo vs Commentary 正文，逻辑未固化）
3. **大量临时脚本** 每步 Write+Bash+修复

目标：**27' -> 3-5'**。原则（采纳 Sonnet 方案）：当前 80% 痛点是代码 bug 非工具问题，先修 bug + 固化逻辑 + 硬 gate 校验，LLM 仅作可选兜底，不急着换 Docling/MinerU。

---

## 阶段0：环境清理（5 分钟）
**操作**：删除遗留垃圾脚本
- `rm /tmp/inspect.py /tmp/fix_omega*.py /tmp/fix_td.py /tmp/stake_*.py`
**验收**：在 /tmp 下 `python3 -c "import inspect; print(inspect.signature)"` 正常返回（不再 AttributeError）

---

## 阶段1：修 bug + 固化 Commentary 优先（`lib/extract.py`，1 小时）

### 新增函数

```python
def extract_commentary_return(text: str) -> Optional[float]:
    """从 PDF 文本提取 Commentary 当月收益（after fees，返回小数）。
    正则 r'returned\s+([+-]?\d+\.\d+)%'，捕获符号。
    正数省略正号正常（"0.53%"），负号必须捕获（"-0.26%"）。
    取第一个匹配（当月声明）。无匹配返回 None。
    """

def extract_perf_rolling(text: str) -> dict:
    """提取 performance 表 Class A 滚动收益。
    显式处理 "-" 空列：先解析表头列标题（1 month/3 months/6 months/
    12 months/Since inception），数据值按列标题对应，cell 数与列数
    不匹配标记 parse_error=True。
    返回 {'1mo':float|None,'3mo':..,'6mo':..,'12mo':..,'inception':..,
           'parse_error':bool}
    用列标题与值显式对应，不靠位置（解决这次错位根因）。
    """

def extract_pdf_links_from_archive(markdown: str) -> list[tuple[str, str]]:
    """从归档页 markdown 提取 [(YYYY-MM, pdf_url), ...]。
    识别月份名+年 + .pdf 链接。
    """

def extract_pdf_one(pdf_path: str, max_pages: Optional[int] = None
                    ) -> tuple[Optional[float], dict]:
    """单 PDF 提取纯函数（顶层，可被 ThreadPool/ProcessPool 调用）。
    parse_pdf_text -> extract_commentary_return + extract_perf_rolling。
    返回 (commentary_return, rolling)。失败返回 (None, {'parse_error':True})。
    顶层纯函数设计：未来可一行切 ProcessPoolExecutor 应对大批量。
    """

def download_and_extract_parallel(links: list[tuple[str,str]],
                                  dest_dir: str,
                                  max_workers: Optional[int] = None
                                  ) -> list[tuple[str, Optional[float], dict]]:
    """ThreadPool pipeline：每 worker 下载一个 PDF 后立即提取
    （IO 下载与 CPU 提取重叠，无 barrier，比"下载并发->提取并发"两阶段更快）。
    max_workers 默认 min(16, os.cpu_count())（M5 满核 10-16）。
    返回 [(ym, commentary_return, rolling), ...]，按 ym 排序。
    失败隔离：单 PDF 下载/提取失败 -> (ym, None, {'parse_error':True})，不中断其他。
    复用 download_file + extract_pdf_one。
    线程安全：fitz C 层释放 GIL，ThreadPool 多核有效；每 worker 独立 fitz.open
    不共享 Document。不用 ProcessPool（macOS spawn 重新 import fitz 开销 > 15 个小 PDF 收益）。
    """

def verify_monthly_vs_rolling(monthly: list[tuple[str, float]],
                              rolling: dict) -> dict:
    """复利交叉验证。用 monthly 复利算截至各月的 3mo/6mo/12mo，
    对比 rolling 同期值。返回 {'3mo':{'expected','actual','error'},...,
    'pass':bool}。阈值：绝对误差 < 0.5%（容忍 PDF 四舍五入）。
    rolling 缺列或 monthly 不足 N 个月时跳过该窗口。
    """

def gate_check(records: list[tuple[str, float]],
               rolling_per_month: dict) -> tuple[bool, list[str]]:
    """入库前硬 gate。组合校验：
    - check_gaps（缺口零容忍）
    - ANTI-FABRICATION（连续相同精确浮点数）
    - verify_monthly_vs_rolling（复利验证，至少一个窗口通过）
    - 字段类型校验（net_return 在合理月度范围，如 |r|<0.5 即 50%）
    返回 (pass, errors)。pass=False 时 errors 列出具体问题。
    """
```

### 修改
- 所有百分比正则统一用 `[+-]?\d+\.\d+%`（捕获符号）

### 测试（`tests/test_extract.py` 新增）
- `extract_commentary_return`：正数/负数/无符号/无匹配 4 用例
- `extract_perf_rolling`：含 "-" 空列的 performance 表（用 Stake Nov 2025 真实文本，5 列 4 值）
- `verify_monthly_vs_rolling`：Stake May 2026 真实数据（应通过）+ 构造错误序列（应失败）
- `gate_check`：完整通过流程 + 缺口失败 + 复利失败 3 用例
- `extract_pdf_one`：Stake 真实 PDF fixture，验证返回 (commentary_return, rolling)
- `download_and_extract_parallel`：mock download_file，验证 pipeline 并发 + 失败隔离 + 结果按 ym 排序

### 验收
- `cd skills && python3 -m pytest tests/test_extract.py -v` 全过
- 用 Stake 15 个 PDF 跑 `extract_commentary_return`，结果与已入库 Commentary 值一致（-0.0051...0.0105）

---

## 阶段2：主入口 `lib/ingest.py`（全自动流水线，半天）

### 设计
agent 只做 MCP 抓取（JS 渲染页必须 MCP），其余程序化：
```
agent: stealthy_fetch 归档页 -> 存 /tmp/<fund>_archive.md
agent: python3 -m lib.ingest add --fund-id X --name Y --archive-html <path> --verified-at YYYY-MM-DD
ingest.py 自动: 解析归档页 -> 并发下载 PDF -> 提取 Commentary+滚动收益 -> gate_check -> 入库/报错
```

### 函数
```python
# lib/ingest.py
def add_fund(fund_id: str, name: str, archive_html_path: str,
             apir: Optional[str] = None, url_type: str = 'archive_page',
             fetch_method: str = 'pdf', verified_at: Optional[str] = None,
             confirmed_url: Optional[str] = None) -> dict:
    """全自动流水线。返回 {'months':n,'start':,'end':,'nav_end':,
    'gaps':[],'gate_pass':bool,'errors':[]}。
    流程:
    1. 读 archive_html_path
    2. extract_pdf_links_from_archive -> [(ym,url)]
    3. download_and_extract_parallel -> [(ym, commentary_return, rolling)]
       (下载+提取 pipeline 并发，M5 满核)
       -> records=[(date,net_return)], rolling_per_month={ym:rolling}
    4. gate_check(records, rolling_per_month) -> (pass, errors)
    5. pass: create_fund + 逐月 upsert_monthly_return（批量事务）; fail: 打印 errors 退出
    6. 打印报告（月数/起止/NAV/可追溯/36月不足提示）
    """

# CLI 入口
if __name__ == '__main__':
    # argparse: add 子命令 + 参数
```

### 测试（`tests/test_ingest.py` 新增）
- 用 Stake 归档页 markdown fixture（已抓取的真实内容）跑 `add_fund`，验证入库 15 个月无缺口、NAV 复利正确
- gate_check 失败时（构造缺数据）不入库、返回 errors

### 验收
- `python3 -m lib.ingest add --fund-id stake_accumulate --name "Stake Accumulate Fund" --archive-html /tmp/stake_archive.md --verified-at 2026-07-12` 一次跑完
- 3-5 分钟内完成（瓶颈为 PDF 下载，并发）
- 入库结果与已手动入库的 15 个月一致
- 先清空 stake_accumulate 旧数据再测（或用测试 fund_id）

---

## 阶段3：固化 skill 文档（持续）

### `add_fixed_fund.md` 更新
- 第1步：URL 探测用 `mcp__search__search`（已改）
- 第3步：JS 渲染归档页用 `stealthy_fetch(network_idle=true)`，存 /tmp/<fund>_archive.md
- 第4-6步：合并为"调 `python3 -m lib.ingest add`"，不再让 agent 手写提取脚本
- **新增「硬约束」节**：
  1. Commentary 正文优先于 performance 表 1mo（复利验证已证明，performance 表 1mo 口径错误）
  2. 负号强制捕获 `[+-]?\d+\.\d+%`
  3. 入库前必须过 `gate_check`（复利验证+缺口+ANTI-FABRICATION），不通过报错停
  4. PDF 下载并发（ThreadPool，max_workers=8）
  5. inspect 避坑：脚本在 skills 目录跑，不在 /tmp（或清理 sys.path）
  6. 序列起点=第一份真实研报日期，不反推捏造

### `update_fixed_fund.md` 同步
- 同样的硬约束
- 增量更新流程改用 ingest.py（或加 update 子命令）
- 提及 free-search-mcp 替代 WebSearch

---

## 阶段4：LLM 兜底（可选，本次不实现代码）
- 仅文档说明：gate_check 失败时，agent 可用 LLM 兜底
  - 只喂局部文本片段（performance 表区块或 Commentary 段）
  - JSON Schema 约束输出，`value` 字段允许 null（避免必填诱导幻觉）
  - 结果照样过 gate_check，不绕过
  - 本机 haiku=DeepSeek-V4-Flash，先测再定（用户已选）
- 本次不写 LLM 代码，留接口

---

## 并发设计（全流程清单）
| # | 阶段 | 并发 | 方式 | 理由 |
|---|---|---|---|---|
| 1 | 读归档页 html | ✗ | - | 本地文件瞬时 |
| 2 | 解析归档页提 PDF 链接 | ✗ | - | 单页正则瞬时 |
| 3 | PDF 下载 | ✓ | ThreadPool | IO 密集（网络等待释放 GIL） |
| 4 | PDF 文本提取 (fitz) | ✓ | ThreadPool | CPU，fitz C 层释放 GIL，多核有效 |
| 5 | Commentary+rolling 正则 | (并入4) | - | 毫秒级，随 4 完成 |
| 6 | 复利验证 | ✗ | - | 15月×3窗口微秒级，调度开销>收益 |
| 7 | gate_check | ✗ | - | 毫秒级本地校验 |
| 8 | SQLite 入库 | ✗ | executemany 批量事务 | SQLite 写锁，批量比并发快 |
| 9 | LLM 兜底（未来） | ✓ | ThreadPool | API 调用 IO 密集 |
| 10 | 多基金（未来） | ✓ | 每基金一 ingest 任务 | 独立 |

**核心：3+4 合并为下载+提取 pipeline**，单一 ThreadPoolExecutor，`max_workers=min(16, os.cpu_count())`（M5 满核）。每 worker = download_file + extract_pdf_one，IO/CPU 重叠无 barrier，as_completed 收集 + 失败隔离。

**ThreadPool 而非 ProcessPool**：fitz 释放 GIL 已多核有效；macOS ProcessPool spawn 重新 import fitz（~200ms/进程）对 15 个小 PDF 开销 > 收益；无序列化。`extract_pdf_one` 顶层纯函数预留 ProcessPool 切换口（未来大批量 100+ PDF 时）。

**单 PDF 不拆页**：月报 2-5 页，一次 get_text 全文最快，拆页调度开销 > 收益。留 page_range 参数口应对未来大 PDF。

---

## 数据完整性保证（gate_check 硬 gate）
入库前强制过，不通过报错停（硬停止，不加 pending_review 表，符合现有"缺口零容忍即停止"模式）：
1. **缺口检查**：check_gaps，缺失月份列出
2. **ANTI-FABRICATION**：连续相同精确浮点数
3. **复利交叉验证**：monthly 复利 vs performance 表滚动收益，至少一个窗口误差<0.5%
4. **字段类型校验**：net_return 月度范围合理（|r|<0.5）
5. **可追溯**：confirmed_url + verified_at

---

## 执行顺序与验收
0 -> 1 -> 2 -> 3（4 可选，本次仅文档）

每阶段验收后再进下一阶段。阶段1单元测试全过后才跑阶段2端到端。

## 预期效果
- 单基金入库：27' -> 3-5'
- 瓶颈从"agent 试错回合"变成"网络下载"（可并发压缩）
- 数据完整性：gate_check 硬 gate 兜底，LLM 幻觉/正则错误都挡在入库前
- 多基金泛化：Stake 模板搞定后，Bentham 等新基金加入时若正则搞不定，再上 Docling 横向测试或 LLM 兜底
