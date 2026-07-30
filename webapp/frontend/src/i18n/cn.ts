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
} as const
