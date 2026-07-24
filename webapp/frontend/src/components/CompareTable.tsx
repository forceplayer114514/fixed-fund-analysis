import { useMemo } from 'react'
import { useStore } from '../store/useStore'
import type { FundMetrics } from '../types'
import WarnBadge from './WarnBadge'
import { buildShortCodeMap } from '../lib/fundCodes'

/** 小样本门禁阈值（PDD 1.5）：n < 24 时 IR/胜率名次置灰 + 角标。 */
const SMALL_SAMPLE_THRESHOLD = 24

function rankBy<T extends FundMetrics>(
  items: T[],
  extract: (item: T) => number | null,
  eligible: (item: T) => boolean = () => true,
  asc = false,
): Map<string, number> {
  // 仅 eligible 项参与 1..k 排序；非 eligible（样本不足）项不占用名次序号（PDD 1.5）。
  const eligibleItems = items.filter(eligible)
  const sorted = [...eligibleItems].sort((a, b) => {
    const va = extract(a) ?? (asc ? Infinity : -Infinity)
    const vb = extract(b) ?? (asc ? Infinity : -Infinity)
    return asc ? va - vb : vb - va
  })
  const map = new Map<string, number>()
  sorted.forEach((item, i) => map.set(item.fund_id, i + 1))
  return map
}

function fmt(v: number | null, suffix = '', decimals = 2) {
  if (v == null) return '-'
  return `${(v * 100).toFixed(decimals)}${suffix}`
}

