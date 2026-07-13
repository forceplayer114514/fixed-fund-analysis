# 字段错位静默入库防护设计

**日期**: 2026-07-13
**状态**: 待实现
**触发事件**: Coolabah FRHY Institutional 份额类被误存为 AusBond Credit FRN Index 基准数据

## 背景

2026-07-13 抓取 Coolabah Floating-Rate High Yield Fund 时，探测子 agent 越权直接调 `lib.db` 入库（绕过 `lib.ingest` 的 `gate_check`），且自写 Plotly hovertext 提取代码按 trace 顺序猜"前 N=基金 后 N=基准"，未按 `name` 字段过滤排除 benchmark。结果 Institutional 份额类（ETL6855AU）的 `monthly_returns` 被写入 AusBond 基准序列（NAV 1.0026->1.1919, inception pa 5.07%），而非真 Institutional 序列（应 1.0098->1.3523, pa 9.006%）。

用户从 webapp 超额年化异常发现（Institutional 1.01% vs Assisted 4.51%，机构费率低但超额反低 3.5%）。排查确认：清洗数据环节错（skills 侧），非后端公式错。

## 根因

1. **流程给幻觉空间**：探测子 agent 拥有 DB 写权限，能绕过 `gate_check` 直接调 `lib.db.upsert_monthly_return`。skill 文档虽写"ingest.py 由主对话跑"，但属提示词层约束，非结构约束。
2. **提取逻辑在 lib 外**：Coolabah Plotly HTML 报告的 NAV 序列提取由 agent 临时写代码，未纳入 `lib/extract.py`。agent 按 trace 顺序猜基金类 vs benchmark，无 `name` 字段过滤。
3. **现有 gate_check 无跨序列校验**：`gate_check`/`gate_check_table` 只校验单序列内部（缺口/ANTI-FABRICATION/字段类型/复利），AusBond 基准序列单看完全自洽，gate 全通过。

**非根因**：`lib/extract.py` 的 `extract_perf_rolling`/`parse_html_monthly_table` 已用表头文字定位（非列位置索引），用户初始假设（位置索引错位）不成立。但表头定位缺回归测试，须巩固防退化。

## 设计原则

- **prevention 优先 > detection 兜底**：重点在结构层让错不可能发生，而非靠校验拦截。detection 不过繁杂，精选 high-value 校验。
- **流程优化非提示词堆砌**：CLAUDE.md 堆"严禁/禁止"作用有限，可辅助。根本是结构上不给幻觉空间（read-only 探测 agent + 单一入库入口 + 提取函数化）。
- **通用框架非特例分支**：禁止 `if fund_id == "coolabah..."`，4 条校验适用于所有基金。
- **写库前自证一致性**：校验失败必 block + 保留原文，禁止静默丢弃/旧值兜底。

## 架构

```
lib/extract.py      + parse_plotly_nav_series()        # 新增,Plotly NAV 提取
lib/consistency.py  # 新增,self-consistency 4 条校验 + 跨序列查 DB
lib/ingest.py       + add_fund_from_plotly_html()      # 新增入口,Coolabah 模式
                    gate_check/gate_check_table 末尾调 consistency
lib/audit.py        # 新增,历史数据批量扫描
.claude/agents/     探测 agent 改 read-only
tests/              夹具 A/B/C/D + 单元测试
```

## prevention 层

### 1. Plotly NAV 提取函数化

`lib/extract.py::parse_plotly_nav_series`:

```python
def parse_plotly_nav_series(
    html: str,
    fund_name_pattern: str,
) -> list[tuple[str, float]]:
    """从 pandoc HTML 报告 Plotly hovertext 提取基金类 NAV 序列。

    按 fund_name_pattern 过滤 trace,name 不匹配的丢弃。
    排除 name 含 Benchmark/Index/AusBond 的 trace(结构上 benchmark 不可能混入)。
    多 trace 匹配 pattern 报错(防歧义)。返回 [(date, nav), ...] 升序。
    """
```

