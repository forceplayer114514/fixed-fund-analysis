# 🇦🇺 澳洲固定收益基金量化分析平台
Australian Fixed Income Fund Analytics Platform

端到端的基金月报抓取 → 清洗 → 入库 → 指标计算 → 可视化对比平台。核心目标：
用**可追溯的原始月度净收益**（严禁捏造/回填），配合澳洲央行（RBA）现金利率
动态扣减，产出可对比的风险调整后表现（Sortino / IR / 回撤 / 恢复月数等），
并以 Web 看板呈现。

> **2026-07 架构变更**：旧的 `skills/`（Claude Code slash command 驱动摄取）
> 已删除。摄取逻辑重写为纯 Python 库 `llm_ingest/`，由 `webapp/backend` 直接
> import 调用（不再是"两者只通过 SQLite 交互"的松耦合设计）。前端"添加基金"
> 按钮会触发真实抓取，不再只是登记元信息。

---

## ✨ 当前能力

- **数据摄取（`llm_ingest/`，由 `webapp/backend` 后台任务调用）**
  - 三层归档发现 fallback：
    - **L1 官网归档页**：Grok（agentic search，并发问两次、两种基金名大小写
      变体，互相校验）或 Tavily 定位候选页 → 抓页 → LLM (`classify_pdf_links`)
      判断页内哪些链接是本基金月报、对应哪个月，只收链接文字里能验证出月份的
      结果（防止从年份猜月份的幻觉）
    - **L2 Wayback CDX**：`web.archive.org/cdx` 补官网已滚动删除的旧月份
    - **L3 fundmonitors.com Full Fund Profile AJAX**：逐月表兜底源
  - 三条提取通道（PDF / HTML 表格&正文 / CSV），LLM 只抄原文 token，
    数值换算和校验全在代码侧（`parsers.py`）
  - **"网页本身就是月报"自动识别**（2026-08）：Coolabah 一类发行商把月报做成
    一张内嵌 Plotly 图表的网页，页上没有可下载的月报文件。发现层在 PDF 路径
    **彻底失败之后**才检查沿途各页有没有内嵌本基金净值序列
    （`plotly_nav.parse_plotly_nav_series`，纯代码无 LLM）；命中则记住该页、
    从序列首末日期推定成立月与最新月，在同一个摄取任务内直接走
    `html_to_pdf.py` 整页渲染成 PDF 并入 PDF 通道。
    净值序列**只当判别器与月份区间来源，不直接入库** —— 数值仍走两道闸。
    - 同一策略常有多个份额类别（Assisted / Institutional / Base Fee / USD…），
      各占一页。曲线名命中数 ≠ 1 时**拒绝判定而非挑一条**（2026-07-18 错源
      173 个月事故的教训）；此时日志会列出页内候选曲线名，据此把份额类别
      补进基金名重试即可
    - 曲线名可能与营销名不一致（实测 Coolabah 某基金页地址写
      `smarter-money-higher-income`、曲线名却是 `Short Term Income`），
      以日志给出的候选曲线名为准
  - **两道闸**（`verify.py`）：闸1 引用硬校验（LLM 抄的每个数字 token 必须
    同时是它自己给的 quote、和原文的子串）；闸2 数值滚动窗口交叉校验
    （月度值复利算 3/6/12mo，和文档里的滚动收益对不上就打回）
  - 两闸全过 → 直接入 `monthly_returns`；未过 / 找不到 → `pending_review`
    人工审核队列；三层 fallback 穷尽仍找不到 → `confirmed_gaps`（不参与
    计算，也不再重复尝试）
  - `confirmed_url` 归档页地址会记住并复用：同一基金再次"更新数据"不用
    重新跑一遍完整发现流程；记住的地址失效（0 链接）会自动清空重新发现
  - **反捏造硬约束**：LLM 输出无数值字段（全字符串原文摘抄），数学换算
    全在代码；数据缺口只标记为 gap，不做 backfill/forward-fill
- **指标计算（`webapp/backend/app/metrics_pipeline.py`）**
  - NAV 复利、超额收益（对 RBA cash rate 逐月扣减）
  - 5 维指标：年化收益 / 年化超额 / Sortino / IR / 最大回撤 & 恢复月数
  - Geltner 去平滑（Ljung-Box 一阶自相关性触发），产出 unsmoothed 副本
  - 异常检测（月度 z-score / rolling 断裂）+ 手工订正 PATCH 接口