export default function CompareTable() {
  const compareData = useStore(s => s.compareData)
  const smoothingMode = useStore(s => s.smoothingMode)
  const funds = useStore(s => s.funds)
  const period = useStore(s => s.period)
  const anchorFundId = useStore(s => s.anchorFundId)
  const timeSeriesData = useStore(s => s.timeSeriesData)
  const setAnchor = useStore(s => s.setAnchor)
  const fundNameMap = useMemo(() => {
    const m = new Map<string, string>()
    funds.forEach(f => m.set(f.fund_id, f.fund_name))
    return m
  }, [funds])
  const codeMap = useMemo(() => buildShortCodeMap(funds), [funds])

  const rows = useMemo(() => {
    if (!compareData) return []
    const items = compareData.funds as FundMetrics[]
    const isOrig = smoothingMode === 'original'
    const statEligible = (x: FundMetrics) => (x.excess_sample_months ?? 0) >= SMALL_SAMPLE_THRESHOLD

    const rankExcess = rankBy(items, m =>
      isOrig ? m.orig_annualized_excess_return : m.un_annualized_excess_return)
    const rankSharpe = rankBy(items, m =>
      isOrig ? m.orig_sharpe_ratio : m.un_sharpe_ratio, statEligible)
    const rankDD = rankBy(items, m =>
      isOrig ? m.orig_max_drawdown : m.un_max_drawdown)
    const rankWin = rankBy(items, m =>
      isOrig ? m.orig_excess_win_rate : m.un_excess_win_rate, statEligible)

    return items.map(m => {
      const isOrigRow = isOrig
      const dd = isOrigRow ? m.orig_max_drawdown : m.un_max_drawdown
      const recoveryMonths = isOrigRow ? m.orig_recovery_months : m.un_recovery_months
      const recovered = isOrigRow ? m.orig_dd_recovered : m.un_dd_recovered
      // 恢复月数标签（修正3 统一口径：与卡片一致）
      let recoveryLabel: string
      if (dd === 0 || recoveryMonths == null) recoveryLabel = '无回撤'
      else recoveryLabel = recovered
        ? `恢复${recoveryMonths}个月`
        : `未恢复(已${recoveryMonths}个月)`

      return {
        fund_id: m.fund_id,
        annReturn: fmt(isOrigRow ? m.orig_annualized_return : m.un_annualized_return, '%'),
        excess: fmt(isOrigRow ? m.orig_annualized_excess_return : m.un_annualized_excess_return, '%'),
        excessRank: rankExcess.get(m.fund_id),
        sharpe: isOrigRow ? m.orig_sharpe_ratio : m.un_sharpe_ratio,
        sharpeRank: rankSharpe.get(m.fund_id),
        dd: fmt(dd, '%'),
        ddRank: rankDD.get(m.fund_id),
        recoveryLabel,
        winRate: fmt(isOrigRow ? m.orig_excess_win_rate : m.un_excess_win_rate, '%'),
        winRank: rankWin.get(m.fund_id),
        run: `${isOrigRow ? m.orig_max_underperform_months : m.un_max_underperform_months} 个月`,
        vol: fmt(isOrigRow ? m.orig_annualized_volatility : m.un_annualized_volatility, '%'),
        small: (m.excess_sample_months ?? 0) < SMALL_SAMPLE_THRESHOLD,
        n: m.excess_sample_months ?? 0,
      }
    })
  }, [compareData, smoothingMode])

  // F5：窗口说明（共同区间/1y/3y 显示起止+n；full 或锚定显示对应口径），消除满屏⚠困惑
  const windowNote = useMemo(() => {
    if (anchorFundId) {
      // 指标窗口起点=锚定基金自身首月（与图表 rebase 同口径），各基金终点各自最新月份
      const startMonth = timeSeriesData?.series
        .find(s => s.fund_id === anchorFundId)?.dates[0]?.slice(0, 7)
      return startMonth ? `锚定窗口: ${startMonth} 起 · 各基金至自身最新月份` : '锚定模式'
    }
    const items = compareData?.funds ?? []
    if (period === 'full' || items.length === 0) return '全部区间'
    const endYM = items[0].date_period
    const n = items[0].excess_sample_months ?? 0
    if (!endYM || n === 0) return ''
    const [y, m] = endYM.split('-').map(Number)
    const startIdx = (y * 12 + (m - 1)) - (n - 1)
    const sy = Math.floor(startIdx / 12)
    const sm = (startIdx % 12) + 1
    const startYM = `${sy}-${String(sm).padStart(2, '0')}`
    return `当前窗口: ${startYM} 至 ${endYM} (n=${n})`
  }, [period, anchorFundId, compareData, timeSeriesData])

  if (rows.length === 0) return null

  return (
    <div className="bg-white rounded-lg p-5 shadow-sm">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-sm text-gray-400">指标对比</h3>
        {windowNote && <span className="text-xs text-gray-400">{windowNote}</span>}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b-2 border-gray-100">
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">基金名称</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">年化收益率</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">年化超额收益</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">夏普比率（超额）</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">最大回撤</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">超额胜率</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">最长跑输</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">年化波动率</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const isAnchor = anchorFundId === r.fund_id
              return (
              <tr
                key={r.fund_id}
                onClick={() => setAnchor(r.fund_id)}
                title={isAnchor ? '再次点击取消锚定' : '点击锚定该基金（同点击曲线）'}
                className={`border-b border-gray-50 cursor-pointer ${
                  isAnchor ? 'bg-blue-50 hover:bg-blue-100' : 'hover:bg-gray-50'
                }`}
              >
                <td className="py-2.5 px-3 font-medium max-w-xs truncate" title={fundNameMap.get(r.fund_id) ?? r.fund_id}>
                  {isAnchor && <span className="text-blue-500 mr-1">●</span>}
                  {fundNameMap.get(r.fund_id) ?? r.fund_id}
                  <span className="ml-1.5 text-xs text-gray-400 font-normal">{codeMap.get(r.fund_id)}</span>
                </td>
                {/* 年化收益率：无名次括号（PDD 1.3） */}
                <td className="py-2.5 px-3">{r.annReturn}</td>
                <td className="py-2.5 px-3">{r.excess} ({r.excessRank})</td>
                <td className="py-2.5 px-3">
                  {r.sharpe == null ? '-' : r.sharpe.toFixed(2)}
                  {r.sharpeRank != null && <span className="text-xs text-gray-400 ml-1">({r.sharpeRank})</span>}
                  {r.small && <WarnBadge note={`样本不足(n=${r.n})，统计指标不可靠`} />}
                </td>
                <td className="py-2.5 px-3">
                  {r.dd} ({r.ddRank}) <span className="text-xs text-gray-400">· {r.recoveryLabel}</span>
                </td>
                <td className="py-2.5 px-3">
                  {r.winRate}
                  {r.winRank != null && <span className="text-xs text-gray-400 ml-1">({r.winRank})</span>}
                  {r.small && <WarnBadge note={`样本不足(n=${r.n})，统计指标不可靠`} />}
                </td>
                <td className="py-2.5 px-3">{r.run}</td>
                <td className="py-2.5 px-3">{r.vol}</td>
              </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="text-xs text-gray-400 mt-2">点击行锚定该基金（与点击曲线等效）· 再次点击同一行取消锚定</div>
    </div>
  )
}
