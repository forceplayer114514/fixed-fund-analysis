# Task 4: 固化 skill 文档硬约束 + add 改用 ingest.py

**阶段 3**（优化计划阶段 3）。依赖 Task 1-3（lib/ingest.py CLI + extract.py 函数已实现）。

## 目标
把优化固化进 skill 文档：`add_fixed_fund` 工作流改用 `lib.ingest.py` 全自动流水线（不再手写提取脚本），两个文档新增「硬约束」节。

## Files
- Modify: `.claude/skills/add_fixed_fund.md`
- Modify: `.claude/skills/update_fixed_fund.md`

## Steps

- [ ] **Step 1: 修改 `add_fixed_fund.md`**

**1a. 环境前提**：在依赖列表加 `lib/ingest.py`（全自动流水线入口）。改为：
```
- 依赖：`lib/db.py`（sqlite3 写入）、`lib/extract.py`（提取/清洗）、`lib/ingest.py`（全自动流水线入口）、MCP `ScraplingServer`（`stealthy_fetch` 抓 JS 渲染/反爬页）、`mcp__search__search`（搜索，替代已禁用的 WebSearch）
```

**1b. 工作流第 3 步**（抓取数据）改为：
```markdown
### 3. 抓取归档页（委派子 agent）
- **JS 渲染归档页**（如 hellostake.com）：用 MCP `stealthy_fetch(url, network_idle=true, wait=3000)`。**禁止用 `fetch`**（只返回 footer，不等 JS 渲染）。
- 存 markdown 到 `/tmp/<fund_id>_archive.md`
- **PDF 基金**（Bentham/Metrics）：MCP 抓基金页面找 PDF 归档链接，同样存归档页 markdown
- 子 agent 抓取返回后，主对话核对归档页是否含 PDF 链接（数值/格式异常则重新委派）
```

**1c. 工作流第 4-6 步**（LLM 提取/清洗/写入）合并改为：
```markdown
### 4. 全自动入库
```bash
cd skills && python3 -m lib.ingest add \
  --fund-id <id> --name "<name>" \
  --archive-html /tmp/<fund_id>_archive.md \
  --confirmed-url <url> --verified-at <YYYY-MM-DD> \
  [--apir <apir>] [--max-workers <int>]
```
`ingest.py` 自动完成：解析归档页 PDF 链接 -> 并发下载+提取（Commentary 当月收益 + performance 表滚动收益）-> `gate_check` 硬 gate（复利交叉验证 + 缺口 + ANTI-FABRICATION + 字段类型）-> 入库。
- `gate_pass=True`：入库成功，打印 months/start/end/NAV
- `gate_pass=False`：报错列出 errors，**不入库**，退出码 1
- `short_history_warning=True`（月数<36）：提示数据不足，webapp 将标记不参与 Sortino/去平滑
```

**1d. 第 7 步**（输出与提示）保留，但简化为引用 ingest.py 返回 dict + recompute 提示。

**1e. 新增「硬约束（PDF 提取，2026-07 优化固化）」节**（放在"数据完整性约束"节之后）：
```markdown
## 硬约束（PDF 提取，2026-07 优化固化）
1. **Commentary 正文优先于 performance 表 1mo**：复利交叉验证已证明 performance 表 1mo 口径错误（列错位/12mo=inception 合并），Commentary 正文值才是当月真实收益。`extract.extract_commentary_return` 优先于 `extract.extract_perf_rolling` 的 1mo。
2. **负号强制捕获**：所有百分比正则用 `[+-]?\d+\.\d+%`（负号 `-0.26%` 必须捕获，正数可省略正号）。
3. **入库前必须过 `gate_check`**：复利交叉验证（monthly 复利 vs 滚动收益，误差<0.5%）+ 缺口零容忍 + ANTI-FABRICATION（连续>=3月相同非零值）+ 字段类型（|r|<0.5）。不通过报错停，不入库。
4. **PDF 下载并发**：`download_and_extract_parallel` ThreadPool pipeline，`max_workers=min(16, os.cpu_count())`（M5 满核），下载+提取 IO/CPU 重叠无 barrier，失败隔离。
5. **inspect 避坑**：脚本在 `skills/` 目录跑（`python3 -m lib.ingest`），**不在 /tmp**。/tmp/inspect.py 曾遮蔽标准库致 PyMuPDF 加载失败，已清理但 /tmp 下禁放 .py 脚本。
6. **序列起点=第一份真实研报日期**：不反推捏造成立初期数据。提取层只做纯文本到数字映射，禁止 backfill/forward-fill。
7. **单 PDF 提取失败隔离**：失败项 commentary=None，不中断其他 PDF；gate_check 检测由此产生的缺口。
```

**1f. 子 agent 委派节**更新：明确"`stealthy_fetch` 抓归档页委派子 agent；`ingest.py` 由主对话跑（程序化，无需委派）"。

- [ ] **Step 2: 修改 `update_fixed_fund.md`**

**2a. 环境前提**：依赖列表加 `lib/ingest.py`、`mcp__search__search`。

**2b. 工作流第 3 步**（抓取最新数据）：HTML/PDF 分流保留，但补一句"提取用 `extract.extract_commentary_return`（Commentary 优先）+ `extract.extract_perf_rolling`，负号正则 `[+-]?\d+\.\d+%`"。

**2c. 工作流第 4 步**（缺口检查）：补"合并后用 `extract.gate_check(全部 records, rolling_per_month)` 做硬 gate（复利+缺口+ANTI-FABRICATION+字段类型），不通过停止更新"。

**2d. 新增「硬约束（PDF 提取，2026-07 优化固化）」节**（同 add_fixed_fund.md 的 7 条，复制）。

**2e. 提及 free-search-mcp**：在合适位置加"URL 探测用 `mcp__search__search`（WebSearch 已全局禁用）"。

- [ ] **Step 3: 验证文档一致性**

Run: `cd /Users/chong/Desktop/fixed_fund_analysis/skills && grep -l "gate_check\|ingest.py\|extract_commentary_return" .claude/skills/add_fixed_fund.md .claude/skills/update_fixed_fund.md`
Expected: 两个文件都匹配。

检查：
- add_fixed_fund.md 工作流第 4 步是 `python3 -m lib.ingest add`（非手写提取脚本）
- 两个文档都有「硬约束」节含 7 条
- 两个文档都提及 Commentary 优先、负号、gate_check、并发、inspect 避坑

- [ ] **Step 4: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add skills/.claude/skills/add_fixed_fund.md skills/.claude/skills/update_fixed_fund.md
git commit -m "docs(skills):固化 PDF 提取硬约束 + add 改用 ingest.py

Task 4（阶段 3）:
- add_fixed_fund: 工作流第4-6步合并为 python3 -m lib.ingest add 全自动流水线
- 两文档新增「硬约束」节 7 条: Commentary 优先/负号/gate_check/并发/inspect避坑/不反推捏造/失败隔离
- update_fixed_fund: 提及 extract_commentary_return + gate_check

固化优化成果, 避免 27' 超时回归。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

## 验收标准
1. add_fixed_fund.md 工作流用 `python3 -m lib.ingest add`（不再手写提取脚本）
2. 两个文档都有「硬约束」节，含 Commentary 优先、负号、gate_check、并发、inspect 避坑、不反推捏造、失败隔离 7 条
3. 两个文档都提及 `mcp__search__search`（替代 WebSearch）
4. 已提交

## 完成后
在 `skills/.superpowers/sdd/task-4-report.md` 写报告：列出两文档改动点、commit hash。
