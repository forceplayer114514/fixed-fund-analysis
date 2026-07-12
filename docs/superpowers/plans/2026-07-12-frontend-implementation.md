# 前端实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 构建 React + Vite + Tailwind + ECharts 前端，消费 webapp 后端 10 个 API 端点

**架构：** 三页 SPA（对比看板 / 异常审计 / 基金管理），Zustand 全局状态，ECharts 渲染金融时序图，侧边栏导航

**技术栈：** React 18 + TypeScript + Vite + Tailwind CSS + ECharts (echarts-for-react) + React Router v6 + Zustand

## 全局约束

1. 数据完整性：不捏造任何数据，直接消费 API 响应
2. CORS 已配 `http://localhost:5173`，后端端口 `8000`
3. Vite dev server 代理 `/api` 到 `http://localhost:8000`（避免 CORS 问题）
4. 目录结构：所有源码在 `webapp/frontend/src/` 下
5. 使用 `pnpm` 作为包管理器（如果不可用则用 `npm`）
6. 图表统一使用 ECharts（echarts-for-react），不引入 Recharts

---
### Task 1: 项目脚手架

**Files:**
- Create: `webapp/frontend/package.json`
- Create: `webapp/frontend/tsconfig.json`
- Create: `webapp/frontend/tsconfig.app.json`
- Create: `webapp/frontend/tsconfig.node.json`
- Create: `webapp/frontend/vite.config.ts`
- Create: `webapp/frontend/tailwind.config.js`
- Create: `webapp/frontend/postcss.config.js`
- Create: `webapp/frontend/index.html`
- Create: `webapp/frontend/src/main.tsx`
- Create: `webapp/frontend/src/vite-env.d.ts`
- Create: `webapp/frontend/src/index.css`

**Interfaces:**
- Produces: 可运行的 Vite dev server，`pnpm run dev` 可启动

- [ ] **Step 1: 初始化项目与安装依赖**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
mkdir -p webapp/frontend && cd webapp/frontend
```

创建 `package.json`:

```json
{
  "name": "fixed-fund-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.0",
    "zustand": "^4.5.0",
    "echarts": "^5.5.0",
    "echarts-for-react": "^3.0.2"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "autoprefixer": "^10.4.19",
    "postcss": "^8.4.38",
    "tailwindcss": "^3.4.4",
    "typescript": "^5.5.0",
    "vite": "^5.4.0"
  }
}
```

- [ ] **Step 2: 创建 Vite 配置**

`vite.config.ts`:
```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
```

- [ ] **Step 3: 创建 Tailwind 配置**

`tailwind.config.js`:
```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {},
  },
  plugins: [],
}
```

`postcss.config.js`:
```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

- [ ] **Step 4: 创建 TypeScript 配置**

`tsconfig.json`:
```json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" }
  ]
}
```

`tsconfig.app.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"]
}
```

`tsconfig.node.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2023"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "strict": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 5: 创建入口文件**

`index.html`:
```html
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>固定收益基金分析</title>
  </head>
  <body class="bg-gray-50">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`src/main.tsx`:
```typescript
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
```

`src/index.css`:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
```

`src/vite-env.d.ts`:
```typescript
/// <reference types="vite/client" />
```

`src/App.tsx`（最小版本，后续 Task 3 完善）:
```typescript
function App() {
  return <div className="p-4 text-lg">固定收益基金分析</div>
}
export default App
```

- [ ] **Step 6: 安装依赖并验证**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis/webapp/frontend
npm install
npx tsc -b --noEmit 2>&1 || true  # 预计有暂时错误因为组件尚未实现
echo "Scaffold complete"
```

---

### Task 2: 类型定义 + API Client + Zustand Store

**Files:**
- Create: `webapp/frontend/src/types/index.ts`
- Create: `webapp/frontend/src/api/client.ts`
- Create: `webapp/frontend/src/store/useStore.ts`

**Interfaces:**
- Consumes: Task 1 的 Vite 脚手架
- Produces: `types/index.ts` 被所有页面/组件引用；`api/client.ts` 被 store 调用；`store/useStore.ts` 被所有页面/组件使用

- [ ] **Step 1: 创建类型定义**

`src/types/index.ts`:
```typescript
/** 基金元信息（来自 GET /api/funds） */
export interface Fund {
  fund_id: string
  fund_name: string
  apir_code: string | null
  confirmed_url: string
  fetch_method: string
  url_type: string
  max_pdf_pages: number | null
  data_cutoff_month: string | null
  has_metrics: boolean
}

/** 5 维指标（来自 compare 端点或 recompute 返回） */
export interface FundMetrics {
  fund_id: string
  date_period: string
  history_months: number
  is_short_history_warning: boolean
  unsmoothing_coefficient_phi: number
  is_geltner_applied: boolean
  orig_annualized_excess_return: number
  un_annualized_excess_return: number | null
  orig_max_drawdown: number
  un_max_drawdown: number
  orig_omega_ratio: number | null
  un_omega_ratio: number | null
  orig_excess_win_rate: number
  un_excess_win_rate: number
  orig_max_underperform_months: number
  un_max_underperform_months: number
  orig_annualized_volatility: number
  un_annualized_volatility: number
  ljung_box_q: number
  is_q_significant: boolean
}

/** compare 端点返回 */
export interface CompareResponse {
  period: string
  funds: FundMetrics[]
}

/** 时序数据（来自 time-series 端点） */
export interface FundSeries {
  fund_id: string
  fund_name: string
  dates: string[]
  orig_nav: number[]
  unsm_nav: number[] | null
  is_geltner_applied: boolean
}

export interface TimeSeriesResponse {
  period: string
  months: string[]
  series: FundSeries[]
}

/** 异常记录 */
export interface Anomaly {
  id: number
  fund_id: string
  date: string
  value: number
  z_score: number
  threshold_sigma: number
  mean: number
  stdev: number
  fund_name: string | null
}

/** 纠错请求体 */
export interface MonthlyReturnPatch {
  net_return: number
  commentary_truth?: number | null
}

/** 添加基金请求体 */
export interface FundCreatePayload {
  fund_id: string
  fund_name: string
  apir_code?: string | null
  confirmed_url: string
  fetch_method: string
  url_type: string
  max_pdf_pages?: number | null
}
```

