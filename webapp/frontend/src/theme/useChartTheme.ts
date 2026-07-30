import { useStore } from '../store/useStore'
import { chartPalettes, type ChartPalette } from './chartTheme'

/**
 * 图表调色板。调用方必须把 resolvedTheme 也放进 option 的 useMemo 依赖，
 * 否则切主题时 ECharts option 不重建、画布不重绘。
 */
export function useChartTheme(): ChartPalette {
  const resolvedTheme = useStore(s => s.resolvedTheme)
  return chartPalettes[resolvedTheme]
}
