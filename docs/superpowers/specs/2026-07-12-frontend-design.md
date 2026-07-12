# 固定收益基金分析 — React 前端设计

> **日期：** 2026-07-12
> **状态：** 待审查
> **对应阶段：** 阶段 4

## 1. 目标

构建 React + Vite + Tailwind + ECharts 前端，消费 webapp 后端 10 个 API 端点，提供动态交互式基金分析界面。

## 2. 架构

### 2.1 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 框架 | React 18 + Vite | 开发速度快，HMR 热重载 |
| 语言 | TypeScript | 类型安全 |
| 样式 | Tailwind CSS | 快速原型，一致设计 |
| 图表 | ECharts (echarts-for-react) | 专业级金融图表，默认高质量主题，数据缩放/多轴/标记线开箱即用 |
| 路由 | React Router v6 | SPA 路由 |
| 状态管理 | Zustand | 轻量，适合此规模，无 boilerplate |
| HTTP | fetch（原生） | 仅 10 个端点，无需 axios |

### 2.2 目录结构

```
webapp/frontend/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.js
├── postcss.config.js
├── src/
│   ├── main.tsx                  # 入口
│   ├── App.tsx                   # 路由 + 侧边栏布局
│   ├── api/
│   │   └── client.ts             # fetch 封装，所有 API 调用
│   ├── store/
│   │   └── useStore.ts           # Zustand store
│   ├── pages/
│   │   ├── Dashboard.tsx         # 对比看板
│   │   ├── Anomalies.tsx         # 异常审计
│   │   └── FundManagement.tsx    # 基金管理
│   ├── components/
│   │   ├── Layout.tsx            # 侧边栏 + 主内容布局
│   │   ├── Sidebar.tsx           # 导航侧边栏
│   │   ├── MetricCard.tsx        # 指标卡片（含排名）
│   │   ├── NavChart.tsx          # 折线图 + 指标切换下拉框
│   │   ├── CompareTable.tsx      # 指标对比表格
│   │   ├── AnomalyTable.tsx      # 异常数据表格
│   │   ├── SmoothingCards.tsx    # 去平滑分析卡片组
│   │   ├── FundChips.tsx         # 基金选择芯片
│   │   └── ErrorBoundary.tsx     # 错误边界
│   └── types/
│       └── index.ts              # TypeScript 类型定义
```

### 2.3 路由设计

| 路径 | 页面 | 说明 |
|---|---|---|
| `/` | Dashboard | 对比看板（首页） |
| `/anomalies` | Anomalies | 异常审计 |
| `/funds` | FundManagement | 基金管理 |

## 3. 对比看板页（Dashboard）

### 3.1 布局

```
┌──────────┬─────────────────────────────────────────┐
│ 侧边栏   │  对比看板                                │
│          │  [全部基金▼]  [全部区间▼]  [原始/去平滑▼]  │
│          │                                         │
│          │  [Bentham] [Coolabah] [Stake] [Metrics]  │
│          │  (chip 可多选，点击切换高亮)               │
│          │                                         │
│          │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐  │
│          │  │年化超额│ │最大回撤│ │Omega │ │胜率  │  │
│          │  │3.82%(1)││-2.14%(2)││2.45(1)││72.3%(3)││
│          │  └──────┘ └──────┘ └──────┘ └──────┘  │
│          │                                         │
│          │  ┌─ 月走势图 ──────────────────── [超额收益▼] ─┐│
│          │  │  📈 4条折线，选中基金高亮               │  │
│          │  │  其他基金暗淡                           │  │
│          │  │  [Bentham] [Coolabah] [Stake] [Metrics]│  │
│          │  └────────────────────────────────────────┘  │
│          │                                         │
│          │  ┌─ 指标对比 ────────────────────────────┐│
│          │  │ 基金名│年化超额│回撤│Omega│胜率│跑输│波动率││
│          │  │ Bentham│3.82%(1)│...│...│...│...│...│  │
│          │  │ ...                                 │  │
│          │  └──────────────────────────────────────┘  │
└──────────┴─────────────────────────────────────────┘
```

### 3.2 组件分解

**FundChips：** 所有已注册基金显示为圆形 chip，点击切换选中状态。选中基金影响指标卡片、图表高亮、表格筛选。

**MetricCard（4 张）：** 展示年化超额收益、最大回撤、Omega 比率、超额胜率。每项值后带 `(排名)`，排名基于各基金在当前区间的比较。排名数据来源于 `GET /api/metrics/compare` 返回的所有基金指标。

