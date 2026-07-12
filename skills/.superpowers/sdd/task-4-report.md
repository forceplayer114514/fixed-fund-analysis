# Task 4 报告：固化 skill 文档硬约束 + add 改用 ingest.py

**阶段 3**（优化计划阶段 3，最后一个 task，纯文档修改）。
**Commit**: `aef991659f41e008bf3f20271ffec897695eb9b3`（分支 `fix/audit-p0-p3`）
**提交文件**: `skills/.claude/skills/add_fixed_fund.md`、`skills/.claude/skills/update_fixed_fund.md`（2 files changed, 44 insertions, 46 deletions）

## 前置验证（执行 Edit 前确认函数名准确）
- `lib/ingest.py` 存在，CLI 子命令 `add`，参数 `--fund-id/--name/--archive-html/--confirmed-url/--apir/--verified-at/--max-workers`，输出 JSON dict，退出码 0/1（gate_pass）。
- `lib/extract.py` 确认存在：`extract_commentary_return`、`extract_perf_rolling`、`download_and_extract_parallel`、`gate_check`。
- `add_fund()` 返回 dict 实际字段：`{months, start, end, gaps, gate_pass, errors, failed_months, short_history_warning}`——**不含 NAV 字段**（NAV 由 `upsert_monthly_return` 在 DB 层自动重算）。

## add_fixed_fund.md 改动点（1a-1f）
1. **1a 环境前提**：依赖列表新增 `lib/ingest.py`（全自动流水线入口）。
2. **1b 第 3 步**：原"抓取数据（按数据源类型分流）"改为"抓取归档页（委派子 agent）"——JS 渲染归档页强制 `stealthy_fetch(network_idle=true, wait=3000)`，禁止 `fetch`；存 `/tmp/<fund_id>_archive.md`；PDF 基金同样存归档页 markdown。
3. **1c 第 4-6 步合并**：原"LLM 提取 / 清洗缺口检查 / 写入数据库"三步合并为单步"全自动入库"——调用 `cd skills && python3 -m lib.ingest add ...`，自动完成解析 PDF 链接 -> 并发下载+提取 -> gate_check -> 入库。
4. **1d 第 7 步简化并重编号为第 5 步**：输出改为引用 `ingest.py` 返回的 JSON dict 字段 + recompute 提示。
5. **1e 新增「硬约束（PDF 提取，2026-07 优化固化）」节**（置于"数据完整性约束"节之后）：7 条——Commentary 优先 / 负号强制捕获 / gate_check 硬 gate / PDF 下载并发 / inspect 避坑 / 序列起点不反推捏造 / 单 PDF 失败隔离。
6. **1f 子 agent 委派节**：明确 `stealthy_fetch` 抓归档页委派子 agent，`ingest.py` 由主对话跑（程序化，无需委派）。

## update_fixed_fund.md 改动点（2a-2e）
1. **2a 环境前提**：依赖列表新增 `lib/ingest.py` + `mcp__search__search`（附 WebSearch 禁用说明）。
2. **2b 第 3 步（LLM 提取新增月份）**：补"提取用 `extract.extract_commentary_return`（Commentary 优先）+ `extract.extract_perf_rolling`，负号正则 `[+-]?\d+\.\d+%`"。第 2 步 HTML/PDF 分流保留不动。
3. **2c 第 4 步（缺口检查）**：补"合并后用 `extract.gate_check(全部 records, rolling_per_month)` 做硬 gate（复利+缺口+ANTI-FABRICATION+字段类型），不通过停止更新"。
4. **2d 新增「硬约束」节**：复制 add 的同 7 条。
5. **2e 提及 free-search-mcp**：第 1 步末尾补"若 confirmed_url 失效，用 `mcp__search__search` 重新探测（禁止 WebSearch）"。

## grep 验证结果（Step 3，全部通过）
- `grep -l "gate_check\|ingest.py\|extract_commentary_return"`：**两文件都匹配** ✓
- add 第 4 步确认为 `python3 -m lib.ingest add`（第 49 行）✓
- 「硬约束（PDF 提取，2026-07 优化固化）」节标题：add 第 71 行、update 第 80 行 ✓
- 硬约束 7 条编号 1-7 完整存在于两文档 ✓
- 关键词覆盖（add / update 计数）：
  - Commentary 正文优先：1 / 2 ✓
  - 负号强制捕获：1 / 1 ✓
  - gate_check：3 / 3 ✓
  - download_and_extract_parallel：1 / 1 ✓
  - inspect 避坑：1 / 1 ✓
  - 不反推捏造：2 / 1 ✓
  - 失败隔离：2 / 2 ✓
  - mcp__search__search：3 / 2 ✓

## 偏差说明
1. **brief 1c 写"打印 months/start/end/NAV"，实际改为"打印 months/start/end（NAV 由 upsert_monthly_return 自动重算）"**。原因：`ingest.py` 的 `add_fund()` 返回 dict 实际不含 NAV 字段（NAV 在 DB 层由 `upsert_monthly_return` 重算），照搬 brief 会误导执行 agent。语义与 brief 一致（入库后 NAV 已正确），仅表述更准确。
2. **brief 2b 标题"第 3 步（抓取最新数据）"与 update 文档实际结构不符**：update 文档中"抓取最新数据"是第 2 步、"LLM 提取新增月份"才是第 3 步。解读为：第 2 步 HTML/PDF 分流保留（符合 brief"保留"），补的提取函数说明落在第 3 步（提取步骤，语义正确位置）。已在报告标注。
3. 未碰代码文件（lib/、tests/、webapp、data/fund_analysis.db）——符合 Task 4"纯文档修改"边界。
4. `progress.md` 已有改动但未纳入本次提交（brief 只要求提交两 skill 文档）。

## 验收标准核对
- [x] add_fixed_fund.md 工作流用 `python3 -m lib.ingest add`（不再手写提取脚本）
- [x] 两文档都有「硬约束」节，含 7 条
- [x] 两文档都提及 `mcp__search__search`（替代 WebSearch）
- [x] 已提交（commit `aef9916`）
