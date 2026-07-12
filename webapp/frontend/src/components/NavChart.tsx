import { useMemo } from 'react'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useStore } from '../store/useStore'

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  CanvasRenderer,
])

const COLORS = ['#4fc3f7', '#7c4dff', '#ff7043', '#66bb6a', '#ffca28', '#ec407a', '#26c6da', '#ab47bc']

export default function NavChart() {
  const timeSeriesData = useStore(s => s.timeSeriesData)
  const selectedFundIds = useStore(s => s.selectedFundIds)
  const chartMetric = useStore(s => s.chartMetric)
  const smoothingMode = useStore(s => s.smoothingMode)
  const setChartMetric = useStore(s => s.setChartMetric)

  const option = useMemo(() => {
    if (!timeSeriesData || timeSeriesData.series.length === 0) return null

    const allMonths = timeSeriesData.months
    const series = timeSeriesData.series.map((s, i) => {
      // 建立 month -> nav 查找表，按 allMonths 对齐
      const navByMonth = new Map<string, number>()
      const navData = smoothingMode === 'unsmoothed' && s.unsm_nav ? s.unsm_nav : s.orig_nav
      s.dates.forEach((date, j) => {
        const month = date.slice(0, 7)
        navByMonth.set(month, navData[j])
      })

      const isSelected = selectedFundIds.includes(s.fund_id)

      let data: (number | null)[]
      if (chartMetric === 'nav') {
        data = allMonths.map(m => navByMonth.get(m) ?? null)
      } else {
        // 月收益率：按基金自身日期序列算 NAV 环比增长
        const retByMonth = new Map<string, number>()
        navData.forEach((v, j) => {
          if (j === 0) { retByMonth.set(s.dates[j].slice(0, 7), 0); return }
          const prev = navData[j - 1]
          retByMonth.set(s.dates[j].slice(0, 7), prev !== 0 ? (v - prev) / prev : 0)
        })
        data = allMonths.map(m => retByMonth.get(m) ?? null)
      }

      return {
        name: s.fund_name,
        type: 'line',
        data,
        smooth: true,
        symbol: 'none',
        lineStyle: {
          width: isSelected ? 3 : 1.5,
          opacity: isSelected ? 1 : 0.25,
        },
        itemStyle: { color: COLORS[i % COLORS.length] },
      }
    })

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: any[]) => {
          if (!params || params.length === 0) return ''
          const date = params[0]?.axisValue ?? ''
          const lines = params
            .sort((a: any, b: any) => (b.data ?? 0) - (a.data ?? 0))
            .map(
              (p: any, idx: number) =>
                `${p.marker} ${p.seriesName}: ${p.data == null ? '-' : p.data.toFixed(4)} <span style="color:#999">(${idx + 1})</span>`
            )
          return `<div style="font-weight:500;margin-bottom:4px">${date}</div>${lines.join('<br/>')}`
        },
      },
      legend: { bottom: 0, textStyle: { fontSize: 12 } },
      grid: { left: 60, right: 20, top: 20, bottom: 50 },
      xAxis: {
        type: 'category',
        data: allMonths,
        axisLabel: { fontSize: 11, color: '#999' },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          fontSize: 11,
          color: '#999',
          formatter: (v: number) =>
            chartMetric === 'excess_return' ? `${(v * 100).toFixed(1)}%` : v.toFixed(2),
        },
        splitLine: { lineStyle: { color: '#f0f0f0' } },
      },
      dataZoom: [{ type: 'inside', start: 0, end: 100 }],
      series,
    }
  }, [timeSeriesData, selectedFundIds, chartMetric, smoothingMode])

  return (
    <div className="bg-white rounded-lg p-5 shadow-sm mb-5">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-sm text-gray-400">月走势图</h3>
        <select
          className="text-sm border border-gray-200 rounded px-3 py-1.5 bg-white"
          value={chartMetric}
          onChange={e => setChartMetric(e.target.value as any)}
        >
          <option value="nav">累计 NAV</option>
          <option value="excess_return">月收益率</option>
        </select>
      </div>
      {option ? (
        <ReactEChartsCore echarts={echarts} option={option} style={{ height: 400 }} />
      ) : (
        <div className="h-80 flex items-center justify-center text-gray-400 text-sm">
          加载中...
        </div>
      )}
    </div>
  )
}
