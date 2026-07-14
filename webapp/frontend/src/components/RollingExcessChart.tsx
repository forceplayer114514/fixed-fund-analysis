import { useMemo } from 'react'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, DataZoomComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useStore, type Period } from '../store/useStore'
import type { FundReturns } from '../lib/rebase'
import { rollingExcess } from '../lib/rebase'

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, DataZoomComponent, CanvasRenderer])

const COLORS = ['#4fc3f7', '#7c4dff', '#ff7043', '#66bb6a', '#ffca28', '#ec407a', '#26c6da', '#ab47bc']

/** 与 NavChart 同口径算显示月份轴 */
function computeAxisMonths(months: string[], funds: FundReturns[], period: Period, anchorFundId: string | null): string[] {
  if (anchorFundId) {
    const anchor = funds.find(f => f.fund_id === anchorFundId)
    if (!anchor) return months
    const tA = anchor.dates[0].slice(0, 7)
    const end = funds.map(f => f.dates[f.dates.length - 1].slice(0, 7)).sort().pop()!
    return months.filter(m => m >= tA && m <= end)
  }
  if (period === 'full') return months
  if (period === '1y') return months.slice(-12)
  if (period === '3y') return months.slice(-36)
  const sets = funds.map(f => new Set(f.dates.map(d => d.slice(0, 7))))
  let common = sets[0] ?? new Set<string>()
  for (const s of sets.slice(1)) common = new Set([...common].filter(m => s.has(m)))
  return months.filter(m => common.has(m))
}

export default function RollingExcessChart() {
  const timeSeriesData = useStore(s => s.timeSeriesData)
  const selectedFundIds = useStore(s => s.selectedFundIds)
  const period = useStore(s => s.period)
  const anchorFundId = useStore(s => s.anchorFundId)
  const smoothingMode = useStore(s => s.smoothingMode)

  const option = useMemo(() => {
    if (!timeSeriesData || timeSeriesData.series.length === 0) return null
    const isOrig = smoothingMode === 'original'
    const funds: FundReturns[] = timeSeriesData.series
      .filter(s => selectedFundIds.includes(s.fund_id))
      .map(s => ({
        fund_id: s.fund_id, fund_name: s.fund_name, dates: s.dates,
        returns: (isOrig ? s.returns : (s.unsm_returns ?? s.returns)),
      }))
    if (funds.length === 0) return null

    const fullMonths = timeSeriesData.months
    const fullRba = timeSeriesData.rba
    const axisMonths = computeAxisMonths(fullMonths, funds, period, anchorFundId)
    const idxByMonth = new Map<string, number>()
    fullMonths.forEach((m, i) => idxByMonth.set(m, i))

    const series = funds.map((f, i) => {
      // 方案a：在 full 序列上算滚动超额，再按显示窗口裁剪 -> 近1年曲线完整
      const reFull = rollingExcess(f, fullMonths, fullRba)
      const short = f.dates.length < 12
      const data = axisMonths.map(m => {
        const idx = idxByMonth.get(m)
        return idx != null ? reFull[idx] : null
      })
      return {
        id: `roll:${f.fund_id}`,
        name: short ? `${f.fund_name}（历史不足12个月）` : f.fund_name,
        type: 'line' as const,
        data,
        connectNulls: false,
        symbol: 'none',
        smooth: false,
        lineStyle: { width: 1.5, opacity: short ? 0 : 1 },
        itemStyle: { color: COLORS[i % COLORS.length] },
        markLine: i === 0 ? {
          symbol: 'none',
          data: [{ yAxis: 0 }],
          lineStyle: { color: '#bbb', type: 'dashed' },
          label: { show: false },
        } : undefined,
      }
    })

    const vals = series.flatMap(s => s.data.filter((v): v is number => v != null))
    const yMin = vals.length ? Math.min(...vals) : -0.01
    const yMax = vals.length ? Math.max(...vals) : 0.01

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: any[]) => {
          if (!params || params.length === 0) return ''
          const date = params[0]?.axisValue ?? ''
          const lines = params
            .filter((p: any) => p.data != null)
            .sort((a: any, b: any) => (b.data as number) - (a.data as number))
            .map((p: any) => `${p.marker} ${p.seriesName}: ${((p.data as number) * 100).toFixed(2)}%`)
          return `<div style="font-weight:500;margin-bottom:4px">${date}</div>${lines.length ? lines.join('<br/>') : '无数据'}`
        },
      },
      legend: { bottom: 0, textStyle: { fontSize: 12 } },
      grid: { left: 60, right: 20, top: 20, bottom: 50 },
      xAxis: { type: 'category', data: axisMonths,
        axisLabel: { fontSize: 10, color: '#999' }, axisLine: { show: false }, axisTick: { show: false } },
      yAxis: { type: 'value', min: yMin, max: yMax,
        axisLabel: { fontSize: 10, color: '#999', formatter: (v: number) => `${(v * 100).toFixed(1)}%` },
        splitLine: { lineStyle: { color: '#f0f0f0' } } },
      dataZoom: [{ type: 'inside', start: 0, end: 100 }],
      series,
    }
  }, [timeSeriesData, selectedFundIds, period, anchorFundId, smoothingMode])

  if (!option) {
    return <div className="h-80 flex items-center justify-center text-gray-400 text-sm">加载中...</div>
  }
  return <ReactEChartsCore echarts={echarts} option={option} notMerge lazyUpdate style={{ height: 480 }} />
}