- **前端看板（`webapp/frontend`）**
  - 概览：多基金 chip 选择、指标卡、对比表（含超额）、异常热力图
  - NAV 状态机（原始/rebase 归一 / 回撤视图）、滚动超额曲线、时间锚点
    点击联动、基准线、tooltip 短码、窗口口径说明
  - 基金管理页：新增基金触发真实抓取（后台任务 + 轮询进度）、待审核
    队列人工通过/驳回
- **调度**
  - `apscheduler` 每日刷新 RBA cash rate（`POST /api/rba/refresh` 手动触发）

---

## 🧱 目录结构

```text
.
├── llm_ingest/                  # 摄取核心库（webapp/backend 直接 import）
│   ├── discover.py / discover2.py  #   归档发现三层 fallback + LLM 链接判类
│   ├── grok.py / search.py         #   Grok agentic search / Tavily 检索
│   ├── navigate.py                 #   归档入口页 -> 内链 1 跳导航
│   ├── fundmonitors.py             #   L3 fundmonitors AJAX 逐月表兜底
│   ├── extract.py / extract_html.py / extract_csv.py  # PDF/HTML/CSV 三通道
│   ├── html_to_pdf.py              #   Plotly/JS 图表页 -> PDF 渲染
│   ├── parsers.py                  #   LLM 原文 token -> 数值/单位换算
│   ├── verify.py                   #   两道闸（引用硬校验 + 滚动数值交叉）
│   ├── issuer_rules.py             #   发行商专项提取规则（按关键词路由）
│   ├── store.py                    #   写库（monthly_returns/pending_review/confirmed_gaps）
│   ├── migrations/                 #   幂等 DB schema 迁移
│   ├── prompts/                    #   各 LLM 调用的 prompt 模板
│   ├── scripts/                    #   一次性批处理脚本（如 spec_b 全清重爬）
│   └── cli.py                      #   compare 命令：PDF 抽取结果 vs DB 对拍报告
├── webapp/
│   ├── backend/                 # FastAPI + SQLAlchemy 2 + APScheduler
│   │   └── app/
│   │       ├── routers/         #   funds / metrics / anomalies / rba / ingest
│   │       ├── models.py        #   Fund / MonthlyReturn / FundMetric / Anomaly / RBARate
│   │       ├── calculations.py  #   Sortino / IR / drawdown / Geltner unsmoothing
│   │       ├── metrics_pipeline.py
│   │       └── anomaly.py
│   └── frontend/                # React 18 + Vite 5 + TS + Tailwind + ECharts + Zustand
│       └── src/
│           ├── pages/           #   Dashboard / Anomalies / FundManagement
│           ├── components/      #   NavChart / RollingExcessChart / ExcessHeatmap / CompareTable ...
│           └── store/           #   Zustand global state
├── data/
│   ├── fund_analysis.db         # 共享 SQLite（唯一持久化数据源）
│   └── pdf_cache/               # 下载的原始月报 PDF 缓存（按基金分目录）
├── tests/                       # llm_ingest 摄取管道单测（pytest，仓库根运行）
├── docs/                        # 迭代 plan / 分析报告
└── .archive_scripts/            # 旧一次性脚本，历史保留，不再被任何流程调用
```

---

## 🚀 本地运行

### 0. Python & Node 环境
- Python 3.12+
- Node 20+ / pnpm | npm

### 1. 安装依赖（一个 venv 即可，摄取逻辑运行在 backend 进程内）
```bash
pip3 install -r requirements.txt                  # llm_ingest 用: PyMuPDF / python-dateutil
pip3 install -r webapp/backend/requirements.txt    # FastAPI / SQLAlchemy / requests / bs4 / httpx ...
```

### 2. 配置 API key（`.env`，参考 `.env.example`）
摄取依赖三个外部 LLM/搜索后端：
- `SUB2API_*`：Gemini（flash-lite）中转，负责链接分类 / PDF·HTML·CSV 提取
- `TAVILY_API_KEY`：结构化搜索兜底
- `GROK_*`：agentic search，归档页发现首选

