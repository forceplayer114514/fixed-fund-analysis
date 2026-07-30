import { useMemo } from 'react'
import { useStore } from '../store/useStore'
import type { FundMetrics } from '../types'
import WarnBadge from './WarnBadge'
import { buildShortCodeMap } from '../lib/fundCodes'
import { useT } from '../i18n/useT'

/** 小样本门禁阈值（PDD 1.5）：n < 24 时 IR/胜率名次置灰 + 角标。 */
const SMALL_SAMPLE_THRESHOLD = 24

function rankBy<T extends FundMetrics>(
  items: T[],
  extract: (item: T) => number | null,
  eligible: (item: T) => boolean = () => true,
  asc = false,
): Map<string, number> {
  // 仅 eligible 项参与 1..k 排序；非 eligible（样本不足）项不占用名次序号（PDD 1.5）。
  const eligibleItems = items.filter(eligible)
  const sorted = [...eligibleItems].sort((a, b) => {
    const va = extract(a) ?? (asc ? Infinity : -Infinity)
    const vb = extract(b) ?? (asc ? Infinity : -Infinity)
    return asc ? va - vb : vb - va
  })
  const map = new Map<string, number>()
  sorted.forEach((item, i) => map.set(item.fund_id, i + 1))
  return map
}

function fmt(v: number | null, suffix = '', decimals = 2) {
  if (v == null) return '-'
  return `${(v * 100).toFixed(decimals)}${suffix}`
}

/** 名次徽标，样式沿用 MetricCard；小样本（dim）置灰版，避免暗示统计上可靠。 */
function RankBadge({ rank, dim = false }: { rank?: number; dim?: boolean }) {
  if (rank == null) return null
  return (
    <span
      className={`ml-1 text-[11px] font-medium rounded px-1.5 py-0.5 ${
        dim ? 'bg-sunken text-fg-subtle' : 'bg-accent-soft text-accent'
      }`}
    >
      #{rank}
    </span>
  )
}

/** 正负着色：必须用原始数值（未经 fmt 格式化/四舍五入的浮点数）判断符号，禁止解析显示字符串。 */
function signColor(v: number | null): string {
  if (v == null) return ''
  return v >= 0 ? 'text-pos' : 'text-neg'
}

