import type { ResolvedTheme } from './theme'

/**
 * ECharts 画布不解析 CSS 变量，只能给具体色值，所以这里是 index.css 的 TS 副本。
 * 两侧靠 palette.test.ts 对拍锁死：改一边不改另一边，测试报红。
 * heatPos / heatNeg 是空格分隔 RGB 三元组，ExcessHeatmap 要按超额绝对值合成 alpha。
 */
export interface ChartPalette {
  series: string[]
  anchor: string
  axisLabel: string
  splitLine: string
  baseline: string
  spliceBorder: string
  tooltipBg: string
  tooltipBorder: string
  tooltipFg: string
  heatPos: string
  heatNeg: string
  heatEmpty: string
}

/** 字段 → index.css 变量名。对拍测试据此逐项比对。 */
export const CHART_TOKEN_MAP: Record<Exclude<keyof ChartPalette, 'series'>, string> = {
  anchor: '--anchor',
  axisLabel: '--fg-subtle',
  splitLine: '--grid',
  baseline: '--border-strong',
  spliceBorder: '--fg-muted',
  tooltipBg: '--tooltip-bg',
  tooltipBorder: '--tooltip-border',
  tooltipFg: '--tooltip-fg',
  heatPos: '--heat-pos',
  heatNeg: '--heat-neg',
  heatEmpty: '--sunken',
}

export const chartPalettes: Record<ResolvedTheme, ChartPalette> = {
  light: {
    series: ['#2478c4', '#6f42c1', '#e8590c', '#2f9e44', '#c99a06', '#d6336c', '#0c8599', '#9c36b5'],
    anchor: '#14181d',
    axisLabel: '#9aa3ad',
    splitLine: '#eef0f3',
    baseline: '#cfd4da',
    spliceBorder: '#6b7480',
    tooltipBg: '#ffffff',
    tooltipBorder: '#e3e6ea',
    tooltipFg: '#14181d',
    heatPos: '42 120 214',
    heatNeg: '227 73 72',
    heatEmpty: '#f1f3f5',
  },
  dark: {
    series: ['#4dabf7', '#a78bfa', '#ff922b', '#51cf66', '#ffd43b', '#f783ac', '#22b8cf', '#da77f2'],
    anchor: '#f0f4f8',
    axisLabel: '#6e7a87',
    splitLine: '#21272f',
    baseline: '#3a4552',
    spliceBorder: '#9aa5b1',
    tooltipBg: '#1c232c',
    tooltipBorder: '#273040',
    tooltipFg: '#e6edf3',
    heatPos: '77 155 233',
    heatNeg: '240 105 100',
    heatEmpty: '#1c232c',
  },
}
