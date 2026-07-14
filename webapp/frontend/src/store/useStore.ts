import { create } from 'zustand'
import { api } from '../api/client'
import type { Fund, Anomaly, CompareResponse, TimeSeriesResponse } from '../types'

export type Period = 'full' | '3y' | '1y' | 'common'
/** 图表类型：累计 NAV | 滚动12月超额（删月收益率折线，PDD 2.7） */
export type ChartMetric = 'nav' | 'rolling_excess'
export type SmoothingMode = 'original' | 'unsmoothed'

interface AppState {
  // 基金列表
  funds: Fund[]
  fundsLoading: boolean
  fundsError: string | null

  // 选中
  selectedFundIds: string[]
  period: Period
  /** 锚定基金（Phase 2 状态机 C）：非 null 时 period 置灰、图表拼接 rebase、卡片/热力图联动 */
  anchorFundId: string | null
  /** 进入锚定前的 period，取消锚定时恢复 */
  prevPeriod: Period
  chartMetric: ChartMetric
  smoothingMode: SmoothingMode

  // 数据
  compareData: CompareResponse | null
  compareLoading: boolean
  compareError: string | null
  timeSeriesData: TimeSeriesResponse | null
  timeSeriesLoading: boolean
  timeSeriesError: string | null
  anomalies: Anomaly[]
  anomaliesLoading: boolean

  // 操作
  fetchFunds: () => Promise<void>
  fetchCompare: () => Promise<void>
  fetchTimeSeries: () => Promise<void>
  fetchAnomalies: () => Promise<void>
  toggleFund: (fundId: string) => void
  setPeriod: (period: Period) => void
  setAnchor: (id: string | null) => void
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
  anchorFundId: null,
  prevPeriod: 'full',
  chartMetric: 'nav',
  smoothingMode: 'original',

  compareData: null,
  compareLoading: false,
  compareError: null,
  timeSeriesData: null,
  timeSeriesLoading: false,
  timeSeriesError: null,
  anomalies: [],
  anomaliesLoading: false,

  fetchFunds: async () => {
    set({ fundsLoading: true, fundsError: null })
    try {
      const funds = await api.listFunds()
      const prev = get().selectedFundIds
      let selectedFundIds: string[]
      if (prev.length === 0) {
        selectedFundIds = funds.map(f => f.fund_id)
      } else {
        const validIds = new Set(funds.map(f => f.fund_id))
        selectedFundIds = prev.filter(id => validIds.has(id))
      }
      // 锚定基金若被删除/取消选中，清除锚定（状态机 1.2）
      const anchor = get().anchorFundId
      const nextAnchor = anchor && selectedFundIds.includes(anchor) ? anchor : null
      set({ funds, fundsLoading: false, selectedFundIds,
            anchorFundId: nextAnchor })
    } catch (e: unknown) {
      set({ fundsError: (e as Error).message, fundsLoading: false })
    }
  },

  fetchCompare: async () => {
    const { selectedFundIds, period, anchorFundId } = get()
    if (selectedFundIds.length === 0) {
      set({ compareData: null, compareError: null })
      return
    }
    // 修正A：锚定模式下 compare 用 full（卡片/表格/图表同口径=锚定完整历史）
    const effPeriod = anchorFundId ? 'full' : period
    set({ compareLoading: true, compareError: null })
    try {
      const data = await api.compare(selectedFundIds, effPeriod)
      set({ compareData: data, compareLoading: false })
    } catch (e: unknown) {
      set({ compareLoading: false, compareError: (e as Error).message, compareData: null })
    }
  },

  fetchTimeSeries: async () => {
    const { selectedFundIds } = get()
    if (selectedFundIds.length === 0) {
      set({ timeSeriesData: null, timeSeriesError: null })
      return
    }
    set({ timeSeriesLoading: true, timeSeriesError: null })
    try {
      // 方案a：永远 full，period/anchor 切换纯前端重算（滚动超额需窗口前数据）
      const data = await api.timeSeries(selectedFundIds, 'full')
      set({ timeSeriesData: data, timeSeriesLoading: false })
    } catch (e: unknown) {
      set({ timeSeriesLoading: false, timeSeriesError: (e as Error).message, timeSeriesData: null })
    }
  },

  fetchAnomalies: async () => {
    set({ anomaliesLoading: true })
    try {
      const data = await api.listAnomalies()
      set({ anomalies: data, anomaliesLoading: false })
    } catch {
      set({ anomaliesLoading: false })
    }
  },

  toggleFund: (fundId: string) => {
    set(state => {
      const exists = state.selectedFundIds.includes(fundId)
      const next = exists
        ? state.selectedFundIds.filter(id => id !== fundId)
        : [...state.selectedFundIds, fundId]
      // 取消选中锚定基金 -> 清除锚定（状态机 1.2）
      const anchor = state.anchorFundId
      const nextAnchor = anchor && next.includes(anchor) ? anchor : null
      return { selectedFundIds: next, anchorFundId: nextAnchor }
    })
  },

  setPeriod: (period) => { set({ period }) },

  setAnchor: (id) => set(state => {
    if (id === null) return { anchorFundId: null }              // 显式清除
    if (state.anchorFundId === id) return { anchorFundId: null } // 再次点击同一曲线 -> 取消锚定
    const entering = state.anchorFundId === null                // 首次进入 C：存 prevPeriod
    return {
      anchorFundId: id,
      prevPeriod: entering ? state.period : state.prevPeriod,
    }
  }),

  setChartMetric: (chartMetric) => { set({ chartMetric }) },
  setSmoothingMode: (smoothingMode) => { set({ smoothingMode }) },

  patchMonthlyReturn: async (id: number, netReturn: number) => {
    await api.patchMonthlyReturn(id, { net_return: netReturn })
    await Promise.all([
      get().fetchAnomalies(),
      get().fetchCompare(),
      get().fetchTimeSeries(),
    ])
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
      anchorFundId: state.anchorFundId === fundId ? null : state.anchorFundId,
    }))
    if (get().selectedFundIds.length > 0) {
      get().fetchCompare()
      get().fetchTimeSeries()
    } else {
      set({ compareData: null, timeSeriesData: null })
    }
  },
}))