**NavChart：**
- 下拉框切换指标：累计 NAV / 月超额收益
- ECharts `LineChart`（echarts-for-react）渲染选中基金的对应指标月走势
- 数据来源：`GET /api/metrics/time-series`
- 选中基金线 **高亮**（粗线、全不透明），其他基金线 **暗淡**（细线、低不透明度）
- ECharts 原生 tooltip 展示鼠标悬停时各基金数值 + 排名
- 底部图例，支持点击显隐单条线
- X 轴日期格式化，Y 轴百分比格式
- 支持数据缩放（dataZoom）查看局部区间

**CompareTable：** 表格展示所有选定基金的完整指标对比。表头：基金名称、年化超额收益、最大回撤、Omega 比率、超额胜率、最长跑输月数、年化波动率。每项数值后带排名 `(N)`。

### 3.3 数据流

```
页面加载 →  GET /api/funds → 获取基金列表
         →  GET /api/metrics/compare?fund_ids=A,B,C,D&period=full → 指标数据 + 排名
         →  GET /api/metrics/time-series?fund_ids=A,B,C,D&period=full → 时序数据

区间切换 → 重新请求 compare + time-series 带新 period
去平滑切换 → 切换表格和卡片显示 orig_* vs un_* 字段
基金选中 → 仅影响 UI 高亮，不重新请求数据
```

## 4. 异常审计页（Anomalies）

### 4.1 布局

```
┌──────────┬─────────────────────────────────────────┐
│ 侧边栏   │  异常审计                                │
│          │                                         │
│          │  ┌─ 异常数据 ──────────────── 共 5 条 ─┐ │
│          │  │ [全部基金（5条异常）▼]                │ │
│          │  │ ┌───┬──────┬──────┬──────┬─┬───┬──┐│ │
│          │  │ │基金│日期  │收益率│Z-Score││纠错││ │ │
│          │  │ ├───┼──────┼──────┼──────┼┼───┼┤ │ │
│          │  │ │...│      │      │      ││纠错││ │ │
│          │  │ └───┴──────┴──────┴──────┴┴───┴┘│ │
│          │  └───────────────────────────────────┘ │
│          │                                         │
│          │  ┌─ 去平滑分析（Geltner 检验）─ 4 支 ─┐ │
│          │  │                                     │ │
│          │  │ ┌─需去平滑──┐ ┌─需去平滑────┐       │ │
│          │  │ │ Bentham  │ │ Coolabah   │       │ │
│          │  │ │ φ=0.62   │ │ φ=0.48     │       │ │
│          │  │ │ Q=8.45   │ │ Q=5.92     │       │ │
│          │  │ │ 概率92.3%│ │ 概率85.1%  │       │ │
│          │  │ │ ████████ │ │ ███████    │       │ │
│          │  │ └──────────┘ └────────────┘       │ │
│          │  │                                     │ │
│          │  │ ┌─建议关注──┐ ┌─数据不足────┐       │ │
│          │  │ │ Stake    │ │ Metrics    │       │ │
│          │  │ │ φ=0.31   │ │ φ=0.15     │       │ │
│          │  │ │ 概率17.7%│ │ 无法判定    │       │ │
│          │  │ │ ██       │ │            │       │ │
│          │  │ └──────────┘ └────────────┘       │ │
│          │  └───────────────────────────────────┘ │
└──────────┴─────────────────────────────────────────┘
```

### 4.2 第一部分：异常数据

- 基金选择下拉框：`全部基金（N 条）` + 每支基金 `基金名（N 条）`
- 数据来源：`GET /api/anomalies`
- 表格列：基金名、日期、收益率、Z-Score、阈值、均值、标准差、操作（纠错按钮）
- Z-Score 颜色：≥3.0 红色高亮，≥2.5 橙色
- **纠错按钮**：点击弹出 Modal/Inline 编辑框，输入新净值，提交 `PATCH /api/monthly-returns/{id}`，成功后刷新异常列表

### 4.3 第二部分：去平滑分析

- 数据来源：`GET /api/metrics/compare?fund_ids=all&period=full` 中的 Geltner 字段
- 卡片按 4 种状态分类：

| 状态 | 颜色 | 条件 | 展示 |
|---|---|---|---|
| 🔴 需去平滑 | 红左边框 | 三重防火墙全部通过 | φ、Q、p 值、人为干预概率 |
| 🟠 建议关注 | 橙左边框 | φ>0 但 Q 未达显著 | φ、p 值、说明 |
| ⚪ 数据不足 | 灰左边框 | 月数<36 | 说明"需 ≥36 个月" |
| 🟢 无需去平滑 | 绿左边框 | φ≈0 或防火墙 2/3 失败 | 显示检验结果 |

