# 澳大利亚固定收益基金业绩分析 Web 系统设计规范 (Spec)

- **创建日期**：2026-07-10
- **当前状态**：Approved (已批准)
- **文档路径**：`docs/superpowers/specs/2026-07-10-fund-web-ui-design.md`

---

## 1. 业务背景与痛点 (Context)
当前项目是一个固定收益基金分析系统，专注于评估澳洲固收基金的业绩表现并防范资产估值平滑效应（Smoothing）对风险指标的低估。
原有系统基于 Python 脚本抓取事实单 PDF/HTML，将清洗后的时序数据以 JSON 文件形式存储在本地，并利用 `generate_report.py` 离线计算指标并生成 Markdown 报告。

### 重构动机：
1. **展示维度受限**：Markdown 报告无法提供多维度的交互体验（如多只基金的动态对比、任意特定区间的筛选、原始 vs 去平滑 NAV 曲线折线图展示）。
2. **数据流耦合严重**：数据抓取爬虫（Skills）与后台指标计算、表格报表编排逻辑混杂在一起。每次计算修改都需要重跑爬取，极难维护。
3. **指标不够体系化**：原先的 Sortino 比率较难应对非对称概率分布的极值干扰，同时需要引入能反映投资者实际心理体感（跑输基准的最长连续月数）和基金经理稳定性（超额胜率）的指标。

---

## 2. 核心架构设计与重构路径

系统改用**数据库解耦架构**。将整个仓库重构为两个独立的职责模块，中间通过本地 **SQLite 数据库** 关联：

```
+------------------+         写入/同步数据         +-----------------------+
|  Custom Skills   | -------------------------> |  SQLite Database      |
|  (数据抓取/清洗) |                            |  (data/fund_analysis) |
+------------------+                            +-----------------------+
                                                            ^
                                                            | 查询原始数据 & 保存指标结果
                                                            v
+------------------+          提供 API          +-----------------------+
|   React 前端    | <------------------------- |   FastAPI 网页后端    |
|   (Web UI)       |                            |   (在线计算指标/AI)   |
+------------------+                            +-----------------------+
```

### 2.1 ETL与抓取端（Skills 模块）
* **单一职责**：自定义技能 `/add_fixed_fund` 和 `/update_fixed_fund` 仅负责数据源的探测、下载、文本/图表提取与清洗，并将最基础的月度收益率（`net_return` 和 `commentary_truth`）写入 SQLite 数据库的 `monthly_returns` 表。
* **不再计算指标**：Skills 端不再引入 Geltner 去平滑计算，也不再计算 Sortino、回撤等高级指标，更不生成 Markdown/Excel 报告。

### 2.2 数据库存储端（Database 模块）
* 在 `data/fund_analysis.db` 下创建 SQLite 数据库。
* 数据表为：
  1. `funds`：基金基本信息（APIR 设为可选 `nullable` 以支持 Stake 等基金，防重约束基于标准基金名称 `fund_name`）。
  2. `monthly_returns`：月度收益率与累计复利 NAV 的原始时序。
  3. `anomalies`：统计学异常值（MAD/Z-Score 偏离值）。
  4. `rba_cash_rates`：RBA 年化现金利率历史。
  5. `fund_metrics`：预计算好的 5 维业绩风险指标结果，附带 `date_period` 截止年月。
  6. `ai_reports`：缓存大模型生成的 Markdown 对比投研报告。

### 2.3 呈现与计算端（Web App 模块）
* 新建独立的 `/webapp` 目录，划分为前端和后端：
  * **网页后端 (`webapp/backend`)**：基于 Python FastAPI 搭建。
    * 导入复用 `scripts/metrics.py` 的计算库，在 API 请求或数据同步时进行在线指标计算，结果缓存于 `fund_metrics` 表。
    * 提供定时任务自动获取最新的 RBA 基准利率。
    * 引入大模型集成（支持配置中转站 API 地址和 API Key），为选中的基金组动态编写分析报告。
  * **网页前端 (`webapp/frontend`)**：基于 React + TypeScript (Vite) + Tailwind CSS + Recharts。
    * 负责渲染看板首页，多基金动态对比图表，异常数据复核等交互页面。
    * 支持在基金卡片后直观显示该基金的“数据截止年月”（如：`数据截止: 2026-05`）。

