import { useEffect } from 'react'
import { useStore } from '../store/useStore'
import FundChips from '../components/FundChips'
import MetricCard from '../components/MetricCard'
import NavChart from '../components/NavChart'
import CompareTable from '../components/CompareTable'
import type { FundMetrics } from '../types'

function rankAmong(
  items: FundMetrics[],
  value: number | null,
  extract: (m: FundMetrics) => number | null,
  asc = false
): number | undefined {
  if (value == null) return undefined
  const vals = items.map(extract).filter((x): x is number => x != null)
  if (asc) return vals.filter(x => x < value).length + 1
  return vals.filter(x => x > value).length + 1
}

export default function Dashboard() {
  const funds = useStore(s => s.funds)
  const fundsLoading = useStore(s => s.fundsLoading)
  const compareData = useStore(s => s.compareData)
  const selectedFundIds = useStore(s => s.selectedFundIds)
  const period = useStore(s => s.period)
  const smoothingMode = useStore(s => s.smoothingMode)
  const setPeriod = useStore(s => s.setPeriod)
  const setSmoothingMode = useStore(s => s.setSmoothingMode)
  const fetchFunds = useStore(s => s.fetchFunds)
  const fetchCompare = useStore(s => s.fetchCompare)
  const fetchTimeSeries = useStore(s => s.fetchTimeSeries)

  useEffect(() => {
    fetchFunds()
  }, [])

  useEffect(() => {
    if (funds.length > 0) {
      fetchCompare()
      fetchTimeSeries()
    }
  }, [selectedFundIds, period])

  // 取第一支选中基金的指标显示在卡片
  const firstId = selectedFundIds[0]
  const firstMetrics: FundMetrics | undefined = firstId
    ? compareData?.funds?.find(m => m.fund_id === firstId)
    : compareData?.funds?.[0]
  const allMetrics = compareData?.funds ?? []

  const excess = firstMetrics
    ? (smoothingMode === 'original'
        ? firstMetrics.orig_annualized_excess_return
        : firstMetrics.un_annualized_excess_return)
    : null
  const dd = firstMetrics
    ? (smoothingMode === 'original' ? firstMetrics.orig_max_drawdown : firstMetrics.un_max_drawdown)
    : null
  const omega = firstMetrics
    ? (smoothingMode === 'original' ? firstMetrics.orig_omega_ratio : firstMetrics.un_omega_ratio)
    : null
  const winRate = firstMetrics
    ? (smoothingMode === 'original'
        ? firstMetrics.orig_excess_win_rate
        : firstMetrics.un_excess_win_rate)
    : null

  if (fundsLoading) return <div className="text-gray-400">加载基金列表...</div>
  if (funds.length === 0)
    return <div className="text-gray-400">暂无基金数据，请先通过 skills 端添加基金</div>

  return (
    <div>
      <div className="flex justify-between items-center mb-5 flex-wrap gap-3">
        <h1 className="text-xl font-semibold">对比看板</h1>
        <div className="flex gap-2">
          <select
            className="text-sm border border-gray-200 rounded px-3 py-1.5 bg-white"
            value={period}
            onChange={e => setPeriod(e.target.value as any)}
          >
            <option value="full">全部区间</option>
            <option value="3y">近3年</option>
            <option value="1y">近1年</option>
            <option value="common">共同区间</option>
          </select>
          <select
            className="text-sm border border-gray-200 rounded px-3 py-1.5 bg-white"
            value={smoothingMode}
            onChange={e => setSmoothingMode(e.target.value as any)}
          >
            <option value="original">原始</option>
            <option value="unsmoothed">去平滑</option>
          </select>
        </div>
      </div>

      <FundChips />

      <div className="flex gap-3 mb-6 flex-wrap">
        <MetricCard
          label="年化超额收益"
          value={excess != null ? `${(excess * 100).toFixed(2)}%` : '—'}
          rank={rankAmong(allMetrics, excess, m =>
            smoothingMode === 'original'
              ? m.orig_annualized_excess_return
              : m.un_annualized_excess_return
          )}
        />
        <MetricCard
          label="最大回撤"
          value={dd != null ? `${(dd * 100).toFixed(2)}%` : '—'}
          rank={rankAmong(allMetrics, dd, m =>
            smoothingMode === 'original' ? m.orig_max_drawdown : m.un_max_drawdown
          )}
        />
        <MetricCard
          label="Omega 比率"
          value={omega?.toFixed(2) ?? '—'}
          rank={rankAmong(allMetrics, omega, m =>
            smoothingMode === 'original' ? m.orig_omega_ratio : m.un_omega_ratio
          )}
        />
        <MetricCard
          label="超额胜率"
          value={winRate != null ? `${(winRate * 100).toFixed(1)}%` : '—'}
          rank={rankAmong(allMetrics, winRate, m =>
            smoothingMode === 'original'
              ? m.orig_excess_win_rate
              : m.un_excess_win_rate
          )}
        />
      </div>

      <NavChart />
      <CompareTable />
    </div>
  )
}