### 3. 启动后端（API + 指标 + 摄取）
```bash
cd webapp/backend
python3 migrate_phase1.py                 # 首次或有 schema 变更时执行
uvicorn app.main:app --reload --port 8000
# health check: http://localhost:8000/health
```
- DB 路径解析优先级：`FUND_DB_PATH` > `DATABASE_URL` > 仓库默认 `data/fund_analysis.db`
- 启动时自动跑幂等迁移、可选启动 RBA 每日调度器（`SCHEDULER_ENABLED`）

### 4. 启动前端
```bash
cd webapp/frontend
npm install
npm run dev                               # Vite dev server，默认 5173
npm run test                              # vitest
npm run build
```

### 5. 测试
```bash
python3 -m pytest tests/ -v                        # 仓库根：llm_ingest 摄取管道
python3 -m pytest webapp/backend/tests/ -v          # backend：API / 指标 / 异常检测
cd webapp/frontend && npm run test                  # frontend
```

---

## 🌐 API 一览（webapp/backend）

| Method | Path                                    | 说明 |
|--------|-----------------------------------------|------|
| GET    | `/api/funds`                            | 基金列表 |
| POST   | `/api/funds`                            | 新增基金元信息（不触发抓取） |
| PATCH  | `/api/funds/{fund_id}/visibility`       | 显示/隐藏基金 |
| DELETE | `/api/funds/{fund_id}`                  | 删除基金 |
| GET    | `/api/funds/{fund_id}/returns`          | 单基金月度收益明细 |
| POST   | `/api/funds/{fund_id}/recompute`        | 触发 NAV + 指标 + 异常重算 |
| POST   | `/api/ingest/funds`                     | 起摄取后台任务（发现+下载+提取+入库），返回 job_id |
| GET    | `/api/ingest/jobs/active`               | 当前运行中的摄取任务 |
| GET    | `/api/ingest/jobs/{job_id}`             | 查摄取任务状态/日志 |
| GET    | `/api/pending`                          | 待人工审核记录（两闸未过） |
| PATCH  | `/api/pending/{review_id}/approve`      | 人工通过 → 入 monthly_returns + 重算 |
| PATCH  | `/api/pending/{review_id}/reject`       | 人工驳回 |
| GET    | `/api/metrics/compare?fund_ids=…`       | 多基金对比（5 维指标 + 窗口口径） |
| GET    | `/api/metrics/time-series?fund_id=…`    | NAV / 月度收益 / 超额 时序 |
| GET    | `/api/anomalies`                        | 异常清单 |
| PATCH  | `/api/monthly-returns/{row_id}`         | 人工订正单行月度收益 |
| POST   | `/api/rba/refresh`                      | 手动刷新 RBA cash rate |
| GET    | `/api/rba/history`                      | RBA cash rate 历史序列 |

---

## 🛑 数据完整性（不可违反）

1. 任何净值 / 收益 / 利率必须能追溯到真实数据源（URL + 抓取时间）。
2. 数据缺口零容忍：pipeline 报错并列出具体月份，**不允许**插值、backfill、
   forward-fill 或"合理估算"。
3. 单只基金 <36 个月标记 `short_history`（去平滑/动态 φ 不参与），
   >8 个月才允许入库。
4. 提取到的原始数值只允许**保留 + 打标异常**，不允许自动纠错（历史
   教训见 `CLAUDE.md` 第六章：2026-07 曾为对齐官方年化收益硬编码回填
   数据缺口，现由反捏造闸 + 摄取层禁 backfill 硬约束防范）。

详见根目录 `CLAUDE.md`。

---

## 🗂️ 迭代记录

- `docs/` 保留了各阶段的实施 plan 与分析报告（Spec A~H：摄取管道从
  skills 迁到 llm_ingest、LLM 判链接清单替代文件名规则、Grok 双查询
  容错、HTML 渲染 PDF 通道等）。
- 最近提交（截至 2026-07）：摄取发现层用 LLM 分类整体替换文件名启发式
  规则；Grok 并发问两次（两种基金名写法）互相校验 + 抓页失败重试 +
  找到即收工；归档页地址记忆与自愈；清理已废弃的 Plotly HTML 字节窗口
  切片方案。

---

## 📄 声明
本仓库仅用于研究与自动化流程展示，抓取数据不构成任何投资建议。