- [ ] **Step 2: 创建 API Client**

`src/api/client.ts`:
```typescript
const BASE = ''  // 代理处理跨域

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${body}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export const api = {
  // 基金
  listFunds: () => request<import('../types').Fund[]>('/api/funds'),
  createFund: (data: import('../types').FundCreatePayload) =>
    request<import('../types').Fund>('/api/funds', { method: 'POST', body: JSON.stringify(data) }),
  deleteFund: (fundId: string) =>
    request<void>(`/api/funds/${fundId}`, { method: 'DELETE' }),
  recomputeFund: (fundId: string) =>
    request<Record<string, unknown>>(`/api/funds/${fundId}/recompute`, { method: 'POST' }),

  // 指标
  compare: (fundIds: string[], period: string) =>
    request<import('../types').CompareResponse>(`/api/metrics/compare?fund_ids=${fundIds.join(',')}&period=${period}`),
  timeSeries: (fundIds: string[], period: string) =>
    request<import('../types').TimeSeriesResponse>(`/api/metrics/time-series?fund_ids=${fundIds.join(',')}&period=${period}`),

  // 异常
  listAnomalies: () => request<import('../types').Anomaly[]>('/api/anomalies'),
  patchMonthlyReturn: (id: number, data: import('../types').MonthlyReturnPatch) =>
    request<Record<string, unknown>>(`/api/monthly-returns/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
}
```

- [ ] **Step 3: 创建 Zustand Store**

`src/store/useStore.ts`:
```typescript
import { create } from 'zustand'
import { api } from '../api/client'
import type { Fund, FundMetrics, FundSeries, Anomaly, CompareResponse, TimeSeriesResponse } from '../types'

export type Period = 'full' | '3y' | '1y' | 'common'
export type ChartMetric = 'nav' | 'excess_return'
export type SmoothingMode = 'original' | 'unsmoothed'

interface AppState {
  // 基金列表
  funds: Fund[]
  fundsLoading: boolean
  fundsError: string | null

  // 选中
  selectedFundIds: string[]
  period: Period
  chartMetric: ChartMetric
  smoothingMode: SmoothingMode

  // 数据
  compareData: CompareResponse | null
  compareLoading: boolean
  timeSeriesData: TimeSeriesResponse | null
  timeSeriesLoading: boolean
  anomalies: Anomaly[]
  anomaliesLoading: boolean

  // 操作
  fetchFunds: () => Promise<void>
  fetchCompare: () => Promise<void>
  fetchTimeSeries: () => Promise<void>
  fetchAnomalies: () => Promise<void>
  toggleFund: (fundId: string) => void
  setPeriod: (period: Period) => void
  setChartMetric: (m: ChartMetric) => void
  setSmoothingMode: (m: SmoothingMode) => void
  patchMonthlyReturn: (id: number, netReturn: number) => Promise<void>
  recomputeFund: (fundId: string) => Promise<void>
  deleteFund: (fundId: string) => Promise<void>
}

export const useStore = create<AppState>((set, get) => ({
  funds: [],
  fundsLoading: false,
  fundsError: null,

  selectedFundIds: [],
  period: 'full',
  chartMetric: 'nav',
  smoothingMode: 'original',

  compareData: null,
  compareLoading: false,
  timeSeriesData: null,
  timeSeriesLoading: false,
  anomalies: [],
  anomaliesLoading: false,

  fetchFunds: async () => {
    set({ fundsLoading: true, fundsError: null })
    try {
      const funds = await api.listFunds()
      set({ funds, fundsLoading: false, selectedFundIds: funds.map(f => f.fund_id) })
    } catch (e: any) {
      set({ fundsError: e.message, fundsLoading: false })
    }
  },

  fetchCompare: async () => {
    const { selectedFundIds, period } = get()
    if (selectedFundIds.length === 0) return
    set({ compareLoading: true })
    try {
      const data = await api.compare(selectedFundIds, period)
      set({ compareData: data, compareLoading: false })
    } catch { set({ compareLoading: false }) }
  },

  fetchTimeSeries: async () => {
    const { selectedFundIds, period } = get()
    if (selectedFundIds.length === 0) return
    set({ timeSeriesLoading: true })
    try {
      const data = await api.timeSeries(selectedFundIds, period)
      set({ timeSeriesData: data, timeSeriesLoading: false })
    } catch { set({ timeSeriesLoading: false }) }
  },

  fetchAnomalies: async () => {
    set({ anomaliesLoading: true })
    try {
      const data = await api.listAnomalies()
      set({ anomalies: data, anomaliesLoading: false })
    } catch { set({ anomaliesLoading: false }) }
  },

  toggleFund: (fundId: string) => {
    set(state => {
      const exists = state.selectedFundIds.includes(fundId)
      const next = exists
        ? state.selectedFundIds.filter(id => id !== fundId)
        : [...state.selectedFundIds, fundId]
      return { selectedFundIds: next }
    })
  },

  setPeriod: (period: Period) => { set({ period }) },
  setChartMetric: (chartMetric: ChartMetric) => { set({ chartMetric }) },
  setSmoothingMode: (smoothingMode: SmoothingMode) => { set({ smoothingMode }) },

  patchMonthlyReturn: async (id: number, netReturn: number) => {
    await api.patchMonthlyReturn(id, { net_return: netReturn })
    await get().fetchAnomalies()
  },

  recomputeFund: async (fundId: string) => {
    await api.recomputeFund(fundId)
    await Promise.all([get().fetchFunds(), get().fetchCompare()])
  },

  deleteFund: async (fundId: string) => {
    await api.deleteFund(fundId)
    set(state => ({
      funds: state.funds.filter(f => f.fund_id !== fundId),
      selectedFundIds: state.selectedFundIds.filter(id => id !== fundId),
    }))
  },
}))
```

---

### Task 3: 布局 + 路由 + 错误边界

**Files:**
- Create: `webapp/frontend/src/components/ErrorBoundary.tsx`
- Create: `webapp/frontend/src/components/Sidebar.tsx`
- Create: `webapp/frontend/src/components/Layout.tsx`
- Modify: `webapp/frontend/src/App.tsx`
- Create: `webapp/frontend/src/pages/Dashboard.tsx`（最小占位）
- Create: `webapp/frontend/src/pages/Anomalies.tsx`（最小占位）
- Create: `webapp/frontend/src/pages/FundManagement.tsx`（最小占位）

**Interfaces:**
- Consumes: Task 2 的 store (useStore)
- Produces: App 路由结构，Layout 被所有页面使用

- [ ] **Step 1: ErrorBoundary**

`src/components/ErrorBoundary.tsx`:
```typescript
import { Component, ReactNode } from 'react'