---

## 3. SQLite 数据表 Schema 定义

```sql
-- 基金注册主表
CREATE TABLE funds (
    fund_id TEXT PRIMARY KEY,                       -- 基金英文/拼音标识，如 coolabah_long_short_credit
    fund_name TEXT NOT NULL UNIQUE,                 -- 基金官方标准名称，作全局防重防重复注册
    apir_code TEXT UNIQUE,                          -- APIR代码 (格式正则 ^[A-Z]{3}\d{4}AU$, 选填/可空)
    confirmed_url TEXT NOT NULL,                    -- 事实单爬取 URL
    fetch_method TEXT NOT NULL,                     -- 提取策略（pdf 或 html_plotly 等）
    url_type TEXT NOT NULL,                         -- 链接类别
    max_pdf_pages INTEGER,                          -- 限制解析页数
    verified_at TEXT,                               -- 上次检验时间 YYYY-MM-DD
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 月度收益率与时序数据表
CREATE TABLE monthly_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_id TEXT NOT NULL,
    date TEXT NOT NULL,                             -- 月份末尾日期 (YYYY-MM-DD)，如 2026-05-31
    net_return REAL NOT NULL,                       -- 原始月度收益率 (如 0.0053 代表 0.53%)
    nav REAL NOT NULL,                              -- 累计复利净值 (以 1.0 为起点，插入时事务中重新重排)
    commentary_truth REAL,                          -- 事实单段落提取的对照值
    FOREIGN KEY (fund_id) REFERENCES funds(fund_id) ON DELETE CASCADE,
    UNIQUE(fund_id, date)
);

-- 数据异常审计表
CREATE TABLE anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_id TEXT NOT NULL,
    date TEXT NOT NULL,                             -- 异常发生月份 YYYY-MM-DD
    value REAL NOT NULL,                            -- 异常的收益率数值
    z_score REAL NOT NULL,                          -- 偏离标准差数
    threshold_sigma REAL NOT NULL,                  -- 判断门禁（默认 3.0）
    mean REAL NOT NULL,                             -- 时序移动平均值
    stdev REAL NOT NULL,                            -- 时序标准差
    FOREIGN KEY (fund_id) REFERENCES funds(fund_id) ON DELETE CASCADE
);

-- RBA 无风险利率缓存表
CREATE TABLE rba_cash_rates (
    date_period TEXT PRIMARY KEY,                   -- 对应月份 (YYYY-MM)
    rate REAL NOT NULL,                             -- 年化官方现金利率，如 0.0435
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 5维核心业绩指标结果表
CREATE TABLE fund_metrics (
    fund_id TEXT PRIMARY KEY,
    date_period TEXT NOT NULL,                      -- 数据最近截止月份 (YYYY-MM)
    history_months INTEGER NOT NULL,                -- 历史有效月度数
    is_short_history_warning INTEGER NOT NULL,       -- 是否历史数据过短不足 36 个月 (0或1)
    unsmoothing_coefficient_phi REAL NOT NULL,      -- 计算出的一阶自相关系数
    is_geltner_applied INTEGER NOT NULL,            -- 是否成功应用去平滑修正 (0或1)
    
    -- 1. 进攻维度 (绝对实力)
    orig_annualized_excess_return REAL NOT NULL,    -- 原始逐月扣除 RBA 的复利年化超额收益 (Alpha)
    un_annualized_excess_return REAL NOT NULL,      -- 去平滑后的超额收益
    
    -- 2. 防守维度 (底线思维)
    orig_max_drawdown REAL NOT NULL,                -- 原始绝对最大回撤
    un_max_drawdown REAL NOT NULL,                  -- 去平滑最大回撤
    
    -- 3. 性价比维度 (核心灵魂)
    orig_omega_ratio REAL NOT NULL,                 -- 原始 Omega 比率 (Target = 逐月 RBA)
    un_omega_ratio REAL NOT NULL,                   -- 去平滑后 Omega 比率
    
    -- 4. 体感与煎熬度维度
    orig_excess_win_rate REAL NOT NULL,             -- 原始超额月度胜率 (跑赢 RBA 的月数 / 总月数)
    un_excess_win_rate REAL NOT NULL,               -- 去平滑后胜率
    orig_max_underperform_months INTEGER NOT NULL,  -- 原始跑输 RBA 的最长连续月数
    un_max_underperform_months INTEGER NOT NULL,    -- 去平滑跑输 RBA 最长连续月数
    
    -- 5. 真实性与防伪 (去平滑统计项)
    orig_annualized_volatility REAL NOT NULL,       -- 原始波动率
    un_annualized_volatility REAL NOT NULL,         -- 去平滑后波动率
    ljung_box_q REAL NOT NULL,                      -- Ljung-Box Q 统计量
    is_q_significant INTEGER NOT NULL,              -- 自相关是否显著 (0或1)
    
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (fund_id) REFERENCES funds(fund_id) ON DELETE CASCADE
);

-- AI 对比投研报告缓存表
CREATE TABLE ai_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_ids TEXT NOT NULL,                         -- 逗号分隔的基金ID组，字母升序（如 "bentham_global_income_fund,stake_accumulate"）
    date_period TEXT NOT NULL,                      -- 数据最近截止点 YYYY-MM
    report_type TEXT NOT NULL,                      -- 对比区间类型（full, 3y, 1y, common 等）
    content TEXT NOT NULL,                          -- Markdown 格式的 AI 评述正文
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4. 核心计算指标与算法设计

重构后的后端计算引擎需要包含以下五个维度的计算实现：

### 4.1 进攻维度：复利年化超额收益 (Alpha)
为克服不同基金成立时期无风险利率不同的偏差，不使用单一 hurdle rate 扣减，而是**逐月扣减当期历史 RBA 年化现金利率**：
1. 获得月度超额收益：$r_{e, t} = r_t - \frac{\text{RBA}_t}{12}$
2. 得到复利累积超额收益：$\text{CumExcess} = \prod_{t=1}^n (1 + r_{e, t})$
3. 计算年化超额收益：$\text{AnnExcess} = \text{CumExcess}^{\frac{12}{n}} - 1$

### 4.2 防守维度：最大回撤 (Max Drawdown)
回撤衡量本金从最高峰值到最低谷底的损失。
* 基于累计 NAV 序列 $NAV_t$：
  $$\text{MaxDD} = \min_{t} \left( \frac{NAV_t - Peak_t}{Peak_t} \right) \quad \text{其中} \quad Peak_t = \max_{i \le t} NAV_i$$

### 4.3 性价比维度：Omega 比率 (Target = RBA)
使用 Omega 比率代替传统的 Sortino / Sharpe，更全面地捕获极端波动的非对称性（偏度、峰度）。对于超额收益 $r_{e, t}$：
$$\Omega = \frac{\sum_{t=1}^n \max(r_{e, t}, 0)}{\sum_{t=1}^n \max(-r_{e, t}, 0)}$$
* **解释**：即“超额为正的累积盈余面积”除以“超额为负的累积亏损面积”。
* **异常处理**：若分母极小或为 0（极端优秀，无跑输月），返回 `float('inf')`。

### 4.4 体感与煎熬度维度
* **超额胜率**：跑赢当月 RBA 的比率。$\text{WinRate} = \frac{\sum [r_{e, t} > 0]}{n}$。
* **跑输 RBA 的最长连续月数**：超额收益 $r_{e, t} \le 0$ 的最长连续块的长度。该指标能直观刻画投资者忍受亏损或跑输大盘的心理煎熬度。

### 4.5 真实性防伪与 Geltner 去平滑 (带三重防火墙)
平滑估值会低估基金真实波动率。我们使用以下判定规则执行去平滑计算：
* **防火墙 1**：历史有效月数 $n \ge 36$。不足则该基金标记为 `is_short_history_warning = 1`，跳过后续判定，去平滑指标与原始指标完全一致，前端去平滑折线图不予显示。
* **防火墙 2**：Ljung-Box 一阶自相关检验必须统计显著。计算 Q 统计量 $Q = n(n+2)\phi^2 / (n-1)$，在 95% 置信度下，要求 $Q > 3.841$。
* **防火墙 3**：一阶自相关系数 $\phi$ 必须处于正向合理区间 $0 \le \phi \le 0.85$。
* **算法**：全部通过后，计算去平滑后的月度收益率序列 $r'_{t} = (r_t - \phi r_{t-1}) / (1 - \phi)$ 并计算对应的 5 维业绩指标。
* **特殊规则**：对于已上市的封闭式基金或交易所基金（如 MXT），仅提供原始业绩，并对其自相关性判定做单独标注，告知用户这属于场内定价资产。

---

## 5. Web 端接口与功能页设计

### 5.1 FastAPI API 端点
1. `GET /api/funds`：获取注册基金列表、最近更新截止年月。
2. `POST /api/funds`：添加新基金。由后端在后台运行 URL 验证和历史解析。
3. `DELETE /api/funds/{fund_id}`：删除指定基金（级联删除所有相关表记录）。
4. `GET /api/metrics/compare`：获取对比表数据，支持参数 `?fund_ids=A,B&period=full`（可传 3y、1y、common 对齐区间）。
5. `GET /api/metrics/time-series`：获取选中基金对齐后的累计 NAV 折线图数据（同时包含原始 NAV 和去平滑 NAV 的序列）。
6. `POST /api/reports/ai-summary`：接收 `fund_ids` 和 `period`，调用中转站大模型 API 撰写投研报告，返回 Markdown。

### 5.2 React 前端交互模块
1. **对比看板**：
   * 支持多选已录入基金，通过卡片高亮数据截止点。
   * 支持四种数据区间切换，并用卡片标签标记各基金的去平滑适用状态（如：`已应用去平滑 (phi=0.42)` / `跳过去平滑(样本过短)`）。
   * 提供多色 Recharts 时序图表，支持通过开关（Toggle）切换“显示去平滑后的 NAV 曲线”。
2. **AI 分析生成器**：
   * 在对比下方提供“生成 AI 投研对比”入口。流式呈现经过精心设计的金融 prompt 后由 LLM 生成的深度报告。
3. **数据管理与异常人工审计**：
   * 汇总所有检测出来的 $\pm 3\sigma$ 数据点。
   * 提供编辑框和“确认”按钮，允许用户在线纠错。修改后直接更新数据库，并触发该基金的 `fund_metrics` 重新计算。

---

## 6. 仓库重构与冗余清理计划

完成规范确认后，需要做以下清理：
1. **删除** `references/fund_registry.yaml` (改用 `funds` 表)。
2. **删除** `scripts/registry_schema.py` 和 `scripts/validate_registry.py`，因为其 Pydantic schema 重定位到了 FastAPI 数据库模型的 `models.py` 中。
3. **删除** `data/raw/` 和 `data/cleaned/` 下各基金的 JSON 数据目录与缓存文件（所有历史数据在迁移后全量入库，不再依靠本地 JSON 文件交互）。
4. **删除** `scripts/generate_report.py`, `scripts/cleanup_funds.py`, `scripts/fetch_browser.py`，不再生成静态 Markdown 报告。
5. **重构本地自定义技能**：
   * 删除旧的 `fund_analysis.md`, `fund_analysis_list.md`, `fund_analysis_history.md`。
   * 新增 `add_fixed_fund.md` 和 `update_fixed_fund.md`。其内部执行的是连接 SQLite 数据库进行 ETL 导入/同步，并在控制台完成输出的 Python 逻辑。

---

## 7. 部署与环境配置 (Env Variables)
后端运行时需要如下环境变量支持：
* `DATABASE_URL`：SQLite 连接串，默认 `sqlite:///data/fund_analysis.db`。
* `LLM_API_BASE`：大模型 API 中转站端点地址（选填，默认官方地址）。
* `LLM_API_KEY`：大模型服务商 API Key（必填以启用 AI 对比报告功能）。
* `LLM_MODEL`：使用的大模型名称，默认 `claude-3-5-sonnet` 或其他合适的高性价比模型。

---

## 8. 实施测试与双重验证 (QA Verification)
* **单元测试**：使用 `pytest` 编写 `webapp/backend` 的核心逻辑单元测试（包括 Ljung-Box 检验、Geltner 去平滑公式、Omega 比率计算等），确保迁移后与原先测试的数值一致。
* **数据回测比对**：将原先 `generate_report.py` 生成的 `report.md` 中的历史计算指标数值，与重构后从新 SQLite 数据库 `fund_metrics` 读出的数据进行逐项对比，确保重构过程精度零损失。