- `fund_name_pattern` 必传，强制调用方明确要哪个份额类
- benchmark trace（name 含 Benchmark/Index/AusBond）自动丢弃，非靠调用方过滤
- 多 trace 匹配同一 pattern -> raise，防 agent 按 trace 顺序猜
- **零 trace 匹配 -> raise**（非静默返回空列表）。pattern 打错（大小写/空格）时必须报错，防下游把空列表当"无数据"跳过
- 本次 bug 在此结构上不可能：AusBond trace name 不匹配 pattern，自动丢弃

### 2. 表头定位巩固

`extract_perf_rolling`/`parse_html_monthly_table` 已用表头文字定位。加回归测试夹具 A/B/C 锁版式，防退化。表头匹配失败降级位置索引时显式警告"表头匹配失败，已降级为位置索引，结果需人工复核"。

### 3. 越权防护（流程结构 + 运行时隔离）

**问题**：去 Write/Edit 只堵"写文件再执行"路径，堵不住 agent bash 内联 `python3 -c "from lib.db import upsert_monthly_return; ..."`。真结构防护须 harness sandbox（agent bash 环境隔离），当前 Claude Code harness 不支持。现实方案分三层：

**层 1：运行时写凭证隔离（lib.db 层）**
- `lib.db.upsert_monthly_return`/`create_fund` 等写操作检查 `os.environ.get('FUND_DB_WRITE_TOKEN')`，未设 raise `PermissionError`
- `lib.ingest` CLI `_cli()` main 入口设 token（从 CLI `--write-token` 参数或主对话 shell 环境注入）
- 探测 agent bash 子进程**不继承 token**（主对话跑 ingest 时 token 仅在该进程环境，agent bash 是独立子进程不注入）
- agent bash `python3 -c "from lib.db import upsert_monthly_return; ..."` 因无 token 失败

**局限**：agent 若 bash `FUND_DB_WRITE_TOKEN=x python3 -c "..."` 且知道 token 值可绕过。token 须不落盘/不在源码硬编码（从主对话 shell 环境注入，agent 读不到）。当前 harness agent bash 与主对话同用户环境，token 完全隔离须 harness sandbox（二期/换 harness 支持）。

**层 2：read-only 探测 agent（堵写文件路径）**
- `add_fixed_fund` skill 探测步骤改用 `cavecrew-investigator` agent（Read/Grep/Glob/Bash，无 Write/Edit）
- 堵住 agent 写 .py 脚本调 lib.db 的路径（须 Write 权限创建文件）
- 与层 1 叠加：写文件路径被堵 + 内联路径无 token 失败

**层 3：detection 兜底（越权写入后的拦截）**
- 即便层 1/2 被绕过（agent 拿到 token + bash 内联），detection 层（A 组文档内自证 + B 组跨序列 + 复利验证）作为兜底
- `audit_all_funds` 每次入库后自动跑（见下），扫描越权写入的静默错位
- 本次 bug：Agent A 越权写入后，若 audit 自动跑，复利验证（AusBond pa 5.14% vs PDF 9.05%）+ 6 条（份额类差值）会立即拦截

**诚实声明**：层 1/2 是"提高门槛 + 提高被发现概率"非"绝对防住"。绝对防住须 harness sandbox（agent bash 与主对话 DB 写权限完全隔离），属 harness 层能力，超出 skills 代码层。当前方案把"越权写库"从"提示词约束"提升到"运行时 token + read-only + detection 兜底"三重防护，符合"结构优化非提示词堆砌"原则，但未达 harness sandbox 的绝对隔离。

## detection 层

### `lib/consistency.py` 接口

