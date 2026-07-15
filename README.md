# 🇦🇺 澳洲固定收益基金量化分析平台
Australian Fixed Income Fund Analytics Platform

端到端的基金月报抓取 → 清洗 → 入库 → 指标计算 → 可视化对比平台。核心目标：
用**可追溯的原始月度净收益**（严禁捏造/回填），配合澳洲央行（RBA）现金利率
动态扣减，产出可对比的风险调整后表现（Sortino / IR / 回撤 / 恢复月数等），
并以 Web 看板呈现。

平台原生兼容 Claude Code：`skills/` 目录下的 slash command 由大模型驱动完成
抓取 & 入库；`webapp/` 负责指标计算、异常检测与前端展示。两者**只通过共享
SQLite 数据库** `data/fund_analysis.db` 交互，互不 import。

---

## ✨ 当前能力

- **数据摄取（skills/）**
  - Slash command：`/add_fixed_fund <名称>`、`/update_fixed_fund <ID|名称>`
  - APIR 三段式校验（`^[A-Z]{3}\d{4}AU$`）+ Pydantic registry 前置校验
  - 通用/专用（Bentham net、Stake Commentary、Coolabah Plotly、Macquarie CSV 等）
    提取策略 + fallback 链，命中失败即报错，不猜数
  - `pending_review` / `confirmed_gaps` 表分流；增量 `update_fund` 复查滞留
  - **反捏造硬约束**：`validate_data.py::ANTI-FABRICATION GUARD` 禁止连续常数插值；
    数据缺口只能视为 gap，不做 backfill/forward-fill
- **指标计算（webapp/backend/app/metrics_pipeline.py）**
  - NAV 复利、超额收益（对 RBA cash rate 逐月扣减）
  - 5 维指标：年化收益 / 年化超额 / Sortino / IR / 最大回撤 & 恢复月数
  - Geltner 去平滑（Ljung-Box 一阶自相关性触发），产出 unsmoothed 副本
  - 异常检测（月度 z-score / rolling 断裂）+ 手工订正 PATCH 接口
- **前端看板（webapp/frontend）**
  - 概览：多基金 chip 选择、指标卡、对比表（含超额）、异常热力图
  - Phase 2 图表：NAV 状态机（原始/rebase 归一 / 回撤视图）、滚动超额曲线、
    时间锚点点击联动、基准线、tooltip 短码、窗口口径说明
- **调度**
  - `apscheduler` 每日刷新 RBA cash rate（`POST /api/rba/refresh` 手动触发）

---

## 🧱 目录结构

```text
.
├── skills/                     # 抓取/清洗/入库（LLM 驱动，独立工作区）
│   ├── .claude/skills/         #   Claude Code slash command 定义
│   ├── lib/                    #   db / extract / ingest / strategies / consistency / audit
│   ├── tests/                  #   pytest：extract & db 契约测试
│   └── CLAUDE.md               #   数据完整性硬约束（不可覆盖）
├── webapp/
│   ├── backend/                # FastAPI + SQLAlchemy 2 + APScheduler
│   │   └── app/
│   │       ├── routers/        #   funds / metrics / anomalies / rba
│   │       ├── models.py       #   Fund / MonthlyReturn / FundMetric / Anomaly / RBARate
│   │       ├── calculations.py #   Sortino / IR / drawdown / Geltner unsmoothing
│   │       ├── metrics_pipeline.py
│   │       └── anomaly.py
│   └── frontend/               # React 18 + Vite 5 + TS + Tailwind + ECharts + Zustand
│       └── src/
│           ├── pages/          #   Dashboard / Anomalies / FundManagement
│           ├── components/     #   NavChart / RollingExcessChart / ExcessHeatmap / CompareTable ...
│           └── store/          #   Zustand global state
├── data/
│   └── fund_analysis.db        # 共享 SQLite（skills 写，webapp 读+算+写指标）
├── docs/superpowers/plans/     # 各阶段迭代 plan（backend/API/frontend/dashboard refactor …）
└── .archive_scripts/           # 旧 pipeline 脚本（历史保留，不再由 run_all.py 调度）
```

