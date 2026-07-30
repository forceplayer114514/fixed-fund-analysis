import type { cn } from './cn'

/** 英文字典。缺任何一个 cn 的 key，tsc -b 直接失败。 */
export const en: Record<keyof typeof cn, string> = {
  'common.loading': 'Loading…',
  'common.noData': 'No data',
  'common.error': 'Something went wrong',
  'common.noDrawdown': 'No drawdown',
  'common.months': '{n} mo',
  'common.recovered': 'Recovered in {n} mo',
  'common.notRecovered': 'Not recovered ({n} mo so far)',
  'common.smallSample': 'Sample too small (n={n}); statistics unreliable',
  'common.listSeparator': ', ',
  'common.backendMessage': 'Backend message: ',

  'nav.brand': 'Fixed Income Fund Analytics',
  'nav.dashboard': 'Dashboard',
  'nav.anomalies': 'Anomaly audit',
  'nav.funds': 'Fund management',

  'theme.system': 'System',
  'theme.light': 'Light',
  'theme.dark': 'Dark',

  'error.boundaryTitle': 'Something went wrong',
  'error.reload': 'Reload',
  'metric.rankTitle': 'Rank under the current basis',

  'lang.switch': 'Switch language',

  'chart.tabNav': 'Cumulative NAV',
  'chart.tabRolling': 'Rolling 12m excess',
  'chart.titleNav': 'Cumulative NAV / drawdown',
  'chart.titleRolling': 'Rolling 12-month excess',
  'chart.baseStart': 'Base',
  'chart.noData': 'No data',
  'chart.spliceTip': '{name}: splice base point, equal to the anchor fund cumulative value at {month}; from the next month onward this is the fund own return',
  'chart.insufficientHistory': '{name} (less than 12 months of history)',
  'chart.anchorHint': 'Anchor mode shows the full history of the anchored fund · click the line again to release the anchor',
}
