# Skills 模块（基金数据抓取 / 清洗 / 入库）

独立的 Claude Code 工作区，负责澳洲固定收益基金事实单的抓取、清洗与入库。

## 与 webapp 的关系

- skills **仅通过共享 SQLite 数据库**（`data/fund_analysis.db`）与 webapp 联系
- skills 只写 `funds` + `monthly_returns` 表（原始月度收益率 + NAV 复利）
- 指标计算、异常检测、RBA 更新由 **webapp** 负责：
  - `POST /api/funds/{fund_id}/recompute`：触发 NAV 重算 + 异常检测 + 5 维指标计算
  - `POST /api/rba/refresh`：手动刷新 RBA 现金利率（webapp 亦有每日定时调度）
- skills **不 import webapp 任何代码**（用标准库 `sqlite3` 直接操作 DB）

## 使用方式

在本目录（`skills/`）下用 Claude Code 打开，即可使用两个 slash 命令：

- `/add_fixed_fund <基金名称>`：添加新基金
  - 探测事实单 URL → 验证 APIR → MCP 抓取网页 / 下载 PDF → LLM 提取月度收益 → 清洗 → 入库
- `/update_fixed_fund <基金ID或名称>`：更新已有基金最新月度数据
  - 读 `funds` 表配置 → MCP 抓取最新月 → upsert 新增月份 → 提示 webapp recompute

## 环境变量

- `FUND_DB_PATH`：SQLite 数据库路径，默认 `<仓库根>/data/fund_analysis.db`

## 依赖安装

```bash
pip3 install -r requirements.txt
```

## 测试

```bash
cd skills && python3 -m pytest tests/ -v
```

## 目录结构

```
skills/
├── .claude/skills/        # Claude Code 技能定义（项目级，仅本工作区可用）
│   ├── add_fixed_fund.md
│   └── update_fixed_fund.md
├── lib/
│   ├── db.py              # sqlite3 写 DB（funds + monthly_returns + NAV 重算）
│   └── extract.py         # 通用提取/清洗辅助（复用旧 parse_factsheet 通用函数）
├── tests/
│   ├── conftest.py
│   ├── test_db.py
│   └── test_extract.py
├── CLAUDE.md              # 数据完整性规则（最高优先级）
├── requirements.txt
└── README.md
```