interface Props { children: ReactNode }
interface State { error: Error | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }
  static getDerivedStateFromError(error: Error) { return { error } }
  render() {
    if (this.state.error) {
      return (
        <div className="flex items-center justify-center h-screen">
          <div className="text-center">
            <h2 className="text-xl font-bold text-red-600 mb-2">出错了</h2>
            <p className="text-gray-500 text-sm">{this.state.error.message}</p>
            <button
              className="mt-4 px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
              onClick={() => { this.setState({ error: null }); window.location.reload() }}
            >重新加载</button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
```

- [ ] **Step 2: Sidebar**

`src/components/Sidebar.tsx`:
```typescript
import { NavLink } from 'react-router-dom'

const links = [
  { to: '/', label: '对比看板' },
  { to: '/anomalies', label: '异常审计' },
  { to: '/funds', label: '基金管理' },
]

export default function Sidebar() {
  return (
    <aside className="w-56 bg-[#1a1a2e] text-white flex flex-col shrink-0">
      <h2 className="px-6 py-6 text-base font-semibold border-b border-gray-700">
        固定收益基金分析
      </h2>
      <nav className="flex-1 pt-3">
        {links.map(link => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.to === '/'}
            className={({ isActive }) =>
              `block px-6 py-3 text-sm transition-colors ${
                isActive
                  ? 'text-white bg-white/10 border-r-2 border-cyan-400 font-medium'
                  : 'text-gray-400 hover:text-white hover:bg-white/5'
              }`
            }
          >
            {link.label}
          </NavLink>
        ))}
      </nav>
      <div className="px-6 py-4 text-xs text-gray-600 border-t border-gray-700">v0.1</div>
    </aside>
  )
}
```

- [ ] **Step 3: Layout**

`src/components/Layout.tsx`:
```typescript
import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'

export default function Layout() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="flex-1 overflow-y-auto p-6 bg-gray-50">
        <Outlet />
      </main>
    </div>
  )
}
```

- [ ] **Step 4: 最小占位页面 + App 路由**

`src/pages/Dashboard.tsx`:
```typescript
export default function Dashboard() {
  return <div className="text-lg">对比看板（待实现）</div>
}
```

`src/pages/Anomalies.tsx`:
```typescript
export default function Anomalies() {
  return <div className="text-lg">异常审计（待实现）</div>
}
```

`src/pages/FundManagement.tsx`:
```typescript
export default function FundManagement() {
  return <div className="text-lg">基金管理（待实现）</div>
}
```

`src/App.tsx`:
```typescript
import { Routes, Route } from 'react-router-dom'
import ErrorBounday from './components/ErrorBoundary'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Anomalies from './pages/Anomalies'
import FundManagement from './pages/FundManagement'

export default function App() {
  return (
    <ErrorBounday>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/anomalies" element={<Anomalies />} />
          <Route path="/funds" element={<FundManagement />} />
        </Route>
      </Routes>
    </ErrorBounday>
  )
}
```

- [ ] **Step 5: 验证构建**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis/webapp/frontend
npx tsc -b --noEmit 2>&1
echo "Build check complete"
```

---

### Task 4: 对比看板页

**Files:**
- Create: `webapp/frontend/src/components/FundChips.tsx`
- Create: `webapp/frontend/src/components/MetricCard.tsx`
- Create: `webapp/frontend/src/components/NavChart.tsx`
- Create: `webapp/frontend/src/components/CompareTable.tsx`
- Rewrite: `webapp/frontend/src/pages/Dashboard.tsx`

**Interfaces:**
- Consumes: `useStore` (selectedFundIds, compareData, timeSeriesData, fetchCompare, fetchTimeSeries etc.)
- Produces: 完整对比看板页

- [ ] **Step 1: FundChips**

`src/components/FundChips.tsx`:
```typescript
import { useStore } from '../store/useStore'

export default function FundChips() {
  const funds = useStore(s => s.funds)
  const selected = useStore(s => s.selectedFundIds)
  const toggleFund = useStore(s => s.toggleFund)

  return (
    <div className="flex flex-wrap gap-2 mb-5">
      {funds.map(f => {
        const active = selected.includes(f.fund_id)
        return (
          <button
            key={f.fund_id}
            onClick={() => toggleFund(f.fund_id)}
            className={`px-4 py-1.5 rounded-full text-sm border-2 transition-colors ${
              active
                ? 'border-cyan-400 bg-cyan-50 text-cyan-800 font-medium'
                : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
            }`}
          >
            {f.fund_name}
          </button>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 2: MetricCard**

`src/components/MetricCard.tsx`:
```typescript
interface Props {
  label: string
  value: string | number
  rank?: number
}

