import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useStore } from '../store/useStore'
import FundChips from '../components/FundChips'
import MetricCard from '../components/MetricCard'
import NavChart from '../components/NavChart'
import CompareTable from '../components/CompareTable'
import ExcessHeatmap from '../components/ExcessHeatmap'
import { useT } from '../i18n/useT'
import type { FundMetrics } from '../types'

/** 小样本门禁阈值（PDD 1.5）：当前窗口有效样本月数 n < 24 时 IR/胜率不可靠。 */
const SMALL_SAMPLE_THRESHOLD = 24

function rankAmong(
  items: FundMetrics[],
  value: number | null | undefined,
  extract: (m: FundMetrics) => number | null,
  eligible: (m: FundMetrics) => boolean = () => true,
): number | undefined {
  if (value == null) return undefined
  const vals = items.filter(eligible).map(extract).filter((x): x is number => x != null)
  return vals.filter(x => x > value).length + 1
}

export default function Dashboard() {
  const t = useT()
  const funds = useStore(s => s.funds)
  const fundsLoading = useStore(s => s.fundsLoading)
  const fundsError = useStore(s => s.fundsError)
  const compareData = useStore(s => s.compareData)
  const compareError = useStore(s => s.compareError)
  const selectedFundIds = useStore(s => s.selectedFundIds)
  const period = useStore(s => s.period)
  const anchorFundId = useStore(s => s.anchorFundId)
  const smoothingMode = useStore(s => s.smoothingMode)
  const setPeriod = useStore(s => s.setPeriod)
  const setSmoothingMode = useStore(s => s.setSmoothingMode)
  const fetchFunds = useStore(s => s.fetchFunds)
  const fetchCompare = useStore(s => s.fetchCompare)
  const fetchTimeSeries = useStore(s => s.fetchTimeSeries)

  useEffect(() => { fetchFunds() }, [])
  // 方案a：time-series 仅在选中集变化时 refetch（恒 full）；period/anchor 切换纯前端重算
  useEffect(() => { if (funds.length > 0) fetchTimeSeries() }, [selectedFundIds, funds.length])
  // compare：选中集 / period / 锚定变化时 refetch；锚定时用 full（修正A，卡片表格图表同口径）
  useEffect(() => { if (funds.length > 0) fetchCompare() }, [selectedFundIds, period, anchorFundId, funds.length])

  const allMetrics = compareData?.funds ?? []
  // 卡片联动锚定基金（PDD 2.3）；无锚定时占位
  const m: FundMetrics | undefined = anchorFundId
    ? compareData?.funds?.find(x => x.fund_id === anchorFundId)
    : undefined
  const isOrig = smoothingMode === 'original'
  const isSmallSample = !m || (m.excess_sample_months ?? 0) < SMALL_SAMPLE_THRESHOLD
  const smallNote = m ? t('common.smallSample', { n: m.excess_sample_months ?? 0 }) : undefined
  const eligibleForStats = (x: FundMetrics) => (x.excess_sample_months ?? 0) >= SMALL_SAMPLE_THRESHOLD

  const excess = m ? (isOrig ? m.orig_annualized_excess_return : m.un_annualized_excess_return) : null
  const sharpe = m ? (isOrig ? m.orig_sharpe_ratio : m.un_sharpe_ratio) : null
  const winRate = m ? (isOrig ? m.orig_excess_win_rate : m.un_excess_win_rate) : null
  const dd = m ? (isOrig ? m.orig_max_drawdown : m.un_max_drawdown) : null
  const recoveryMonths = m ? (isOrig ? m.orig_recovery_months : m.un_recovery_months) : null
  const recovered = m ? (isOrig ? m.orig_dd_recovered : m.un_dd_recovered) : false
  const recoverySubtext: string | undefined = (() => {
    if (!m) return undefined
    if (dd === 0 || recoveryMonths == null) return t('common.noDrawdown')
    return recovered
      ? t('common.recovered', { n: recoveryMonths })
      : t('common.notRecovered', { n: recoveryMonths })
  })()

  // 排除提示：前后两段各自是独立字典 key，中间插入真实 <Link>——不依赖任何字符串拼接/切分，
  // 未来改字典措辞或大小写都不会影响渲染结构（区别于曾用过的运行时 split 方案）。
  const excludedIds = compareData?.excluded?.map(e => e.fund_id).join(t('common.listSeparator')) ?? ''

  if (fundsLoading) return <div className="text-fg-subtle">{t('dashboard.loadingFunds')}</div>
  if (fundsError) return <div className="text-neg">{t('dashboard.fundsLoadFailed')}{fundsError}</div>
  if (funds.length === 0) return <div className="text-fg-subtle">{t('dashboard.noFunds')}</div>

  return (
    <div>
      <div className="flex justify-between items-center mb-5 flex-wrap gap-3">
        <h1 className="text-xl font-semibold text-fg">{t('dashboard.title')}</h1>
        <div className="flex gap-2">
          <select
            className="text-sm border border-border rounded-md px-3 py-1.5 bg-surface text-fg disabled:bg-sunken disabled:text-fg-subtle"
            value={period}
            disabled={!!anchorFundId}
            title={anchorFundId ? t('dashboard.periodDisabledHint') : undefined}
            onChange={e => setPeriod(e.target.value as any)}
          >
            <option value="full">{t('dashboard.periodFull')}</option>
            <option value="3y">{t('dashboard.period3y')}</option>
            <option value="1y">{t('dashboard.period1y')}</option>
            <option value="common">{t('dashboard.periodCommon')}</option>
          </select>
          <select
            className="text-sm border border-border rounded-md px-3 py-1.5 bg-surface text-fg disabled:bg-sunken disabled:text-fg-subtle"
            value={smoothingMode}
            onChange={e => setSmoothingMode(e.target.value as any)}
          >
            <option value="original">{t('dashboard.smoothingOriginal')}</option>
            <option value="unsmoothed">{t('dashboard.smoothingUnsmoothed')}</option>
          </select>
        </div>
      </div>

      <FundChips />

      {compareError && (
        <div className="bg-neg-soft border border-neg-border text-neg text-sm rounded-lg p-4 mb-5">
          {t('dashboard.metricsLoadFailed')}{compareError}
        </div>
      )}

      {compareData?.excluded && compareData.excluded.length > 0 && (
        <div className="bg-warn-soft border border-warn-border text-warn text-sm rounded-lg p-3 mb-5">
          {t('dashboard.excludedNoticePrefix', { ids: excludedIds })}
          <Link className="underline" to="/funds">{t('nav.funds')}</Link>
          {t('dashboard.excludedNoticeSuffix')}
        </div>
      )}

      {m ? (
        <div className="text-sm text-fg-muted mb-3">
          {t('dashboard.showing')}<span className="font-medium text-fg">{m.fund_name ?? anchorFundId ?? '-'}</span>
          <span className="text-fg-subtle ml-2">{t('dashboard.historyMonths', { n: m.history_months })}</span>
        </div>
      ) : (
        <div className="text-sm text-fg-subtle mb-3">{t('dashboard.pickAnchorShort')}</div>
      )}

      {m ? (
        <div className="flex gap-3 mb-6 flex-wrap">
          <MetricCard
            label={t('metric.excess')}
            value={excess != null ? `${(excess * 100).toFixed(2)}%` : '-'}
            rank={rankAmong(allMetrics, excess, x =>
              isOrig ? x.orig_annualized_excess_return : x.un_annualized_excess_return)}
          />
          <MetricCard
            label={t('metric.sharpe')}
            value={sharpe != null ? sharpe.toFixed(2) : '-'}
            rank={isSmallSample ? undefined : rankAmong(allMetrics, sharpe,
              x => (isOrig ? x.orig_sharpe_ratio : x.un_sharpe_ratio), eligibleForStats)}
            warn={isSmallSample}
            warnNote={isSmallSample ? smallNote : undefined}
          />
          <MetricCard
            label={t('metric.winRate')}
            value={winRate != null ? `${(winRate * 100).toFixed(1)}%` : '-'}
            rank={isSmallSample ? undefined : rankAmong(allMetrics, winRate,
              x => (isOrig ? x.orig_excess_win_rate : x.un_excess_win_rate), eligibleForStats)}
            warn={isSmallSample}
            warnNote={isSmallSample ? smallNote : undefined}
          />
          <MetricCard
            label={t('metric.maxDrawdown')}
            value={dd != null ? `${(dd * 100).toFixed(2)}%` : '-'}
            rank={rankAmong(allMetrics, dd, x => isOrig ? x.orig_max_drawdown : x.un_max_drawdown)}
            subtext={recoverySubtext}
          />
        </div>
      ) : (
        <div className="bg-sunken border border-dashed border-border rounded-lg p-6 mb-6 text-center text-sm text-fg-subtle">
          {t('dashboard.pickAnchorLong')}
        </div>
      )}

      <NavChart />
      <CompareTable />
      <ExcessHeatmap />
    </div>
  )
}
