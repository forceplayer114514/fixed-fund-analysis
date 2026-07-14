import { useEffect } from 'react'
import { useStore } from '../store/useStore'
import FundChips from '../components/FundChips'
import MetricCard from '../components/MetricCard'
import NavChart from '../components/NavChart'
import CompareTable from '../components/CompareTable'
import ExcessHeatmap from '../components/ExcessHeatmap'
import type { FundMetrics } from '../types'

/** 小样本门禁阈值（PDD 1.5）：当前窗口有效样本月数 n < 24 时 IR/胜率不可靠。 */
const SMALL_SAMPLE_THRESHOLD = 24

function rankAmong(
  items: FundMetrics[],
  value: number | null | undefined,
  extract: (m: FundMetrics) => number | null,
  eligible: (m: FundMetrics) => boolean = () => true,
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
  const period = useStore(s => s.period)
  const anchorFundId = useStore(s => s.anchorFundId)
  const smoothingMode = useStore(s => s.smoothingMode)
  const setPeriod = useStore(s => s.setPeriod)
  const setSmoothingMode = useStore(s => s.setSmoothingMode)
  const fetchFunds = useStore(s => s.fetchFunds)
  const fetchCompare = useStore(s => s.fetchCompare)
  const fetchTimeSeries = useStore(s => s.fetchTimeSeries)

  useEffect(() => { fetchFunds() }, [])
  // 方案a：time-series 仅在选中集变化时 refetch（恒 full）；period/anchor 切换纯前端重算
  useEffect(() => { if (funds.length > 0) fetchTimeSeries() }, [selectedFundIds, funds.length])
  // compare：选中集 / period / 锚定变化时 refetch；锚定时用 full（修正A，卡片表格图表同口径）
  useEffect(() => { if (funds.length > 0) fetchCompare() }, [selectedFundIds, period, anchorFundId, funds.length])

  const allMetrics = compareData?.funds ?? []
  // 卡片联动锚定基金（PDD 2.3）；无锚定时占位
  const m: FundMetrics | undefined = anchorFundId
    ? compareData?.funds?.find(x => x.fund_id === anchorFundId)
    : undefined
  const isOrig = smoothingMode === 'original'
  const isSmallSample = !m || (m.excess_sample_months ?? 0) < SMALL_SAMPLE_THRESHOLD
  const smallNote = m ? `样本不足(n=${m.excess_sample_months})，统计指标不可靠` : undefined
  const eligibleForStats = (x: FundMetrics) => (x.excess_sample_months ?? 0) >= SMALL_SAMPLE_THRESHOLD

  const excess = m ? (isOrig ? m.orig_annualized_excess_return : m.un_annualized_excess_return) : null
  const ir = m ? (isOrig ? m.orig_information_ratio : m.un_information_ratio) : null
  const winRate = m ? (isOrig ? m.orig_excess_win_rate : m.un_excess_win_rate) : null
  const dd = m ? (isOrig ? m.orig_max_drawdown : m.un_max_drawdown) : null
  const recoveryMonths = m ? (isOrig ? m.orig_recovery_months : m.un_recovery_months) : null
  const recovered = m ? (isOrig ? m.orig_dd_recovered : m.un_dd_recovered) : false
  const recoverySubtext: string | undefined = (() => {
    if (!m) return undefined
    if (dd === 0 || recoveryMonths == null) return '无回撤'
    return recovered ? `恢复 ${recoveryMonths} 个月` : `未恢复(已 ${recoveryMonths} 个月)`
  })()

  if (fundsLoading) return <div className="text-gray-400">加载基金列表...</div>
  if (fundsError) return <div className="text-red-500">基金列表加载失败：{fundsError}</div>
  if (funds.length === 0) return <div className="text-gray-400">暂无基金数据，请先通过 skills 端添加基金</div>

  return (
    <div>
      <div className="flex justify-between items-center mb-5 flex-wrap gap-3">
        <h1 className="text-xl font-semibold">对比看板</h1>
        <div className="flex gap-2">
          <select
            className="text-sm border border-gray-200 rounded px-3 py-1.5 bg-white disabled:bg-gray-100 disabled:text-gray-400"
            value={period}
            disabled={!!anchorFundId}
            title={anchorFundId ? '锚定模式下展示锚定基金完整历史' : undefined}
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

      {m ? (
        <div className="text-sm text-gray-500 mb-3">
          当前展示：<span className="font-medium text-gray-800">{m.fund_name ?? anchorFundId ?? '-'}</span>
          <span className="text-gray-400 ml-2">（{m.history_months} 个月历史）</span>
        </div>
      ) : (
        <div className="text-sm text-gray-400 mb-3">点击曲线锚定基金查看详情</div>
      )}

      {m ? (
        <div className="flex gap-3 mb-6 flex-wrap">
          <MetricCard
            label="年化超额收益"
            value={excess != null ? `${(excess * 100).toFixed(2)}%` : '-'}
            rank={rankAmong(allMetrics, excess, x =>
              isOrig ? x.orig_annualized_excess_return : x.un_annualized_excess_return)}
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
            rank={rankAmong(allMetrics, dd, x => isOrig ? x.orig_max_drawdown : x.un_max_drawdown)}
            subtext={recoverySubtext}
          />
        </div>
      ) : (
        <div className="bg-gray-50 border border-dashed border-gray-200 rounded-lg p-6 mb-6 text-center text-sm text-gray-400">
          点击下方曲线锚定基金，查看其完整历史指标卡片与月度超额热力图
        </div>
      )}

      <NavChart />
      <CompareTable />
      <ExcessHeatmap />
    </div>
  )
}
