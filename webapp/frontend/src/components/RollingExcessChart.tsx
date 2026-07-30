import { useMemo } from 'react'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, DataZoomComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useStore } from '../store/useStore'
import type { FundReturns } from '../lib/rebase'
import { rollingExcess, computeAxisMonths } from '../lib/rebase'
import { buildShortCodeMap } from '../lib/fundCodes'
import { useT } from '../i18n/useT'
import { useChartTheme } from '../theme/useChartTheme'

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, DataZoomComponent, CanvasRenderer])

export default function RollingExcessChart() {
  const t = useT()
  const palette = useChartTheme()
  const timeSeriesData = useStore(s => s.timeSeriesData)
  const selectedFundIds = useStore(s => s.selectedFundIds)
  const period = useStore(s => s.period)
  const anchorFundId = useStore(s => s.anchorFundId)
  const smoothingMode = useStore(s => s.smoothingMode)
  const allFunds = useStore(s => s.funds)
  const codeMap = useMemo(() => buildShortCodeMap(allFunds), [allFunds])

  const option = useMemo(() => {
    if (!timeSeriesData || timeSeriesData.series.length === 0) return null
    const isOrig = smoothingMode === 'original'
    const funds: FundReturns[] = timeSeriesData.series
      .filter(s => selectedFundIds.includes(s.fund_id) && s.dates.length > 0)
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
      const isAnchor = f.fund_id === anchorFundId
      const displayName = codeMap.get(f.fund_id) ?? f.fund_name
      return {
        id: `roll:${f.fund_id}`,
        name: short ? t('chart.insufficientHistory', { name: displayName }) : displayName,
        type: 'line' as const,
        data,
        connectNulls: false,
        symbol: 'none',
        smooth: false,
        lineStyle: {
          width: 1.5,
          opacity: short ? 0 : (anchorFundId ? (isAnchor ? 1 : 0.35) : 1),
          color: isAnchor ? palette.anchor : undefined,
        },
        itemStyle: { color: isAnchor ? palette.anchor : palette.series[i % palette.series.length] },
        z: isAnchor ? 10 : 2,
        markLine: i === 0 ? {
          symbol: 'none',
          data: [{ yAxis: 0 }],
          lineStyle: { color: palette.baseline, type: 'dashed' },
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
        backgroundColor: palette.tooltipBg,
        borderColor: palette.tooltipBorder,
        textStyle: { color: palette.tooltipFg },
        formatter: (params: any[]) => {
          if (!params || params.length === 0) return ''
          const date = params[0]?.axisValue ?? ''
          const lines = params
            .filter((p: any) => p.data != null)
            .sort((a: any, b: any) => (b.data as number) - (a.data as number))
            .map((p: any) => `${p.marker} ${p.seriesName}: ${((p.data as number) * 100).toFixed(2)}%`)
          return `<div style="font-weight:500;margin-bottom:4px">${date}</div>${lines.length ? lines.join('<br/>') : t('chart.noData')}`
        },
      },
      legend: { top: 0, textStyle: { fontSize: 12, color: palette.axisLabel } },
      grid: { left: 60, right: 20, top: 30, bottom: 50 },
      xAxis: { type: 'category', data: axisMonths,
        axisLabel: { fontSize: 10, color: palette.axisLabel }, axisLine: { show: false }, axisTick: { show: false } },
      yAxis: { type: 'value', min: yMin, max: yMax,
        axisLabel: { fontSize: 10, color: palette.axisLabel, formatter: (v: number) => `${(v * 100).toFixed(1)}%` },
        splitLine: { lineStyle: { color: palette.splitLine } } },
      dataZoom: [{ type: 'inside', start: 0, end: 100 }],
      series,
    }
  }, [timeSeriesData, selectedFundIds, period, anchorFundId, smoothingMode, codeMap, palette, t])

  if (!option) {
    return <div className="h-80 flex items-center justify-center text-fg-subtle text-sm">{t('common.loading')}</div>
  }
  return <ReactEChartsCore echarts={echarts} option={option} notMerge lazyUpdate style={{ height: 480 }} />
}
