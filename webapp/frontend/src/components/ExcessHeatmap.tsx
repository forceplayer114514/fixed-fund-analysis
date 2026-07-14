import { useMemo } from 'react'
import { useStore } from '../store/useStore'
import { monthlyExcess, monthlyBench, type FundReturns } from '../lib/rebase'

/** 月度超额热力图（PDD 2.6）：仅锚定时渲染于 CompareTable 下方。
 *  单元格 e_t = r_fund − monthlyBench(rba)（与 Phase 1 管道口径一致）；缺月灰格；发散色标 0 中心。 */
export default function ExcessHeatmap() {
  const timeSeriesData = useStore(s => s.timeSeriesData)
  const anchorFundId = useStore(s => s.anchorFundId)
  const smoothingMode = useStore(s => s.smoothingMode)

  const rows = useMemo(() => {
    if (!anchorFundId || !timeSeriesData) return null
    const anchorSeries = timeSeriesData.series.find(s => s.fund_id === anchorFundId)
    if (!anchorSeries) return null
    const isOrig = smoothingMode === 'original'
    const fund: FundReturns = {
      fund_id: anchorSeries.fund_id, fund_name: anchorSeries.fund_name,
      dates: anchorSeries.dates,
      returns: isOrig ? anchorSeries.returns : (anchorSeries.unsm_returns ?? anchorSeries.returns),
    }
    const me = monthlyExcess(fund, timeSeriesData.months, timeSeriesData.rba)
    const years = [...new Set(me.map(p => p.year))].sort((a, b) => b - a) // 倒序，最新在上
    const byKey = new Map<string, typeof me[number]>()
    me.forEach(p => byKey.set(`${p.year}-${p.monthNum}`, p))
    const m = Math.max(
      ...me.filter(p => p.excess != null).map(p => Math.abs(p.excess as number)), 0.0001,
    )
    const cellColor = (e: number | null): string => {
      if (e == null) return '#f0f0f0' // 缺月灰格（禁插值填色）
      const a = Math.min(Math.abs(e) / m, 1)
      return e >= 0 ? `rgba(39,174,96,${a})` : `rgba(192,57,43,${a})`
    }
    return { years, byKey, cellColor }
  }, [timeSeriesData, anchorFundId, smoothingMode])

  if (!rows || !anchorFundId) return null
  const months = Array.from({ length: 12 }, (_, i) => i + 1)
  const fundName = timeSeriesData?.series.find(s => s.fund_id === anchorFundId)?.fund_name ?? anchorFundId

  return (
    <div className="bg-white rounded-lg p-5 shadow-sm mb-5">
      <h3 className="text-sm text-gray-400 mb-3">
        月度超额热力图 · <span className="text-gray-700 font-medium">{fundName}</span>
      </h3>
      <div className="overflow-x-auto">
        <table className="text-xs border-collapse">
          <thead>
            <tr>
              <th className="py-1.5 px-2 text-gray-500 font-medium text-left">年</th>
              {months.map(mn => (
                <th key={mn} className="py-1.5 px-2 text-gray-500 font-medium w-12 text-center">{mn}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.years.map(y => (
              <tr key={y}>
                <td className="py-1 px-2 text-gray-600 font-medium">{y}</td>
                {months.map(mn => {
                  const p = rows.byKey.get(`${y}-${mn}`)
                  const e = p?.excess ?? null
                  const title = e == null
                    ? `${y}-${String(mn).padStart(2, '0')}：无数据`
                    : `${y}-${String(mn).padStart(2, '0')}\n基金月收益: ${((p!.fundReturn as number) * 100).toFixed(3)}%\n基准月收益: ${((monthlyBench(p!.rbaRate as number)) * 100).toFixed(3)}%\n超额: ${(e * 100).toFixed(3)}%`
                  return (
                    <td key={mn} className="p-0.5">
                      <div
                        className="w-12 h-7 rounded-sm border border-white"
                        style={{ backgroundColor: rows.cellColor(e) }}
                        title={title}
                      />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-xs text-gray-400 mt-2">
        色标：绿=正超额、红=负超额、灰=无数据；单元格 hover 见原始月收益/基准/超额。兼数据质检视图。
      </div>
    </div>
  )
}