历史上的 `scripts/run_all.py` 单体流水线已被拆分：抓取归 skills，计算归 webapp。
旧脚本移至 `.archive_scripts/` 仅供参考。

---

## 🚀 本地运行

### 0. Python & Node 环境
- Python 3.12+
- Node 20+ / pnpm | npm

### 1. skills（数据摄取）
```bash
cd skills
pip3 install -r requirements.txt          # requests / bs4 / PyMuPDF
python3 -m pytest tests/ -v               # 契约测试
# 在本目录用 Claude Code 打开，即可使用：
#   /add_fixed_fund <基金名称>
#   /update_fixed_fund <基金ID|名称>
```
- 默认 DB：`../data/fund_analysis.db`（可用 `FUND_DB_PATH` 覆盖）
- skills 只写 `funds` + `monthly_returns` 表，**不算指标**

### 2. webapp/backend（API + 指标）
```bash
cd webapp/backend
pip3 install -r requirements.txt          # fastapi / sqlalchemy / apscheduler / httpx
python3 migrate_phase1.py                 # 首次或有 schema 变更时执行
uvicorn app.main:app --reload --port 8000
# health check: http://localhost:8000/health
# 单基金重算：POST /api/funds/{fund_id}/recompute
```

### 3. webapp/frontend（看板）
```bash
cd webapp/frontend
npm install
npm run dev                               # Vite dev server，默认 5173
npm run test                              # vitest
npm run build
```

---

## 🌐 API 一览（webapp/backend）

| Method | Path                                    | 说明 |
|--------|-----------------------------------------|------|
| GET    | `/api/funds`                            | 基金列表 |
| POST   | `/api/funds`                            | 新增基金（一般由 skills 使用） |
| DELETE | `/api/funds/{fund_id}`                  | 删除基金 |
| POST   | `/api/funds/{fund_id}/recompute`        | 触发 NAV + 指标 + 异常重算 |
| GET    | `/api/metrics/compare?fund_ids=…`       | 多基金对比（5 维指标 + 窗口口径） |
| GET    | `/api/metrics/time-series?fund_id=…`    | NAV / 月度收益 / 超额 时序 |
| GET    | `/api/anomalies`                        | 异常清单 |
| PATCH  | `/api/monthly-returns/{row_id}`         | 人工订正单行月度收益 |
| POST   | `/api/rba/refresh`                      | 手动刷新 RBA cash rate |

---

## 🛑 数据完整性（不可违反）

1. 任何净值 / 收益 / 利率必须能追溯到真实数据源（URL + 抓取时间）。
2. 数据缺口零容忍：pipeline 报错并列出具体月份，**不允许**插值、backfill、
   forward-fill 或"合理估算"。
3. 单只基金 <36 个月标记 `short_history`（去平滑/动态 φ 不参与），
   >8 个月才允许入库。
4. 提取到的原始数值只允许**保留 + 打标异常**，不允许自动纠错（历史
   教训见 `CLAUDE.md` 第六章）。

详见 `CLAUDE.md`（根目录）与 `skills/CLAUDE.md`（skills 侧硬约束）。

---

## 🗂️ 迭代记录

- `docs/superpowers/plans/` 保留了每一阶段的实施 plan：backend 基础 →
  FastAPI API 层 → skills 模块拆分 → 前端 → Dashboard 指标层重构
  (Phase 1) → 图表 & 交互重构（Phase 2）→ skills 代码驱动重构。
- 最近提交（截至 2026-07）：Phase 2 验收修复（锚定点击 / 基准线 /
  短码 tooltip / 窗口说明），A4 通用提取器准入校验 + `EXTRACTOR_MISMATCH`。

---

## 📄 声明
本仓库仅用于研究与自动化流程展示，抓取数据不构成任何投资建议。