```python
def consistency_check(
    fund_id: str,
    records: list[tuple[str, float]],
    conn: sqlite3.Connection,
    *,
    gross_records: Optional[list[tuple[str, float]]] = None,
    benchmark_records: Optional[list[tuple[str, float]]] = None,
    excess_records: Optional[list[tuple[str, float]]] = None,
    growth_records: Optional[list[tuple[str, float]]] = None,
    income_records: Optional[list[tuple[str, float]]] = None,
    shareclass_prefix: Optional[str] = None,  # 如 "coolabah_frhy"
    corr_threshold: float = 0.98,
    fee_diff_monthly_max: float = 0.001,  # 0.1%/月默认(management fee 差/12+跟踪误差)
) -> tuple[bool, list[str], list[str]]:
    """写库前 self-consistency 强制关卡。

    7 条校验分两级:
    - block 级(失败 -> gate_pass=False,不入库): 1/2/3/4/5/6
    - warn 级(失败 -> errors_warn,需人工确认才放行): 7
    返回 (pass, errors_block, errors_warn)。
    """
```

### 7 条校验（按优先级 + 数据依赖分级）

**A 组：文档内自证（无 DB 依赖，第一次入库 DB 空也生效）-- 优先级最高**

| # | 校验 | 数据源 | 容差 | 失败行为 |
|---|------|--------|------|---------|
| 1 | Net Excess ≈ Net Return - Benchmark Return | `net_records` + `benchmark_records` + `excess_records`（解析时同提） | ±0.05% | block |
| 2 | Gross Excess ≈ Gross Return - Benchmark Return | `gross_records` + `benchmark_records` + `excess_records` | ±0.05% | block |
| 3 | Net Return < Gross Return | `gross_records` + `net_records` | 浮点 0.001，除非 fee waiver 标记 | block |
| 4 | Total Return = Growth Return + Income Return | `growth_records` + `income_records`（三者都存在时） | ±0.05% | block |

A 组字段不全（如 Plotly NAV 源无 gross/excess/growth/income）时该条跳过，**不因此 fail**。但复利验证（NAV 复利 vs PDF rolling，见下"复利验证强化"）作为 Plotly 源的文档内自证替代。

**B 组：跨序列校验（依赖 DB 兄弟数据）-- 优先级次**

| # | 校验 | 数据源 | 容差 | 失败行为 |
|---|------|--------|------|---------|
| 5 | 同 family 份额类月度收益符号一致 | DB 查 `fund_id LIKE shareclass_prefix%` 且 != 当前 | 0 值除外 | block |
| 6 | 份额类月度差值 < fee_diff_monthly_max | DB 查同 family 其他份额类同月收益 | 默认 0.1%/月 | block |
| 7 | 新序列 vs DB 所有其他基金序列相关系数 | DB 查所有 fund_id != 当前 + != 同 family | >0.98 嫌疑（须 >=24 月重叠） | **warn + 人工确认**（非 block） |

### 关键设计点

- **A 组优先级高于 B 组**：A 组文档内自证，第一次入库 DB 空也生效；B 组依赖 DB 兄弟，若该份额类先入库（DB 无兄弟）则 5/6 失效。本次 bug 侥幸因 Assisted 先入库，6 条生效；若 Institutional 先入库，须靠 A 组（复利验证 + 若 PDF 含 excess 字段则 1/2）拦截
- **复利验证强化（文档内自证，A 组替代）**：`add_fund_from_plotly_html` 须下 PDF 提 rolling（1mo/3mo/6mo/1yr/inception）+ NAV 复利交叉验证**全窗口**（非只 1mo）。本次 bug：AusBond NAV 复利 inception pa 5.14% vs Institutional PDF 9.05%，复利验证失败 block。此校验不依赖 DB 兄弟
- **7 条降 warn**：Coolabah 产品矩阵（FRHY/Short Term Income/LSCF）底层相似（短久期投资级浮动利率，同 RBA 现金利率因子），月度收益天然高相关，0.98 阈值真实假阳性。7 条是统计嫌疑非会计恒等式，与 3/5/6 确信度不同，降 warn + 人工确认放行
- **6 条 performance fee 局限**：默认 0.1%/月只含 management fee 差/12 + 跟踪误差。**当前假设基金无 performance fee 差异**（FRHY 两类均 None）。带 performance fee 的基金业绩好月份费后收益额外摊薄，月度差值可能自然超 0.1% 误伤。二期加 `funds.management_fee` + `funds.performance_fee` 字段动态调整容差；一期默认值 + 文档注明限制
- **5/6 条**按 `shareclass_prefix` 分组。无份额类的基金（`stake_accumulate`）prefix 传 None，5/6 跳过
- **gate_check/gate_check_table 末尾调 consistency_check**，与现有缺口/ANTI-FABRICATION/字段类型/复利校验串联

