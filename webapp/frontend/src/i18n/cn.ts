/**
 * 中文字典 —— key 的唯一定义源。
 * en.ts 声明为 Record<keyof typeof cn, string>，漏译会在 tsc -b 阶段失败。
 * key 命名：<域>.<语义>，域为 common / nav / theme / lang / dashboard /
 * metric / chart / table / heatmap / anomaly / smoothing / funds。
 */
export const cn = {
  'common.loading': '加载中...',
  'common.noData': '暂无数据',
  'common.error': '出错了',
  'common.noDrawdown': '无回撤',
  'common.months': '{n} 个月',
  'common.recovered': '恢复 {n} 个月',
  'common.notRecovered': '未恢复(已 {n} 个月)',
  'common.smallSample': '样本不足(n={n})，统计指标不可靠',
  'common.listSeparator': '、',
  'common.backendMessage': '后端消息：',

  'nav.brand': '固定收益基金分析',
  'nav.dashboard': '对比看板',
  'nav.anomalies': '异常审计',
  'nav.funds': '基金管理',

  'theme.system': '跟随系统',
  'theme.light': '浅色',
  'theme.dark': '深色',

  'error.boundaryTitle': '出错了',
  'error.reload': '重新加载',
  'metric.rankTitle': '当前口径下名次',

  'lang.switch': '切换语言',

  'chart.tabNav': '累计 NAV',
  'chart.tabRolling': '滚动12月超额',
  'chart.titleNav': '累计 NAV / 回撤',
  'chart.titleRolling': '滚动 12 月超额',
  'chart.baseStart': '起点',
  'chart.noData': '无数据',
  'chart.spliceTip': '{name}：拼接基点，等于锚定基金 {month} 累计值，次月起为该基金自身收益',
  'chart.insufficientHistory': '{name}（历史不足12个月）',
  'chart.anchorHint': '锚定模式下展示锚定基金完整历史 · 再次点击曲线取消锚定',

  'heatmap.title': '月度超额热力图',
  'heatmap.year': '年',
  'heatmap.cellNoData': '{ym}：无数据',
  'heatmap.cellTip': '{ym}\n基金月收益: {fund}%\n基准月收益: {bench}%\n超额: {excess}%',
  'heatmap.legend': '色标：蓝=正超额、红=负超额、灰=无数据（红绿色盲友好配色）；深浅按该基金 90 分位裁剪（危机月不独占满色）；单元格 hover 见原始月收益/基准/超额。兼数据质检视图。',
} as const
