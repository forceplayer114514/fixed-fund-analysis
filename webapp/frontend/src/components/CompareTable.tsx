import { useMemo } from 'react'
import { useStore } from '../store/useStore'
import type { FundMetrics } from '../types'

function rankBy<T extends Record<string, any>>(
  items: T[],
  extract: (item: T) => number | null,
  asc = false
): Map<string, number> {
  const sorted = [...items].sort((a, b) => {
    const va = extract(a) ?? (asc ? Infinity : -Infinity)
    const vb = extract(b) ?? (asc ? Infinity : -Infinity)
    return asc ? va - vb : vb - va
  })
  const map = new Map<string, number>()
  sorted.forEach((item, i) => map.set(item.fund_id, i + 1))
  return map
}

function fmt(v: number | null, suffix = '', decimals = 2) {
  if (v == null) return '—'
  return `${(v * 100).toFixed(decimals)}${suffix}`
}

export default function CompareTable() {
  const compareData = useStore(s => s.compareData)
  const smoothingMode = useStore(s => s.smoothingMode)
  const funds = useStore(s => s.funds)
  const fundNameMap = useMemo(() => {
    const m = new Map<string, string>()
    funds.forEach(f => m.set(f.fund_id, f.fund_name))
    return m
  }, [funds])

  const rows = useMemo(() => {
    if (!compareData) return []
    const items = compareData.funds as (FundMetrics & { fund_id: string })[]

    const rankExcess = rankBy(items, m =>
      smoothingMode === 'original' ? m.orig_annualized_excess_return : m.un_annualized_excess_return
    )
    const rankDD = rankBy(
      items,
      m => (smoothingMode === 'original' ? m.orig_max_drawdown : m.un_max_drawdown)
    )
    const rankOmega = rankBy(items, m => {
      const v = smoothingMode === 'original' ? m.orig_omega_ratio : m.un_omega_ratio
      // Omega 为 null（原 inf，无跑输月=最优）应排首位，而非末名
      return v == null ? Infinity : v
    })
    const rankWin = rankBy(items, m =>
      smoothingMode === 'original' ? m.orig_excess_win_rate : m.un_excess_win_rate
    )

    return items.map(m => {
      const excess =
        smoothingMode === 'original'
          ? m.orig_annualized_excess_return
          : m.un_annualized_excess_return
      const dd =
        smoothingMode === 'original' ? m.orig_max_drawdown : m.un_max_drawdown
      const omega =
        smoothingMode === 'original' ? m.orig_omega_ratio : m.un_omega_ratio
      const winRate =
        smoothingMode === 'original' ? m.orig_excess_win_rate : m.un_excess_win_rate
      const run =
        smoothingMode === 'original'
          ? m.orig_max_underperform_months
          : m.un_max_underperform_months
      const vol =
        smoothingMode === 'original'
          ? m.orig_annualized_volatility
          : m.un_annualized_volatility

      return {
        fund_id: m.fund_id,
        excess: `${fmt(excess, '%')} (${rankExcess.get(m.fund_id)})`,
        dd: `${fmt(dd, '%')} (${rankDD.get(m.fund_id)})`,
        omega: `${omega == null ? '极佳' : omega.toFixed(2)} (${rankOmega.get(m.fund_id)})`,
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
                <td className="py-2.5 px-3 font-medium max-w-xs truncate" title={fundNameMap.get(r.fund_id) ?? r.fund_id}>{fundNameMap.get(r.fund_id) ?? r.fund_id}</td>
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
