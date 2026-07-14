import { create } from 'zustand'
import { api } from '../api/client'
import type { Fund, Anomaly, CompareResponse, TimeSeriesResponse } from '../types'

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
  /** 当前展示基金（Phase 1 过渡：卡片与"当前展示"标签同源，修复一致性 bug；
   *  null 时回退 selectedFundIds[0]。Phase 2 锚定机制将复用此字段） */
  displayFundId: string | null
  period: Period
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
  setDisplayFundId: (id: string | null) => void
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
  displayFundId: null,
  period: 'full',
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
        // 首次加载：默认全选
        selectedFundIds = funds.map(f => f.fund_id)
      } else {
        // 后续：保留已选（剔除已删除基金），新基金默认不选
        const validIds = new Set(funds.map(f => f.fund_id))
        selectedFundIds = prev.filter(id => validIds.has(id))
      }
      set({ funds, fundsLoading: false, selectedFundIds })
    } catch (e: unknown) {
      set({ fundsError: (e as Error).message, fundsLoading: false })
    }
  },

  fetchCompare: async () => {
    const { selectedFundIds, period } = get()
    if (selectedFundIds.length === 0) {
      set({ compareData: null, compareError: null })
      return
    }
    set({ compareLoading: true, compareError: null })
    try {
      const data = await api.compare(selectedFundIds, period)
      set({ compareData: data, compareLoading: false })
    } catch (e: unknown) {
      set({ compareLoading: false, compareError: (e as Error).message, compareData: null })
    }
  },

  fetchTimeSeries: async () => {
    const { selectedFundIds, period } = get()
    if (selectedFundIds.length === 0) {
      set({ timeSeriesData: null, timeSeriesError: null })
      return
    }
    set({ timeSeriesLoading: true, timeSeriesError: null })
    try {
      const data = await api.timeSeries(selectedFundIds, period)
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
      return { selectedFundIds: next }
    })
  },

  setDisplayFundId: (displayFundId: string | null) => { set({ displayFundId }) },
  setPeriod: (period: Period) => { set({ period }) },
  setChartMetric: (chartMetric: ChartMetric) => { set({ chartMetric }) },
  setSmoothingMode: (smoothingMode: SmoothingMode) => { set({ smoothingMode }) },

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
    }))
    // 刷新对比/时序（删除的基金已不在选中）
    if (get().selectedFundIds.length > 0) {
      get().fetchCompare()
      get().fetchTimeSeries()
    } else {
      set({ compareData: null, timeSeriesData: null })
    }
  },
}))