### 错误处理

- 校验失败 -> `gate_pass=False`，**不入库**，errors 列具体问题（哪条校验、哪月、对端 fund_id、实际值 vs 阈值）
- **保留原文**：解析时 `commentary_truth` 字段存原始 Commentary 文本（非数值），失败时附 PDF/HTML 路径供人工复核
- **禁止静默丢弃/旧值兜底**：失败必显式报错 block，不降级
- **禁止特例 if fund_id**：4 条校验通用

## PDD 测试（test-driven-development：red -> green）

### 顺序

1. **夹具 D 先写**（复现本次 bug，DB 有兄弟）-> 实现 `consistency_check` B 组 + 复利验证 -> D 通过
2. **夹具 E**（第一次入库 DB 空，无兄弟）-> 复利验证独立生效 -> E 通过（证明不依赖 DB 兄弟）
3. 夹具 A/B（Plotly 提取）-> 实现 `parse_plotly_nav_series`（含零匹配/多匹配 raise）-> A/B 通过
4. 夹具 C（列序打乱）-> 巩固表头定位 -> C 通过
5. A 组 1/2/4（PDF 表格源多字段夹具）-> 文档内自证 -> 通过
6. 历史扫描 `lib/audit.py` -> 跑存量 + 入库后自动触发

### 夹具

| 夹具 | 文件 | 断言 |
|------|------|------|
| A | `tests/fixtures/frhy_assisted.html`（真实 `-ai` 页片段） | `parse_plotly_nav_series(html, "Institutional")` 返 43 NAV 100->135.23；benchmark trace 丢弃；多 trace 匹配 raise；零匹配 raise |
| B | `tests/fixtures/frhy_institutional.html`（真实 `-i` 页片段） | 同 A 但 Assisted；交叉验证 1mo 0.52% net |
| C | `tests/fixtures/columns_shuffled.md`（合成 fundmonitors 表，benchmark 列与 net return 列调换） | `parse_html_monthly_table` 按表头文字定位，不受列序影响；位置索引兜底触发时显式警告 |
| D | `tests/fixtures/field_misaligned.json`（合成：benchmark 序列当 Institutional 注入 + 同 family Assisted 正确序列 + PDF rolling inception 9.05%） | `consistency_check` B 组 6 条（份额类差值 pa 3.7%>>0.1%×12）block + 复利验证（AusBond pa 5.14% vs PDF 9.05%）block + 7 条 warn 嫌疑；`gate_pass=False`；errors 含对端 fund_id + 实际值 |
| E | `tests/fixtures/first_shareclass.json`（合成：Institutional 先入库，DB 无兄弟，仅 PDF rolling） | A 组 1/2/4 字段不全跳过；**复利验证（NAV vs PDF rolling inception）block**（不依赖 DB 兄弟）；证明"第一次入库 DB 空"场景仍可拦 |

### 测试文件

- `tests/test_parse_plotly_nav.py` -- A/B + 多 trace raise + 零匹配 raise + benchmark 丢弃
- `tests/test_header_parsing.py` -- C + `extract_perf_rolling`/`parse_html_monthly_table` 列序无关
- `tests/test_consistency_check.py` -- D/E + 7 条各 positive/negative（A 组 1/2/3/4 文档内自证 + B 组 5/6 block + 7 warn）
- `tests/test_compound_validation.py` -- 复利验证全窗口（NAV 复利 vs PDF rolling 1mo/1yr/inception），含 AusBond-当-Institutional 拦截场景
- `tests/test_audit.py` -- 历史扫描 + 入库后自动触发
- `tests/test_db_write_token.py` -- `lib.db` 写操作无 token raise PermissionError