export default function CompareTable() {
  const t = useT()
  const compareData = useStore(s => s.compareData)
  const smoothingMode = useStore(s => s.smoothingMode)
  const funds = useStore(s => s.funds)
  const period = useStore(s => s.period)
  const anchorFundId = useStore(s => s.anchorFundId)
  const timeSeriesData = useStore(s => s.timeSeriesData)
  const setAnchor = useStore(s => s.setAnchor)
  const fundNameMap = useMemo(() => {
    const m = new Map<string, string>()
    funds.forEach(f => m.set(f.fund_id, f.fund_name))
    return m
  }, [funds])
  const codeMap = useMemo(() => buildShortCodeMap(funds), [funds])

  const rows = useMemo(() => {
    if (!compareData) return []
    const items = compareData.funds as FundMetrics[]
    const isOrig = smoothingMode === 'original'
    const statEligible = (x: FundMetrics) => (x.excess_sample_months ?? 0) >= SMALL_SAMPLE_THRESHOLD

    const rankExcess = rankBy(items, m =>
      isOrig ? m.orig_annualized_excess_return : m.un_annualized_excess_return)
    const rankSharpe = rankBy(items, m =>
      isOrig ? m.orig_sharpe_ratio : m.un_sharpe_ratio, statEligible)
    const rankDD = rankBy(items, m =>
      isOrig ? m.orig_max_drawdown : m.un_max_drawdown)
    const rankWin = rankBy(items, m =>
      isOrig ? m.orig_excess_win_rate : m.un_excess_win_rate, statEligible)

    return items.map(m => {
      const isOrigRow = isOrig
      const dd = isOrigRow ? m.orig_max_drawdown : m.un_max_drawdown
      const recoveryMonths = isOrigRow ? m.orig_recovery_months : m.un_recovery_months
      const recovered = isOrigRow ? m.orig_dd_recovered : m.un_dd_recovered
      const excessRaw = isOrigRow ? m.orig_annualized_excess_return : m.un_annualized_excess_return
      const underperformMonths = isOrigRow ? m.orig_max_underperform_months : m.un_max_underperform_months
      // 恢复月数标签（修正3 统一口径：与卡片一致）
      let recoveryLabel: string
      if (dd === 0 || recoveryMonths == null) recoveryLabel = t('common.noDrawdown')
      else recoveryLabel = recovered
        ? t('common.recovered', { n: recoveryMonths })
        : t('common.notRecovered', { n: recoveryMonths })

      return {
        fund_id: m.fund_id,
        annReturn: fmt(isOrigRow ? m.orig_annualized_return : m.un_annualized_return, '%'),
        excess: fmt(excessRaw, '%'),
        excessRaw,
        excessRank: rankExcess.get(m.fund_id),
        sharpe: isOrigRow ? m.orig_sharpe_ratio : m.un_sharpe_ratio,
        sharpeRank: rankSharpe.get(m.fund_id),
        dd: fmt(dd, '%'),
        ddRaw: dd,
        ddRank: rankDD.get(m.fund_id),
        recoveryLabel,
        winRate: fmt(isOrigRow ? m.orig_excess_win_rate : m.un_excess_win_rate, '%'),
        winRank: rankWin.get(m.fund_id),
        run: t('common.months', { n: underperformMonths }),
        vol: fmt(isOrigRow ? m.orig_annualized_volatility : m.un_annualized_volatility, '%'),
        small: (m.excess_sample_months ?? 0) < SMALL_SAMPLE_THRESHOLD,
        n: m.excess_sample_months ?? 0,
      }
    })
  }, [compareData, smoothingMode, t])

  // F5：窗口说明（共同区间/1y/3y 显示起止+n；full 或锚定显示对应口径），消除满屏⚠困惑
  const windowNote = useMemo(() => {
    if (anchorFundId) {
      // 指标窗口起点=锚定基金自身首月（与图表 rebase 同口径），各基金终点各自最新月份
      const startMonth = timeSeriesData?.series
        .find(s => s.fund_id === anchorFundId)?.dates[0]?.slice(0, 7)
      return startMonth ? t('table.anchorWindow', { startMonth }) : t('table.anchorMode')
    }
    const items = compareData?.funds ?? []
    if (period === 'full' || items.length === 0) return t('dashboard.periodFull')
    const endYM = items[0].date_period
    const n = items[0].excess_sample_months ?? 0
    if (!endYM || n === 0) return ''
    const [y, m] = endYM.split('-').map(Number)
    const startIdx = (y * 12 + (m - 1)) - (n - 1)
    const sy = Math.floor(startIdx / 12)
    const sm = (startIdx % 12) + 1
    const startYM = `${sy}-${String(sm).padStart(2, '0')}`
    return t('table.currentWindow', { startYM, endYM, n })
  }, [period, anchorFundId, compareData, timeSeriesData, t])

  if (rows.length === 0) return null

  return (
    <div className="card overflow-hidden p-5">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-sm text-fg-muted">{t('table.title')}</h3>
        {windowNote && <span className="text-xs text-fg-muted">{windowNote}</span>}
      </div>
      <div className="overflow-x-auto max-h-[70vh] overflow-y-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th text-left px-3 py-2">{t('table.fundName')}</th>
              <th className="th text-right px-3 py-2">{t('table.annReturn')}</th>
              <th className="th text-right px-3 py-2">{t('table.annExcess')}</th>
              <th className="th text-right px-3 py-2">{t('table.sharpe')}</th>
              <th className="th text-right px-3 py-2">{t('table.maxDrawdown')}</th>
              <th className="th text-right px-3 py-2">{t('table.winRate')}</th>
              <th className="th text-right px-3 py-2">{t('table.longestUnderperform')}</th>
              <th className="th text-right px-3 py-2">{t('table.annVol')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const isAnchor = anchorFundId === r.fund_id
              return (
              <tr
                key={r.fund_id}
                onClick={() => setAnchor(r.fund_id)}
                title={isAnchor ? t('table.anchorOff') : t('table.anchorOn')}
                className={`cursor-pointer transition-colors ${
                  isAnchor ? 'bg-accent-soft' : 'even:bg-sunken hover:bg-accent-soft'
                }`}
              >
                {/* border-l 放在首个 td 而非 tr：table 默认 border-collapse:separate 下浏览器不绘制 tr 自身的 border */}
                <td
                  className={`px-3 py-2 text-fg font-medium max-w-xs truncate border-l-2 ${
                    isAnchor ? 'border-accent' : 'border-transparent'
                  }`}
                  title={fundNameMap.get(r.fund_id) ?? r.fund_id}
                >
                  {isAnchor && <span className="text-accent mr-1">●</span>}
                  {fundNameMap.get(r.fund_id) ?? r.fund_id}
                  <span className="ml-1.5 text-xs text-fg-muted font-normal">{codeMap.get(r.fund_id)}</span>
                </td>
                {/* 年化收益率：无名次括号（PDD 1.3） */}
                <td className="num px-3 py-2">{r.annReturn}</td>
                <td className={`num px-3 py-2 ${signColor(r.excessRaw)}`}>
                  {r.excess}
                  <RankBadge rank={r.excessRank} />
                </td>
                <td className="num px-3 py-2">
                  {r.sharpe == null ? '-' : r.sharpe.toFixed(2)}
                  <RankBadge rank={r.sharpeRank} dim={r.small} />
                  {r.small && <WarnBadge note={t('common.smallSample', { n: r.n })} />}
                </td>
                <td className={`num px-3 py-2 ${signColor(r.ddRaw)}`}>
                  {r.dd}
                  <RankBadge rank={r.ddRank} />
                  <span className="ml-1 text-xs text-fg-muted">· {r.recoveryLabel}</span>
                </td>
                <td className="num px-3 py-2">
                  {r.winRate}
                  <RankBadge rank={r.winRank} dim={r.small} />
                  {r.small && <WarnBadge note={t('common.smallSample', { n: r.n })} />}
                </td>
                <td className="num px-3 py-2">{r.run}</td>
                <td className="num px-3 py-2">{r.vol}</td>
              </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="text-xs text-fg-muted mt-2">{t('table.rowHint')}</div>
    </div>
  )
}