- 人为干预概率 = Q 统计量的 p 值百分比，即 `p * 100%`，表示"数据序列非随机（存在人为平滑干预）的置信度"
- 进度条可视化概率

## 5. 基金管理页（FundManagement）

### 5.1 布局

```
┌──────────┬─────────────────────────────────────────┐
│ 侧边栏   │  基金管理                                │
│          │  [+ 添加基金]                            │
│          │                                         │
│          │  ┌─────────────────────────────────────┐│
│          │  │ 基金名  │ APIR  │ 数据截止 │ 操作  ││
│          │  ├─────────────────────────────────────┤│
│          │  │ Bentham│ —     │ 2026-05 │ 重算🗑││
│          │  │ Coolabah│...   │ 2026-05 │ 重算🗑││
│          │  │ Stake  │ —     │ 2026-05 │ 重算🗑││
│          │  │ Metrics│...    │ 2026-05 │ 重算🗑││
│          │  └─────────────────────────────────────┘│
└──────────┴─────────────────────────────────────────┘
```

### 5.2 功能

- **基金列表**：`GET /api/funds`，展示 fund_id、fund_name、apir_code、data_cutoff_month
- **添加基金**：`POST /api/funds`，表单输入 fund_id, fund_name, apir_code, confirmed_url, fetch_method, url_type
  - 仅注册元信息，数据抓取由 skills 完成
  - 添加成功后刷新列表，提示用户去 skills 端运行 `/add_fixed_fund`
- **重算**：`POST /api/funds/{fund_id}/recompute`，触发 5 维指标计算
  - 按钮点击后显示 loading，完成后刷新列表
- **删除**：`DELETE /api/funds/{fund_id}`，确认弹窗后删除

## 6. 状态管理（Zustand store）

```typescript
interface AppState {
  // 基金列表
  funds: Fund[];
  loading: boolean;
  error: string | null;

  // 选中状态
  selectedFundIds: string[];
  period: 'full' | '3y' | '1y' | 'common';
  chartMetric: 'excess_return' | 'omega' | 'win_rate' | 'nav';
  smoothingMode: 'original' | 'unsmoothed';

  // 数据缓存
  compareData: CompareResponse | null;
  timeSeriesData: TimeSeriesResponse | null;
  anomalies: Anomaly[];

  // 操作
  fetchFunds: () => Promise<void>;
  fetchCompare: () => Promise<void>;
  fetchTimeSeries: () => Promise<void>;
  fetchAnomalies: () => Promise<void>;
  toggleFund: (fundId: string) => void;
  setPeriod: (period: string) => void;
  // ...
}
```

## 7. API 对接

| 前端用途 | 端点 | 请求参数 | 响应关键字段 |
|---|---|---|---|
| 基金列表 | GET /api/funds | — | fund_id, fund_name, data_cutoff_month, has_metrics |
| 添加基金 | POST /api/funds | FundCreate JSON | FundResponse |
| 删除基金 | DELETE /api/funds/{id} | — | 204 |
| 重算指标 | POST /api/funds/{id}/recompute | — | fund_metrics dict |
| 指标对比 | GET /api/metrics/compare | fund_ids, period | period, funds[] |
| 时序数据 | GET /api/metrics/time-series | fund_ids, period | series[].{fund_id, dates, orig_nav, unsm_nav} |
| 异常列表 | GET /api/anomalies | — | AnomalyResponse[] |
| 人工纠错 | PATCH /api/monthly-returns/{id} | net_return | metrics dict |
| RBA 刷新 | POST /api/rba/refresh | — | current_rate, upserted |
| 健康检查 | GET /health | — | status |

## 8. 错误处理

- 全局 `ErrorBoundary` 组件包裹路由
- API 调用统一在 `api/client.ts` 中 try-catch，错误写入 Zustand `error` 字段
- 加载态：各页面独立 loading 状态，骨架屏或 spinner
- 空态：无基金时显示引导提示"请先通过 skills 端添加基金"
- 404/422 错误：toast 或页面内提示

## 9. 非功能需求

- Vite 代理 dev server 到后端 `http://localhost:8000`（CORS 已在后端配置）
- 首次加载从 `GET /api/funds` 获取基金列表作为初始化数据
- 无服务端渲染，纯 CSR
