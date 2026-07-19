import { useMemo } from 'react'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { LineChart, ScatterChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useStore } from '../store/useStore'
import type { FundReturns } from '../lib/rebase'
import {
  rebasePlain, rebaseAnchored, drawdownSeries, computeAxisMonths, withLeadingBaseMonths,
  type RebasedSeries,
} from '../lib/rebase'
import { buildShortCodeMap } from '../lib/fundCodes'
import RollingExcessChart from './RollingExcessChart'

echarts.use([LineChart, ScatterChart, GridComponent, TooltipComponent,
  LegendComponent, DataZoomComponent, MarkLineComponent, CanvasRenderer])

const COLORS = ['#4fc3f7', '#7c4dff', '#ff7043', '#66bb6a', '#ffca28', '#ec407a', '#26c6da', '#ab47bc']

export default function NavChart() {
  const timeSeriesData = useStore(s => s.timeSeriesData)
  const selectedFundIds = useStore(s => s.selectedFundIds)
  const period = useStore(s => s.period)
  const anchorFundId = useStore(s => s.anchorFundId)
  const smoothingMode = useStore(s => s.smoothingMode)
  const chartMetric = useStore(s => s.chartMetric)
  const setChartMetric = useStore(s => s.setChartMetric)
  const setAnchor = useStore(s => s.setAnchor)
  const allFunds = useStore(s => s.funds)
  const codeMap = useMemo(() => buildShortCodeMap(allFunds), [allFunds])
  // 选中的基金收益序列（组件级，option 与 onEvents 共用）
  const funds: FundReturns[] = useMemo(() => {
    if (!timeSeriesData) return []
    const isOrig = smoothingMode === 'original'
    return timeSeriesData.series
      .filter(s => selectedFundIds.includes(s.fund_id))
      .map(s => ({
        fund_id: s.fund_id, fund_name: s.fund_name, dates: s.dates,
        returns: (isOrig ? s.returns : (s.unsm_returns ?? s.returns)),
      }))
  }, [timeSeriesData, selectedFundIds, smoothingMode])
  // seriesName（短码）-> fund_id，供 onEvents click 反查（echarts click params 无 seriesId）
  const nameToFundId = useMemo(() => {
    const m = new Map<string, string>()
    funds.forEach(f => m.set(codeMap.get(f.fund_id) ?? f.fund_name, f.fund_id))
    return m
  }, [funds, codeMap])

  const option = useMemo(() => {
    if (!timeSeriesData || funds.length === 0) return null

    // 每支线各自补一个"起点前一月"恒等基点(=1.0)，权威机构 Growth of $X / 总回报
    // 指数重建惯例：N 个月度回报对应 N+1 个指数点，不是编造 NAV(rebase.ts::alignedNav)。
    const axisMonths = withLeadingBaseMonths(
      computeAxisMonths(timeSeriesData.months, funds, period, anchorFundId), funds, anchorFundId,
    )

    // NAV rebase：A/B 用 plain，C 用 anchored 拼接
    const rebased: RebasedSeries[] = anchorFundId
      ? rebaseAnchored(funds, axisMonths, anchorFundId)
      : funds.map(f => rebasePlain(f, axisMonths))
    // 回撤永远走 rebasePlain（各基金自身序列，非拼接；PDD 2.4）
    const drawdowns = funds.map(f => drawdownSeries(rebasePlain(f, axisMonths).nav))

    // NAV y 轴：可见曲线 [min,max] ±5%，永不锚 0
    const navVals = rebased.flatMap(r => r.nav.filter((v): v is number => v != null))
    const navMin = navVals.length ? Math.min(...navVals) : 0
    const navMax = navVals.length ? Math.max(...navVals) : 1
    const navRange = navMax - navMin || 1
    const ddVals = drawdowns.flat().filter((v): v is number => v != null)
    const ddMin = ddVals.length ? Math.min(...ddVals, 0) : 0

    const inC = anchorFundId != null
    const navSeries = rebased.map((r, i) => ({
      id: `nav:${r.fund_id}`,
      name: codeMap.get(r.fund_id) ?? r.fund_name,
      type: 'line' as const,
      triggerLineEvent: true,
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: r.nav,
      connectNulls: false,
      symbol: 'none',
      smooth: false,
      lineStyle: {
        width: 1.5,
        opacity: inC ? (r.isAnchor ? 1 : 0.35) : 1,
        color: r.isAnchor ? '#000' : undefined,
      },
      itemStyle: { color: r.isAnchor ? '#000' : COLORS[i % COLORS.length] },
      z: r.isAnchor ? 10 : 2,
      // F2：NAV y 轴 1.0 起点基准线（A/B/C 均显示，参考线非数据修饰；silent 不吞 click）
      markLine: i === 0 ? {
        symbol: 'none',
        silent: true,
        data: [{ yAxis: 1.0 }],
        lineStyle: { color: '#bbb', type: 'dashed', width: 1 },
        label: { show: true, position: 'end', formatter: '起点', color: '#999', fontSize: 10 },
      } : undefined,
    }))
    const ddSeries = rebased.map((r, i) => ({
      id: `dd:${r.fund_id}`,
      name: codeMap.get(r.fund_id) ?? r.fund_name,
      type: 'line' as const,
      triggerLineEvent: true,
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: drawdowns[i],
      connectNulls: false,
      symbol: 'none',
      smooth: false,
      lineStyle: {
        width: 1,
        opacity: inC ? (r.isAnchor ? 1 : 0.35) : 0.85,
        color: r.isAnchor ? '#000' : undefined,
      },
      itemStyle: { color: r.isAnchor ? '#000' : COLORS[i % COLORS.length] },
      areaStyle: { opacity: 0.12 },
      z: r.isAnchor ? 10 : 2,
    }))
    // 拼接点空心圆（C 态后发基金首点）
    const splicePoints = rebased
      .filter(r => r.splicePoint)
      .map(r => ({
        type: 'scatter' as const,
        xAxisIndex: 0,
        yAxisIndex: 0,
        name: codeMap.get(r.fund_id) ?? r.fund_name,
        data: [[r.splicePoint!.month, r.splicePoint!.value]],
        symbolSize: 10,
        itemStyle: { color: 'transparent', borderColor: '#555', borderWidth: 1.5 },
        tooltip: {
          formatter: () =>
            `${codeMap.get(r.fund_id) ?? r.fund_name}：拼接基点，等于锚定基金 ${r.splicePoint!.month} 累计值，次月起为该基金自身收益`,
        },
        z: 20,
      }))

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { link: [{ xAxisIndex: 'all' }] },
        formatter: (params: any[]) => {
          if (!params || params.length === 0) return ''
          const date = params[0]?.axisValue ?? ''
          const lines = params
            .filter((p: any) => p.seriesType === 'line' && p.seriesId?.startsWith('nav:'))
            .sort((a: any, b: any) => (b.data ?? -Infinity) - (a.data ?? -Infinity))
            .map((p: any) => `${p.marker} ${p.seriesName}: ${p.data == null ? '无数据' : p.data.toFixed(4)}`)
          return `<div style="font-weight:500;margin-bottom:4px">${date}</div>${lines.join('<br/>')}`
        },
      },
      legend: { top: 0, textStyle: { fontSize: 12 } },
      grid: [
        { left: 60, right: 20, top: 30, height: 290 },
        { left: 60, right: 20, top: 345, height: 150 },
      ],
      xAxis: [
        { type: 'category', data: axisMonths, gridIndex: 0,
          axisLabel: { fontSize: 10, color: '#999' }, axisLine: { show: false }, axisTick: { show: false } },
        { type: 'category', data: axisMonths, gridIndex: 1,
          axisLabel: { fontSize: 10, color: '#999' }, axisLine: { show: false }, axisTick: { show: false } },
      ],
      yAxis: [
        { type: 'value', gridIndex: 0, min: navMin - navRange * 0.05, max: navMax + navRange * 0.05,
          axisLabel: { fontSize: 10, color: '#999', formatter: (v: number) => v.toFixed(3) },
          splitLine: { lineStyle: { color: '#f0f0f0' } } },
        { type: 'value', gridIndex: 1, max: 0, min: ddMin - Math.abs(ddMin) * 0.05 - 0.001,
          axisLabel: { fontSize: 10, color: '#999', formatter: (v: number) => `${(v * 100).toFixed(1)}%` },
          splitLine: { lineStyle: { color: '#f0f0f0' } } },
      ],
      dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 }],
      series: [...navSeries, ...ddSeries, ...splicePoints],
    }
  }, [timeSeriesData, funds, period, anchorFundId, codeMap])

  const onEvents = useMemo(() => ({
    click: (params: any) => {
      // echarts click params 无 seriesId 字段，用 seriesName（短码）反查 fund_id；
      // nav/dd/splicePoint 的 seriesName 均为该基金短码，统一处理。
      const fundId = nameToFundId.get(params?.seriesName)
      if (fundId) setAnchor(fundId)
    },
  }), [setAnchor, nameToFundId])

  return (
    <div className="bg-white rounded-lg p-5 shadow-sm mb-5">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-sm text-gray-400">{chartMetric === 'nav' ? '累计 NAV / 回撤' : '滚动 12 月超额'}</h3>
        <select
          className="text-sm border border-gray-200 rounded px-3 py-1.5 bg-white"
          value={chartMetric}
          onChange={e => setChartMetric(e.target.value as any)}
        >
          <option value="nav">累计 NAV</option>
          <option value="rolling_excess">滚动12月超额</option>
        </select>
      </div>
      {chartMetric === 'rolling_excess' ? (
        <RollingExcessChart />
      ) : option ? (
        <ReactEChartsCore
          echarts={echarts}
          option={option}
          notMerge
          lazyUpdate
          onEvents={onEvents}
          style={{ height: 520 }}
        />
      ) : (
        <div className="h-80 flex items-center justify-center text-gray-400 text-sm">加载中...</div>
      )}
      {anchorFundId && (
        <div className="text-xs text-gray-400 mt-2">
          锚定模式下展示锚定基金完整历史 · 再次点击曲线取消锚定
        </div>
      )}
    </div>
  )
}