export default function MetricCard({ label, value, rank }: Props) {
  return (
    <div className="flex-1 min-w-[140px] bg-white rounded-lg p-4 shadow-sm">
      <div className="text-xs text-gray-400 mb-1">{label}</div>
      <div className="text-lg font-semibold">
        {value ?? '—'}
        {rank != null && <span className="text-xs text-gray-400 font-normal ml-1">({rank})</span>}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: NavChart（ECharts 折线图）**

`src/components/NavChart.tsx`:
```typescript
import { useMemo } from 'react'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, DataZoomComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useStore } from '../store/useStore'

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, CanvasRenderer])

const COLORS = ['#4fc3f7', '#7c4dff', '#ff7043', '#66bb6a', '#ffca28', '#ec407a', '#26c6da', '#ab47bc']

export default function NavChart() {
  const timeSeriesData = useStore(s => s.timeSeriesData)
  const selectedFundIds = useStore(s => s.selectedFundIds)
  const chartMetric = useStore(s => s.chartMetric)
  const setChartMetric = useStore(s => s.setChartMetric)

  const option = useMemo(() => {
    if (!timeSeriesData || timeSeriesData.series.length === 0) return null

    const allMonths = timeSeriesData.months
    const series = timeSeriesData.series.map((s, i) => {
      const isSelected = selectedFundIds.includes(s.fund_id)
      const nav = chartMetric === 'nav' ? s.orig_nav : s.orig_nav  // 使用 orig_nav 展示超额/NAV
      // 对于超额收益模式，计算每月的超额收益（NAV 环比）
      const data = chartMetric === 'nav'
        ? s.orig_nav
        : s.orig_nav.map((v, j) => j === 0 ? 0 : (s.orig_nav[j] - s.orig_nav[j-1]) / s.orig_nav[j-1])

      return {
        name: s.fund_name,
        type: 'line',
        data,
        smooth: true,
        symbol: 'none',
        lineStyle: {
          width: isSelected ? 3 : 1.5,
          opacity: isSelected ? 1 : 0.25,
        },
        itemStyle: { color: COLORS[i % COLORS.length] },
      }
    })

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: any[]) => {
          const date = params[0]?.axisValue ?? ''
          const lines = params
            .sort((a: any, b: any) => (b.data ?? 0) - (a.data ?? 0))
            .map((p: any, idx: number) =>
              `${p.marker} ${p.seriesName}: ${(p.data ?? 0).toFixed(4)} <span class="text-gray-400">(${idx + 1})</span>`
            )
          return `<div class="font-medium mb-1">${date}</div>${lines.join('<br/>')}`
        },
      },
      legend: { bottom: 0, textStyle: { fontSize: 12 } },
      grid: { left: 60, right: 20, top: 20, bottom: 50 },
      xAxis: {
        type: 'category',
        data: allMonths,
        axisLabel: { fontSize: 11, color: '#999' },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          fontSize: 11,
          color: '#999',
          formatter: (v: number) => v.toFixed(2),
        },
        splitLine: { lineStyle: { color: '#f0f0f0' } },
      },
      dataZoom: [{ type: 'inside', start: 0, end: 100 }],
      series,
    }
  }, [timeSeriesData, selectedFundIds, chartMetric])

  return (
    <div className="bg-white rounded-lg p-5 shadow-sm mb-5">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-sm text-gray-400">月走势图</h3>
        <select
          className="text-sm border border-gray-200 rounded px-3 py-1.5 bg-white"
          value={chartMetric}
          onChange={e => setChartMetric(e.target.value as any)}
        >
          <option value="nav">累计 NAV</option>
          <option value="excess_return">月超额收益</option>
        </select>
      </div>
      {option
        ? <ReactEChartsCore echarts={echarts} option={option} style={{ height: 320 }} />
        : <div className="h-80 flex items-center justify-center text-gray-400 text-sm">加载中...</div>
      }
    </div>
  )
}
```

- [ ] **Step 4: CompareTable**

`src/components/CompareTable.tsx`:
```typescript
import { useMemo } from 'react'
import { useStore } from '../store/useStore'
import type { FundMetrics } from '../types'

function rankBy<T>(items: T[], extract: (item: T) => number | null, asc = false): Map<string, number> {
  const sorted = [...items].sort((a, b) => {
    const va = extract(a) ?? (asc ? Infinity : -Infinity)
    const vb = extract(b) ?? (asc ? Infinity : -Infinity)
    return asc ? va - vb : vb - va
  })
  const map = new Map<string, number>()
  sorted.forEach((item, i) => map.set((item as any).fund_id, i + 1))
  return map
}

function fmt(v: number | null, suffix = '', decimals = 2) {
  if (v == null) return '—'
  return `${(v * 100).toFixed(decimals)}${suffix}`
}

export default function CompareTable() {
  const compareData = useStore(s => s.compareData)
  const smoothingMode = useStore(s => s.smoothingMode)

  const rows = useMemo(() => {
    if (!compareData) return []
    const items = compareData.funds as (FundMetrics & { fund_id: string })[]

    const rankExcess = rankBy(items, m => smoothingMode === 'original' ? m.orig_annualized_excess_return : m.un_annualized_excess_return)
    const rankDD = rankBy(items, m => smoothingMode === 'original' ? m.orig_max_drawdown : m.un_max_drawdown, true)
    const rankOmega = rankBy(items, m => smoothingMode === 'original' ? m.orig_omega_ratio : m.un_omega_ratio)
    const rankWin = rankBy(items, m => smoothingMode === 'original' ? m.orig_excess_win_rate : m.un_excess_win_rate)

    return items.map(m => {
      const excess = smoothingMode === 'original' ? m.orig_annualized_excess_return : m.un_annualized_excess_return
      const dd = smoothingMode === 'original' ? m.orig_max_drawdown : m.un_max_drawdown
      const omega = smoothingMode === 'original' ? m.orig_omega_ratio : m.un_omega_ratio
      const winRate = smoothingMode === 'original' ? m.orig_excess_win_rate : m.un_excess_win_rate
      const run = smoothingMode === 'original' ? m.orig_max_underperform_months : m.un_max_underperform_months
      const vol = smoothingMode === 'original' ? m.orig_annualized_volatility : m.un_annualized_volatility

      return {
        fund_id: m.fund_id,
        excess: `${fmt(excess, '%')} (${rankExcess.get(m.fund_id)})`,
        dd: `${fmt(dd, '%')} (${rankDD.get(m.fund_id)})`,
        omega: `${omega?.toFixed(2) ?? '—'} (${rankOmega.get(m.fund_id)})`,
        winRate: `${fmt(winRate, '%')} (${rankWin.get(m.fund_id)})`,
        run: `${run} 个月`,
        vol: fmt(vol, '%'),
      }
    })
  }, [compareData, smoothingMode])

  if (rows.length === 0) return null

  return (
    <div className="bg-white rounded-lg p-5 shadow-sm">
      <h3 className="text-sm text-gray-400 mb-4">指标对比</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b-2 border-gray-100">
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">基金名称</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">年化超额收益</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">最大回撤</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">Omega 比率</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">超额胜率</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">最长跑输</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">年化波动率</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.fund_id} className="border-b border-gray-50 hover:bg-gray-50">
                <td className="py-2.5 px-3 font-medium">{r.fund_id}</td>
                <td className="py-2.5 px-3">{r.excess}</td>
                <td className="py-2.5 px-3">{r.dd}</td>
                <td className="py-2.5 px-3">{r.omega}</td>
                <td className="py-2.5 px-3">{r.winRate}</td>
                <td className="py-2.5 px-3">{r.run}</td>
                <td className="py-2.5 px-3">{r.vol}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Dashboard 页面**

`src/pages/Dashboard.tsx`:
```typescript
import { useEffect } from 'react'
import { useStore } from '../store/useStore'
import FundChips from '../components/FundChips'
import MetricCard from '../components/MetricCard'
import NavChart from '../components/NavChart'
import CompareTable from '../components/CompareTable'
import type { FundMetrics } from '../types'

export default function Dashboard() {
  const funds = useStore(s => s.funds)
  const fundsLoading = useStore(s => s.fundsLoading)
  const compareData = useStore(s => s.compareData)
  const compareLoading = useStore(s => s.compareLoading)
  const selectedFundIds = useStore(s => s.selectedFundIds)
  const period = useStore(s => s.period)
  const smoothingMode = useStore(s => s.smoothingMode)
  const setPeriod = useStore(s => s.setPeriod)
  const setSmoothingMode = useStore(s => s.setSmoothingMode)
  const fetchFunds = useStore(s => s.fetchFunds)
  const fetchCompare = useStore(s => s.fetchCompare)
  const fetchTimeSeries = useStore(s => s.fetchTimeSeries)

  useEffect(() => { fetchFunds() }, [])
  useEffect(() => { if (funds.length > 0) { fetchCompare(); fetchTimeSeries() } }, [selectedFundIds, period])

  // 从 compareData 提取指标卡片数据：取第一支选中基金的指标
  const firstMetrics: FundMetrics | undefined = compareData?.funds?.[0]
  const allMetrics = compareData?.funds ?? []
  const rankExcess = (v: number | null) => {
    if (v == null) return undefined
    const vals = allMetrics.map(m => smoothingMode === 'original' ? m.orig_annualized_excess_return : m.un_annualized_excess_return).filter(x => x != null)
    return vals.filter(x => (x ?? 0) > v).length + 1
  }
  const rankDD = (v: number | null) => {
    if (v == null) return undefined
    const vals = allMetrics.map(m => smoothingMode === 'original' ? m.orig_max_drawdown : m.un_max_drawdown).filter(x => x != null)
    return vals.filter(x => (x ?? 0) < v).length + 1
  }
  const rankOmega = (v: number | null) => {
    if (v == null) return undefined
    const vals = allMetrics.map(m => smoothingMode === 'original' ? m.orig_omega_ratio : m.un_omega_ratio).filter(x => x != null)
    return vals.filter(x => (x ?? 0) > v).length + 1
  }
  const rankWinRate = (v: number | null) => {
    if (v == null) return undefined
    const vals = allMetrics.map(m => smoothingMode === 'original' ? m.orig_excess_win_rate : m.un_excess_win_rate).filter(x => x != null)
    return vals.filter(x => (x ?? 0) > v).length + 1
  }

  const excess = firstMetrics ? (smoothingMode === 'original' ? firstMetrics.orig_annualized_excess_return : firstMetrics.un_annualized_excess_return) : null
  const dd = firstMetrics ? (smoothingMode === 'original' ? firstMetrics.orig_max_drawdown : firstMetrics.un_max_drawdown) : null
  const omega = firstMetrics ? (smoothingMode === 'original' ? firstMetrics.orig_omega_ratio : firstMetrics.un_omega_ratio) : null
  const winRate = firstMetrics ? (smoothingMode === 'original' ? firstMetrics.orig_excess_win_rate : firstMetrics.un_excess_win_rate) : null

  if (fundsLoading) return <div className="text-gray-400">加载基金列表...</div>
  if (funds.length === 0) return <div className="text-gray-400">暂无基金数据，请先通过 skills 端添加基金</div>

  return (
    <div>
      <div className="flex justify-between items-center mb-5 flex-wrap gap-3">
        <h1 className="text-xl font-semibold">对比看板</h1>
        <div className="flex gap-2">
          <select className="text-sm border border-gray-200 rounded px-3 py-1.5 bg-white" value={period} onChange={e => setPeriod(e.target.value as any)}>
            <option value="full">全部区间</option>
            <option value="3y">近3年</option>
            <option value="1y">近1年</option>
            <option value="common">共同区间</option>
          </select>
          <select className="text-sm border border-gray-200 rounded px-3 py-1.5 bg-white" value={smoothingMode} onChange={e => setSmoothingMode(e.target.value as any)}>
            <option value="original">原始</option>
            <option value="unsmoothed">去平滑</option>
          </select>
        </div>
      </div>

      <FundChips />

      <div className="flex gap-3 mb-6 flex-wrap">
        <MetricCard label="年化超额收益" value={excess != null ? `${(excess * 100).toFixed(2)}%` : '—'} rank={rankExcess(excess)} />
        <MetricCard label="最大回撤" value={dd != null ? `${(dd * 100).toFixed(2)}%` : '—'} rank={rankDD(dd)} />
        <MetricCard label="Omega 比率" value={omega?.toFixed(2) ?? '—'} rank={rankOmega(omega)} />
        <MetricCard label="超额胜率" value={winRate != null ? `${(winRate * 100).toFixed(1)}%` : '—'} rank={rankWinRate(winRate)} />
      </div>

      {compareLoading && <div className="text-gray-400 text-sm mb-4">计算指标中...</div>}

      <NavChart />
      <CompareTable />
    </div>
  )
}
```

---

### Task 5: 异常审计页

**Files:**
- Create: `webapp/frontend/src/components/AnomalyTable.tsx`
- Create: `webapp/frontend/src/components/SmoothingCards.tsx`
- Rewrite: `webapp/frontend/src/pages/Anomalies.tsx`

**Interfaces:**
- Consumes: `useStore` (anomalies, funds, compareData, fetchAnomalies, patchMonthlyReturn)

- [ ] **Step 1: AnomalyTable**

`src/components/AnomalyTable.tsx`:
```typescript
import { useState, useMemo } from 'react'
import { useStore } from '../store/useStore'

export default function AnomalyTable() {
  const anomalies = useStore(s => s.anomalies)
  const patchMonthlyReturn = useStore(s => s.patchMonthlyReturn)
  const [filterFund, setFilterFund] = useState<string>('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editValue, setEditValue] = useState('')

  const fundOptions = useMemo(() => {
    const map = new Map<string, number>()
    anomalies.forEach(a => {
      const key = a.fund_name ?? a.fund_id
      map.set(key, (map.get(key) ?? 0) + 1)
    })
    return Array.from(map.entries())
  }, [anomalies])

  const filtered = useMemo(() => {
    if (!filterFund) return anomalies
    return anomalies.filter(a => (a.fund_name ?? a.fund_id) === filterFund)
  }, [anomalies, filterFund])

  const totalBadges = anomalies.length > 0
    ? `全部基金（${anomalies.length} 条异常）`
    : '暂无异常数据'

  const handleEdit = async (id: number) => {
    const val = parseFloat(editValue)
    if (isNaN(val)) return
    try {
      await patchMonthlyReturn(id, val)
      setEditingId(null)
      setEditValue('')
    } catch {}
  }

  return (
    <div className="bg-white rounded-lg p-5 shadow-sm mb-5">
      <h2 className="text-base font-medium mb-4">异常数据 <span className="text-xs bg-cyan-50 text-cyan-700 px-2 py-0.5 rounded-full ml-2">{anomalies.length} 条</span></h2>

      <div className="mb-4">
        <select
          className="text-sm border border-gray-200 rounded px-3 py-1.5 bg-white"
          value={filterFund}
          onChange={e => setFilterFund(e.target.value)}
        >
          <option value="">{totalBadges}</option>
          {fundOptions.map(([name, count]) => (
            <option key={name} value={name}>{name}（{count} 条异常）</option>
          ))}
        </select>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b-2 border-gray-100">
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">基金</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">日期</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">收益率</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">Z-Score</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">阈值</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">均值</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">标准差</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(a => (
              <tr key={a.id} className="border-b border-gray-50 hover:bg-gray-50">
                <td className="py-2.5 px-3">{a.fund_name ?? a.fund_id}</td>
                <td className="py-2.5 px-3">{a.date}</td>
                <td className="py-2.5 px-3">{(a.value * 100).toFixed(2)}%</td>
                <td className={`py-2.5 px-3 font-medium ${a.z_score >= 3 ? 'text-red-600' : a.z_score >= 2.5 ? 'text-orange-500' : ''}`}>
                  {a.z_score.toFixed(2)}
                </td>
                <td className="py-2.5 px-3">{a.threshold_sigma}</td>
                <td className="py-2.5 px-3">{(a.mean * 100).toFixed(2)}%</td>
                <td className="py-2.5 px-3">{(a.stdev * 100).toFixed(2)}%</td>
                <td className="py-2.5 px-3">
                  {editingId === a.id ? (
                    <span className="flex gap-1">
                      <input
                        className="w-20 text-xs border border-gray-200 rounded px-1.5 py-0.5"
                        type="number"
                        step="0.0001"
                        value={editValue}
                        onChange={e => setEditValue(e.target.value)}
                        placeholder="新值"
                      />
                      <button className="text-xs text-white bg-blue-500 rounded px-2 py-0.5" onClick={() => handleEdit(a.id)}>确认</button>
                      <button className="text-xs text-gray-500" onClick={() => setEditingId(null)}>取消</button>
                    </span>
                  ) : (
                    <button className="text-xs text-gray-500 border border-gray-200 rounded px-2 py-0.5 hover:bg-gray-50" onClick={() => { setEditingId(a.id); setEditValue('') }}>
                      纠错
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={8} className="py-8 text-center text-gray-400">暂无异常数据</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: SmoothingCards**

`src/components/SmoothingCards.tsx`:
```typescript
import { useMemo } from 'react'
import { useStore } from '../store/useStore'

function cardBorder(status: string) {
  switch (status) {
    case 'applied': return 'border-l-red-500'
    case 'watch': return 'border-l-orange-400'
    case 'insufficient': return 'border-l-gray-300'
    default: return 'border-l-green-500'
  }
}

function statusTag(status: string) {
  switch (status) {
    case 'applied': return <span className="text-xs bg-red-50 text-red-700 px-2 py-0.5 rounded-full font-medium">需去平滑</span>
    case 'watch': return <span className="text-xs bg-orange-50 text-orange-700 px-2 py-0.5 rounded-full font-medium">建议关注</span>
    case 'insufficient': return <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full font-medium">数据不足</span>
    default: return <span className="text-xs bg-green-50 text-green-700 px-2 py-0.5 rounded-full font-medium">无需去平滑</span>
  }
}

export default function SmoothingCards() {
  const compareData = useStore(s => s.compareData)

  const cards = useMemo(() => {
    if (!compareData?.funds) return []
    return compareData.funds.map((m: any) => {
      const historyMonths = m.history_months ?? 0
      const phi = m.unsmoothing_coefficient_phi ?? 0
      const q = m.ljung_box_q ?? 0
      const isSignificant = m.is_q_significant ?? false
      const isGeltner = m.is_geltner_applied ?? false
      const isShort = m.is_short_history_warning ?? true

      let status: string
      let prob: number | null
      let note = ''

      if (isShort) {
        status = 'insufficient'
        prob = null
        note = `防火墙 1 未通过：历史数据 ${historyMonths} 个月，不足 36 个月，无法检验自相关性`
      } else if (isGeltner) {
        status = 'applied'
        prob = Math.min(q / (historyMonths - 1) * 100, 99.9)
        note = `三重防火墙全部通过，已应用 Geltner 去平滑`
      } else if (phi > 0 && !isSignificant) {
        status = 'watch'
        prob = Math.min(q / (historyMonths - 1) * 100, 99.9)
        note = `φ 为正但未达显著，建议持续观测`
      } else {
        status = 'none'
        prob = null
        note = `自相关性不显著（φ≈0 或 Q 检验未通过），无需去平滑`
      }

      return { fund_id: m.fund_id, fund_name: m.fund_name ?? m.fund_id, phi, q, historyMonths, status, prob, note }
    })
  }, [compareData])

  if (cards.length === 0) return null

  return (
    <div className="bg-white rounded-lg p-5 shadow-sm">
      <h2 className="text-base font-medium mb-4">去平滑分析（Geltner 检验） <span className="text-xs bg-cyan-50 text-cyan-700 px-2 py-0.5 rounded-full ml-2">{cards.length} 支基金</span></h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {cards.map(c => (
          <div key={c.fund_id} className={`bg-gray-50 rounded-lg p-4 border-l-4 ${cardBorder(c.status)}`}>
            <div className="mb-3">{statusTag(c.status)}</div>
            <div className="font-medium text-sm mb-3">{c.fund_name}</div>
            <div className="space-y-1.5 text-xs text-gray-600">
              <div className="flex justify-between"><span className="text-gray-400">自相关系数 φ</span><span>{c.phi.toFixed(4)}</span></div>
              <div className="flex justify-between"><span className="text-gray-400">Ljung-Box Q</span><span>{c.q.toFixed(4)}</span></div>
              <div className="flex justify-between"><span className="text-gray-400">数据月数</span><span>{c.historyMonths} 个月 {c.historyMonths >= 36 ? '✓' : '✗'}</span></div>
              <div className="flex justify-between">
                <span className="text-gray-400">人为干预概率</span>
                <span className={`font-semibold ${c.status === 'applied' ? 'text-red-600' : c.status === 'watch' ? 'text-orange-500' : 'text-gray-400'}`}>
                  {c.prob != null ? `${c.prob.toFixed(1)}%` : '无法判定'}
                </span>
              </div>
              {c.prob != null && (
                <div className="h-1.5 bg-gray-200 rounded-full mt-1 overflow-hidden">
                  <div className={`h-full rounded-full ${c.status === 'applied' ? 'bg-red-500' : 'bg-orange-400'}`} style={{ width: `${Math.min(c.prob, 100)}%` }}></div>
                </div>
              )}
            </div>
            <div className="text-xs text-gray-400 mt-3">{c.note}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Anomalies 页面**

`src/pages/Anomalies.tsx`:
```typescript
import { useEffect } from 'react'
import { useStore } from '../store/useStore'
import AnomalyTable from '../components/AnomalyTable'
import SmoothingCards from '../components/SmoothingCards'

export default function Anomalies() {
  const fetchFunds = useStore(s => s.fetchFunds)
  const fetchAnomalies = useStore(s => s.fetchAnomalies)
  const fetchCompare = useStore(s => s.fetchCompare)
  const funds = useStore(s => s.funds)
  const anomaliesLoading = useStore(s => s.anomaliesLoading)

  useEffect(() => {
    fetchFunds()
    fetchAnomalies()
  }, [])

  // 加载 compare 数据用于平滑卡片
  useEffect(() => {
    if (funds.length > 0) {
      fetchCompare()
    }
  }, [funds.length])

  return (
    <div>
      <h1 className="text-xl font-semibold mb-5">异常审计</h1>
      {anomaliesLoading && <div className="text-gray-400 text-sm mb-4">加载异常数据...</div>}
      <AnomalyTable />
      <SmoothingCards />
    </div>
  )
}
```

---

### Task 6: 基金管理页

**Files:**
- Rewrite: `webapp/frontend/src/pages/FundManagement.tsx`

**Interfaces:**
- Consumes: `useStore` (funds, fetchFunds, recomputeFund, deleteFund, createFund)

- [ ] **Step 1: FundManagement 页面**

`src/pages/FundManagement.tsx`:
```typescript
import { useEffect, useState } from 'react'
import { useStore } from '../store/useStore'
import { api } from '../api/client'

export default function FundManagement() {
  const funds = useStore(s => s.funds)
  const fundsLoading = useStore(s => s.fundsLoading)
  const fetchFunds = useStore(s => s.fetchFunds)
  const recomputeFund = useStore(s => s.recomputeFund)
  const deleteFund = useStore(s => s.deleteFund)
  const [recomputing, setRecomputing] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [addForm, setAddForm] = useState({ fund_id: '', fund_name: '', apir_code: '', confirmed_url: '', fetch_method: 'pdf', url_type: '' })
  const [addError, setAddError] = useState('')

  useEffect(() => { fetchFunds() }, [])

  const handleRecompute = async (fundId: string) => {
    setRecomputing(fundId)
    try {
      await recomputeFund(fundId)
    } catch {}
    setRecomputing(null)
  }

  const handleDelete = async (fundId: string) => {
    try {
      await deleteFund(fundId)
    } catch {}
    setDeleteConfirm(null)
  }

  const handleAdd = async () => {
    setAddError('')
    try {
      await api.createFund({
        fund_id: addForm.fund_id,
        fund_name: addForm.fund_name,
        apir_code: addForm.apir_code || null,
        confirmed_url: addForm.confirmed_url,
        fetch_method: addForm.fetch_method,
        url_type: addForm.url_type,
      })
      setShowAdd(false)
      setAddForm({ fund_id: '', fund_name: '', apir_code: '', confirmed_url: '', fetch_method: 'pdf', url_type: '' })
      await fetchFunds()
    } catch (e: any) {
      setAddError(e.message)
    }
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-5">
        <h1 className="text-xl font-semibold">基金管理</h1>
        <button
          className="text-sm bg-[#1a1a2e] text-white px-4 py-2 rounded-lg hover:bg-[#2a2a4e]"
          onClick={() => setShowAdd(true)}
        >
          + 添加基金
        </button>
      </div>

      {fundsLoading && <div className="text-gray-400">加载中...</div>}

      <div className="bg-white rounded-lg shadow-sm overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b-2 border-gray-100 bg-gray-50">
              <th className="text-left py-3 px-4 text-gray-500 font-medium">基金 ID</th>
              <th className="text-left py-3 px-4 text-gray-500 font-medium">基金名称</th>
              <th className="text-left py-3 px-4 text-gray-500 font-medium">APIR</th>
              <th className="text-left py-3 px-4 text-gray-500 font-medium">数据截止</th>
              <th className="text-left py-3 px-4 text-gray-500 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {funds.map(f => (
              <tr key={f.fund_id} className="border-b border-gray-50 hover:bg-gray-50">
                <td className="py-3 px-4 text-gray-500 text-xs">{f.fund_id}</td>
                <td className="py-3 px-4 font-medium">{f.fund_name}</td>
                <td className="py-3 px-4 text-gray-500">{f.apir_code ?? '—'}</td>
                <td className="py-3 px-4 text-gray-500">{f.data_cutoff_month ?? '—'}</td>
                <td className="py-3 px-4">
                  <button
                    className="text-xs text-blue-600 border border-blue-200 rounded px-2.5 py-1 mr-2 hover:bg-blue-50 disabled:opacity-50"
                    disabled={recomputing === f.fund_id}
                    onClick={() => handleRecompute(f.fund_id)}
                  >
                    {recomputing === f.fund_id ? '计算中...' : '重算'}
                  </button>
                  {deleteConfirm === f.fund_id ? (
                    <span className="text-xs">
                      确认删除？
                      <button className="text-red-600 ml-1 mr-1" onClick={() => handleDelete(f.fund_id)}>是</button>
                      <button className="text-gray-500" onClick={() => setDeleteConfirm(null)}>否</button>
                    </span>
                  ) : (
                    <button className="text-xs text-red-500 border border-red-200 rounded px-2.5 py-1 hover:bg-red-50" onClick={() => setDeleteConfirm(f.fund_id)}>
                      删除
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {funds.length === 0 && !fundsLoading && (
              <tr><td colSpan={5} className="py-10 text-center text-gray-400">暂无基金数据，请先通过 skills 端添加基金</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 添加基金弹窗 */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-lg max-h-[80vh] overflow-y-auto">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-base font-medium">添加基金</h2>
              <button className="text-gray-400 text-xl" onClick={() => setShowAdd(false)}>&times;</button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-gray-500 block mb-1">fund_id *</label>
                <input className="w-full text-sm border border-gray-200 rounded px-3 py-2" value={addForm.fund_id} onChange={e => setAddForm({ ...addForm, fund_id: e.target.value })} placeholder="如 bentham_global_income_fund" />
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">基金名称 *</label>
                <input className="w-full text-sm border border-gray-200 rounded px-3 py-2" value={addForm.fund_name} onChange={e => setAddForm({ ...addForm, fund_name: e.target.value })} />
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">APIR 代码</label>
                <input className="w-full text-sm border border-gray-200 rounded px-3 py-2" value={addForm.apir_code} onChange={e => setAddForm({ ...addForm, apir_code: e.target.value })} placeholder="如 ETL5010AU" />
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">事实单 URL *</label>
                <input className="w-full text-sm border border-gray-200 rounded px-3 py-2" value={addForm.confirmed_url} onChange={e => setAddForm({ ...addForm, confirmed_url: e.target.value })} />
              </div>
              <div className="flex gap-3">
                <div className="flex-1">
                  <label className="text-xs text-gray-500 block mb-1">抓取方式 *</label>
                  <select className="w-full text-sm border border-gray-200 rounded px-3 py-2 bg-white" value={addForm.fetch_method} onChange={e => setAddForm({ ...addForm, fetch_method: e.target.value })}>
                    <option value="pdf">PDF</option>
                    <option value="html_plotly">HTML</option>
                  </select>
                </div>
                <div className="flex-1">
                  <label className="text-xs text-gray-500 block mb-1">URL 类型 *</label>
                  <input className="w-full text-sm border border-gray-200 rounded px-3 py-2" value={addForm.url_type} onChange={e => setAddForm({ ...addForm, url_type: e.target.value })} placeholder="如 factsheet" />
                </div>
              </div>
              {addError && <div className="text-xs text-red-500">{addError}</div>}
              <div className="text-xs text-gray-400">仅注册元信息，数据抓取需在 skills 端运行 /add_fixed_fund</div>
              <button className="w-full text-sm bg-[#1a1a2e] text-white py-2 rounded-lg hover:bg-[#2a2a4e]" onClick={handleAdd}>确认添加</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

---

### Task 7: 最终集成验证

**Files:**
- No new files. 验证全部页面路由、数据加载、交互是否正常。

- [ ] **Step 1: TypeScript 编译检查**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis/webapp/frontend
npx tsc -b --noEmit 2>&1
```

修复所有类型错误。

- [ ] **Step 2: Vite 构建**

```bash
npm run build
```

确认构建无报错。

- [ ] **Step 3: 验证页面路由**

确认以下路径能正常渲染（dev server 启动后）：
- `http://localhost:5173/` → 对比看板
- `http://localhost:5173/anomalies` → 异常审计
- `http://localhost:5173/funds` → 基金管理
