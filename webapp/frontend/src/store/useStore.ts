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
      set({
        funds,
        fundsLoading: false,
        selectedFundIds: funds.map(f => f.fund_id),
      })
    } catch (e: unknown) {
      set({ fundsError: (e as Error).message, fundsLoading: false })
    }
  },

  fetchCompare: async () => {
    const { selectedFundIds, period } = get()
    if (selectedFundIds.length === 0) return
    set({ compareLoading: true })
    try {
      const data = await api.compare(selectedFundIds, period)
      set({ compareData: data, compareLoading: false })
    } catch {
      set({ compareLoading: false })
    }
  },

  fetchTimeSeries: async () => {
    const { selectedFundIds, period } = get()
    if (selectedFundIds.length === 0) return
    set({ timeSeriesLoading: true })
    try {
      const data = await api.timeSeries(selectedFundIds, period)
      set({ timeSeriesData: data, timeSeriesLoading: false })
    } catch {
      set({ timeSeriesLoading: false })
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