## 历史扫描

### `lib/audit.py`

```python
def audit_all_funds(conn) -> dict:
    """批量跑 consistency_check 扫所有已入库基金,找静默字段错位存量。

    跨份额类(5/6):按 fund_id 前缀分组(去末段)组内互校验。
    基准相关(7):所有基金两两相关 >0.98 报嫌疑对(warn)。
    复利验证(A 组):每只基金 NAV 复利 vs 其 PDF rolling(若有)。
    返回 {fund_id: {check: errors_block/errors_warn}, suspect_pairs: [...]}。
    """
```

- **运行频率**：`ingest` 入库成功后**自动触发** `audit_all_funds`（非一次性善后）。每次新基金入库后扫全量，防越权写入或 gate 漏拦的静默错位积累。`add_fund`/`add_fund_from_html_table`/`add_fund_from_plotly_html` 末尾调 audit，warn 级问题打印警告但不回滚已入库数据（人工确认），block 级问题理论上已被前置 gate 拦（audit 是双保险）
- 当前 DB 4 只基金：stake_accumulate / smarter_money_lscf / coolabah_frhy_assisted / coolabah_frhy_institutional（已修）
- 首次扫描确认无其他错位；coolabah_frhy 两类互校验通过（已修后 pa 差 0.23% < 阈值）
- 输出报告存 `docs/superpowers/audits/YYYY-MM-DD-consistency-audit.md`（每次 audit 覆盖或追加时间戳）

## 完成标准

- [ ] 夹具 D 复现本次 bug 场景，`consistency_check` B 组 6 条拦截 + 复利验证拦截（red->green）
- [ ] 夹具 A/B Plotly 提取正确，benchmark 丢弃，零匹配 raise，多匹配 raise
- [ ] 夹具 C 列序打乱不影响表头定位
- [ ] A 组 1/2/4 文档内自证（PDF 表格源含多字段时）+ 复利验证全窗口（Plotly 源 NAV vs PDF rolling 1mo/1yr/inception）
- [ ] `gate_check`/`gate_check_table` 末尾调 consistency（7 条，A 组 block + B 组 5/6 block + 7 warn），失败 block
- [ ] `lib.db` 写操作 token 软隔离（`FUND_DB_WRITE_TOKEN`），`lib.ingest` CLI main 注入
- [ ] `add_fixed_fund` skill 探测步骤改 read-only agent（cavecrew-investigator）
- [ ] `lib/audit.py` 每次入库后自动跑，扫存量无其他错位
- [ ] `add_fund_from_plotly_html` 新入口（Coolabah 模式），复利验证全窗口
- [ ] 无 `if fund_id == "coolabah..."` 特例分支

## 明确禁止事项

- 禁止只加 `if` 分支专门处理 "Coolabah FRHY Institutional" 这一个 fund_id，必须是通用的恒等式校验框架
- 禁止把 self-consistency check 做成"只在测试环境跑、生产环境跳过"，必须是写库路径上的强制关卡
- 禁止校验失败时静默丢弃或静默使用旧值兜底，必须显式报错并阻止该条记录写入，同时保留原始 PDF/HTML 文本方便人工复核
- 禁止靠 CLAUDE.md 堆"严禁/禁止"提示词作为主防护（作用有限，仅辅助），主防护须是结构层（token 软隔离 + read-only agent + 提取函数化 + detection 兜底）
- 禁止 `parse_plotly_nav_series` 零匹配静默返回空列表，必须 raise
- 禁止把 Check 7（相关系数）做成 block 级（统计嫌疑非会计恒等式，须 warn + 人工确认，与 3/5/6 确信度区分）
- 禁止把 6 条 fee_diff_monthly_max 写死常数当通用方案，须文档注明"当前假设无 performance fee 差异" + 二期可配 `funds.management_fee`/`performance_fee` 字段
