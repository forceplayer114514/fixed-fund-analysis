import { useEffect } from 'react'
import { useStore } from '../store/useStore'
import FundChips from '../components/FundChips'
import MetricCard from '../components/MetricCard'
import NavChart from '../components/NavChart'
import CompareTable from '../components/CompareTable'
import type { FundMetrics } from '../types'

/** 小样本门禁阈值（PDD 1.5）：当前窗口有效样本月数 n < 24 时 IR/胜率不可靠。 */
const SMALL_SAMPLE_THRESHOLD = 24

function rankAmong<T extends FundMetrics>(
  items: T[],
  value: number | null | undefined,
  extract: (m: T) => number | null,
  eligible: (m: T) => boolean = () => true,
): number | undefined {
  if (value == null) return undefined
  const vals = items.filter(eligible).map(extract).filter((x): x is number => x != null)
  return vals.filter(x => x > value).length + 1
}

export default function Dashboard() {
  const funds = useStore(s => s.funds)
  const fundsLoading = useStore(s => s.fundsLoading)
  const fundsError = useStore(s => s.fundsError)
  const compareData = useStore(s => s.compareData)
  const compareError = useStore(s => s.compareError)
  const selectedFundIds = useStore(s => s.selectedFundIds)
  const displayFundId = useStore(s => s.displayFundId)
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
  }, [selectedFundIds, period, funds.length])

  // 当前展示基金（Phase 1 过渡）：displayFundId 仍选中则用之，否则回退 selectedFundIds[0]。
  // 卡片区与"当前展示"标签同源，修复一致性 bug（PDD 1.6）。Phase 2 锚定机制上线后替换。
  const effectiveDisplayId = (displayFundId && selectedFundIds.includes(displayFundId))
    ? displayFundId
    : (selectedFundIds[0] ?? null)
  const m: FundMetrics | undefined = effectiveDisplayId
    ? compareData?.funds?.find(x => x.fund_id === effectiveDisplayId)
    : undefined
  const allMetrics = compareData?.funds ?? []
  const isOrig = smoothingMode === 'original'

  const isSmallSample = (m == null) || (m.excess_sample_months ?? 0) < SMALL_SAMPLE_THRESHOLD
  const smallNote = m ? `样本不足(n=${m.excess_sample_months})，统计指标不可靠` : undefined
  const eligibleForStats = (x: FundMetrics) => (x.excess_sample_months ?? 0) >= SMALL_SAMPLE_THRESHOLD

  const excess = m ? (isOrig ? m.orig_annualized_excess_return : m.un_annualized_excess_return) : null
  const ir = m ? (isOrig ? m.orig_information_ratio : m.un_information_ratio) : null
  const winRate = m ? (isOrig ? m.orig_excess_win_rate : m.un_excess_win_rate) : null
  const dd = m ? (isOrig ? m.orig_max_drawdown : m.un_max_drawdown) : null
  const recoveryMonths = m ? (isOrig ? m.orig_recovery_months : m.un_recovery_months) : null
  const recovered = m ? (isOrig ? m.orig_dd_recovered : m.un_dd_recovered) : false

  // 恢复月数副文本（修正3 统一口径：卡片与表格一致）
  const recoverySubtext: string | undefined = (() => {
    if (!m) return undefined
    if (dd === 0 || recoveryMonths == null) return '无回撤'
    return recovered ? `恢复 ${recoveryMonths} 个月` : `未恢复(已 ${recoveryMonths} 个月)`
  })()

  if (fundsLoading) return <div className="text-gray-400">加载基金列表...</div>
  if (fundsError)
    return <div className="text-red-500">基金列表加载失败：{fundsError}</div>
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

      {compareError && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg p-4 mb-5">
          指标加载失败：{compareError}
        </div>
      )}

      {m && (
        <div className="text-sm text-gray-500 mb-3">
          当前展示：<span className="font-medium text-gray-800">{m.fund_name ?? effectiveDisplayId ?? '-'}</span>
          <span className="text-gray-400 ml-2">（{m.history_months} 个月历史）</span>
        </div>
      )}

      <div className="flex gap-3 mb-6 flex-wrap">
        <MetricCard
          label="年化超额收益"
          value={excess != null ? `${(excess * 100).toFixed(2)}%` : '-'}
          rank={rankAmong(allMetrics, excess, x =>
            isOrig ? x.orig_annualized_excess_return : x.un_annualized_excess_return
          )}
        />
        <MetricCard
          label="信息比率"
          value={ir != null ? ir.toFixed(2) : '-'}
          rank={isSmallSample ? undefined : rankAmong(allMetrics, ir,
            x => (isOrig ? x.orig_information_ratio : x.un_information_ratio), eligibleForStats)}
          warn={isSmallSample}
          warnNote={isSmallSample ? smallNote : undefined}
        />
        <MetricCard
          label="超额胜率"
          value={winRate != null ? `${(winRate * 100).toFixed(1)}%` : '-'}
          rank={isSmallSample ? undefined : rankAmong(allMetrics, winRate,
            x => (isOrig ? x.orig_excess_win_rate : x.un_excess_win_rate), eligibleForStats)}
          warn={isSmallSample}
          warnNote={isSmallSample ? smallNote : undefined}
        />
        <MetricCard
          label="最大回撤"
          value={dd != null ? `${(dd * 100).toFixed(2)}%` : '-'}
          rank={rankAmong(allMetrics, dd, x =>
            isOrig ? x.orig_max_drawdown : x.un_max_drawdown
          )}
          subtext={recoverySubtext}
        />
      </div>

      <NavChart />
      <CompareTable />
    </div>
  )
}
