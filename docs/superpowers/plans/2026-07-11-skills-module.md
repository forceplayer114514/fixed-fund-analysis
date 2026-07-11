# 阶段 3 实施计划：Skills 模块（独立文件夹 + DB 解耦）

- **创建日期**：2026-07-11
- **前置**：阶段 2（FastAPI API 层）已完成并合并 main，73 测试通过

---

## 0. 目标与架构

建独立 `skills/` 文件夹，含 `add_fixed_fund` / `update_fixed_fund` 两个 Claude Code 技能。skills 仅负责探测+下载+提取+清洗+写 DB（`funds` + `monthly_returns`），**不算指标**（webapp 负责）。skills 与 webapp **仅通过 SQLite DB 联系**，不 import webapp 任何代码。

### 关键决策（已与用户确认）
1. **完全独立**：`skills/.claude/skills/` 独立 cc 工作区；`skills/lib/db.py` 用 **sqlite3 原生 SQL**，不 import webapp/scripts 任何代码。用户在 `skills/` 目录运行 cc 执行抓取技能。
2. **DB 路径**：环境变量 `FUND_DB_PATH`，默认 `<仓库根>/data/fund_analysis.db`（skills/ 的父目录 + data/）。
3. **表初始化**：`db.py` 的 `ensure_tables()` 用 `CREATE TABLE IF NOT EXISTS`（spec 第 3 节 schema），与 webapp `init_db` 幂等共存。
4. **NAV 重算**：skills 自己实现复利 NAV（与 webapp `crud.recompute_nav` 逻辑一致）。
5. **不算指标**：只写 `funds` + `monthly_returns`。写完提示用户在 webapp 触发 `POST /api/funds/{id}/recompute`。
6. **提取策略**（用户方案）：MCP 抓取网页 + LLM 智能提取月度收益 + 复用 `parse_factsheet.py` 通用清洗函数（**复制**到 `skills/lib/extract.py`，不 import）。
7. **删除旧技能**：删除 `skill/` 目录（旧 3 个 .md）+ `.claude/skills/` 空子目录。

### 文件夹结构
```
skills/
├── .claude/
│   └── skills/
│       ├── add_fixed_fund.md
│       └── update_fixed_fund.md
├── lib/
│   ├── __init__.py
│   ├── db.py            # sqlite3 写 DB（ensure_tables, create_fund, upsert_monthly_return, recompute_nav, list_funds）
│   └── extract.py       # 通用提取/清洗（复用 parse_factsheet 通用函数 + PDF 下载/解析）
├── tests/
│   ├── conftest.py
│   ├── test_db.py
│   └── test_extract.py
├── CLAUDE.md            # 数据完整性规则（复制根 CLAUDE.md 第一、五、六条）
├── requirements.txt     # requests, beautifulsoup4, PyMuPDF
└── README.md
```

---

## 1. 提取策略（用户方案细化）

### HTML 基金（Stake、Coolabah）
1. Claude 用 MCP `fetch`/`stealthy_fetch` 抓 `confirmed_url` → markdown/html
2. Claude（LLM）从内容提取月度收益表格（日期 + 收益率 + 可选 commentary_truth）
3. `extract.py` 清洗（日期解析为月末 YYYY-MM-DD、格式校验、缺口检查）
4. `db.py` 写 DB

### PDF 基金（Bentham、Metrics）
1. Claude 用 MCP `fetch` 抓基金页面，定位 PDF 链接
2. `extract.py` 的 `download_pdf(url, path)` 下载 PDF（复用 `fetch_web.py` 的 `download_file`）
3. `extract.py` 的 `parse_pdf_text(pdf_path)` 用 PyMuPDF 提取文本（复用 `parse_factsheet._process_single_*_pdf` 通用部分）
4. Claude（LLM）从 PDF 文本提取月度收益
5. `extract.py` 清洗 + `db.py` 写 DB

### 通用清洗（extract.py，复制自 parse_factsheet.py）
- `MONTH_MAP`、`clean_spacing`、`get_last_day_of_month`、`extract_month_prefix`、`check_gaps`

---

## 2. 任务分解

### 任务 0：前置确认 + 文件夹骨架
- 确认 `ScraplingServer` MCP 在 `skills/` 工作区可用（检查 `~/.claude/` 配置；若项目级需复制到 `skills/.claude/`）
- 创建 `skills/` 目录结构（`.claude/skills/`、`lib/`、`tests/`）
- `skills/CLAUDE.md`：复制根 CLAUDE.md 第一条（数据完整性）、第五条（异常值保留）、第六条（防幻觉回填）
- `skills/requirements.txt`：requests, beautifulsoup4, PyMuPDF（无 SQLAlchemy）

### 任务 1：DB 写入层（TDD，子代理）
- `skills/lib/db.py`（sqlite3 原生 SQL）：
  - `get_connection()`：读 `FUND_DB_PATH`，默认 `<仓库根>/data/fund_analysis.db`
  - `ensure_tables()`：`CREATE TABLE IF NOT EXISTS`（funds + monthly_returns，spec 第 3 节）
  - `create_fund(fund_id, fund_name, apir_code, confirmed_url, fetch_method, url_type, max_pdf_pages, verified_at)`：INSERT，UNIQUE 冲突抛错
  - `upsert_monthly_return(fund_id, date, net_return, commentary_truth)`：`INSERT ... ON CONFLICT(fund_id,date) DO UPDATE` + `recompute_nav`
  - `recompute_nav(fund_id)`：按日期升序复利重算（nav=1.0 起点）
  - `list_funds()` / `get_fund(fund_id)` / `get_monthly_returns(fund_id)`
- `skills/tests/test_db.py`：临时 DB 单元测试（create/upsert/nav 重算/缺口检查/UNIQUE 冲突）
- `skills/tests/conftest.py`：临时 DB fixture
- 子代理 TDD：写失败测试 → 实现 → 通过

### 任务 2：提取辅助层（子代理）
- `skills/lib/extract.py`：从 `scripts/parse_factsheet.py` + `fetch_web.py` **复制**通用函数
  - `MONTH_MAP`、`clean_spacing`、`get_last_day_of_month`、`extract_month_prefix`、`check_gaps`
  - `download_pdf(url, path)`（复制自 `fetch_web.download_file`）
  - `parse_pdf_text(pdf_path, max_pages)`（从 `_process_single_*_pdf` 提取通用 PyMuPDF 逻辑）
- `skills/tests/test_extract.py`：日期解析、缺口检查、clean_spacing 单元测试

### 任务 3：add_fixed_fund 技能（子代理）
- `skills/.claude/skills/add_fixed_fund.md`：
  - **输入**：基金名称 + 可选 APIR/URL
  - **步骤**：
    1. WebSearch 探测基金事实单 URL（若未提供）
    2. 验证 APIR 正则 `^[A-Z]{3}\d{4}AU$`（可为空，Stake/MXT 无标准 APIR）
    3. 判断 HTML/PDF：
       - HTML：MCP `fetch`/`stealthy_fetch` 抓取 → LLM 提取月度收益表格
       - PDF：MCP 抓页面找链接 → `download_pdf` → `parse_pdf_text` → LLM 提取
    4. `extract.py` 清洗（日期→月末、格式校验、`check_gaps` 缺口零容忍）
    5. `db.py` `create_fund` + `upsert_monthly_return`（逐月）
    6. 查 DB 验证（`get_monthly_returns` 核对）
  - **数据完整性约束**：缺口零容忍（报错并列出缺失月份）、异常值保留不自动纠正、不捏造不插值、数据可追溯（URL + 抓取时间）
  - **输出**：入库月数、起止日期、数据截止月、异常标记

### 任务 4：update_fixed_fund 技能（子代理）
- `skills/.claude/skills/update_fixed_fund.md`：
  - **输入**：基金 ID 或名称
  - **步骤**：
    1. `db.py` `list_funds`/`get_fund` 读现有基金配置
    2. MCP 抓取最新月度数据
    3. LLM 提取新增月份
    4. `db.py` `upsert_monthly_return`（仅新增/变更月）
    5. 查 DB 验证
  - **输出**：新增月数、最新截止月

### 任务 5：端到端 DB 验证（子代理抓取，主对话核对）
- 选一只 HTML 基金（**Stake**，URL 简单：`hellostake.com/legal/monthly-performance-report`）端到端
- 子代理执行：MCP 抓取 → LLM 提取 → `extract.py` 清洗 → `db.py` 写 DB
- 主对话查 DB 验证（CLAUDE.md 第二条：主对话核对子代理返回数据）：
  - `funds` 表：fund_id/fund_name/apir_code/confirmed_url 正确
  - `monthly_returns`：月度数据正确，可追溯 URL + 抓取时间
  - `nav` 复利正确（1.0 起点）
  - 月度数据无缺口（或明确报错）
  - 异常值保留（不自动纠正）
- 不通过则重新委派验证（CLAUDE.md 第二条）

### 任务 6：删除旧技能 + 文档
- 删除 `skill/` 目录（旧 `fund_analysis.md`/`fund_analysis_history.md`/`fund_analysis_list.md`）
- 删除 `.claude/skills/` 空子目录（`fund_analysis`/`fund_analysis_history`/`fund_analysis_list`）
- `skills/README.md`：使用说明（如何在 skills/ 运行 cc、环境变量、技能用法）

---

## 3. 子代理使用策略（用户要求"完全可以用子代理"）

- **任务 1-4**：每个任务派一个子代理（TDD 实现）
- **任务 5**：派子代理执行端到端抓取，主对话核对 DB 数据
- 主对话负责：编排、核对返回数据、DB 验证、删除旧资产
- 每个子代理返回后，主对话核对符合 CLAUDE.md 第一条（数据完整性）

---

## 4. 验证标准
1. `skills/tests/` 单元测试全通过（`pytest skills/tests/`）
2. 端到端：Stake 基金抓取后，DB 中 `funds` + `monthly_returns` 数据正确
3. NAV 复利正确（1.0 起点）
4. 月度数据无缺口（或缺口明确报错并列出月份）
5. 异常值保留（不自动纠正）
6. 数据可追溯（URL + 抓取时间）

---

## 5. 不在阶段 3 范围
- 删除旧 `scripts/`（阶段 5，spec 6.4）
- 删除 `references/fund_registry.yaml`（阶段 5，spec 6.1）
- JSON→SQLite 历史数据迁移（阶段 5，spec 6.3）
- React 前端（阶段 4）
- LLM 报告生成（未来，在 skills/cc 端）
- 迁移其余 4 只基金数据（Bentham/Coolabah/Metrics/Stake 除验证用的那只）——阶段 5 或按需

---

## 6. 风险与回退
- **MCP 在 skills/ 工作区不可用**：回退为 `requests` 直接抓取 HTML（复制 `fetch_web.py` 逻辑到 `extract.py`），保留 MCP 作为首选。
- **LLM 提取不准**：异常值保留原则兜底，人工复核；必要时为特定基金补充硬编码解析器到 `extract.py`。
- **PDF 基金提取复杂**：阶段 3 端到端验证只用 HTML 基金（Stake），PDF 基金（Bentham/Metrics）的完整迁移放阶段 5。
