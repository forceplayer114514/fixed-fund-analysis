import { useMemo } from 'react'
import { useStore } from '../store/useStore'
import type { FundMetrics } from '../types'
import { useT } from '../i18n/useT'
import type { I18nKey } from '../i18n'

function cardBorder(status: string) {
  switch (status) {
    case 'applied':
      return 'border-l-neg'
    case 'watch':
      return 'border-l-warn'
    case 'insufficient':
      return 'border-l-border-strong'
    default:
      return 'border-l-pos'
  }
}

const STATUS_KEY: Record<string, I18nKey> = {
  applied: 'smoothing.statusApplied',
  watch: 'smoothing.statusWatch',
  insufficient: 'smoothing.statusInsufficient',
  none: 'smoothing.statusNone',
}

function statusTagClass(status: string) {
  switch (status) {
    case 'applied':
      return 'bg-neg-soft text-neg'
    case 'watch':
      return 'bg-warn-soft text-warn'
    case 'insufficient':
      return 'bg-sunken text-fg-subtle'
    default:
      return 'bg-pos-soft text-pos'
  }
}

export default function SmoothingCards() {
  const t = useT()
  const compareData = useStore(s => s.compareData)

  const cards = useMemo(() => {
    if (!compareData?.funds) return []
    return compareData.funds.map((m: FundMetrics) => {
      const historyMonths = m.history_months ?? 0
      const phi = m.unsmoothing_coefficient_phi ?? 0
      const q = m.ljung_box_q ?? 0
      const isSignificant = m.is_q_significant ?? false
      const isGeltner = m.is_geltner_applied ?? false
      const isShort = m.is_short_history_warning ?? true

      let status: string
      let prob: number | null
      let note: string

      if (isShort) {
        status = 'insufficient'
        prob = null
        note = t('smoothing.fw1Fail', { n: historyMonths })
      } else if (isGeltner) {
        status = 'applied'
        prob = Math.min((q / (historyMonths - 1)) * 100, 99.9)
        note = t('smoothing.allPass')
      } else if (phi > 0 && !isSignificant) {
        status = 'watch'
        prob = Math.min((q / (historyMonths - 1)) * 100, 99.9)
        note = t('smoothing.phiPosWeak')
      } else {
        status = 'none'
        prob = null
        note = t('smoothing.notSignificant')
      }

      return {
        fund_id: m.fund_id,
        fund_name: m.fund_name ?? m.fund_id,
        phi,
        q,
        historyMonths,
        status,
        prob,
        note,
      }
    })
  }, [compareData, t])

  if (cards.length === 0) return null

  return (
    <div className="card p-5">
      <h2 className="text-base font-medium mb-4">
        {t('smoothing.title')}{' '}
        <span className="text-xs bg-accent-soft text-accent px-2 py-0.5 rounded-full ml-2">
          {t('smoothing.fundCount', { n: cards.length })}
        </span>
      </h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {cards.map(c => (
          <div
            key={c.fund_id}
            className={`bg-sunken rounded-lg p-4 border-l-4 ${cardBorder(c.status)}`}
          >
            <div className="mb-3">
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusTagClass(c.status)}`}>
                {t(STATUS_KEY[c.status])}
              </span>
            </div>
            <div className="font-medium text-sm mb-3 text-fg">{c.fund_name}</div>
            <div className="space-y-1.5 text-xs text-fg-muted">
              <div className="flex justify-between">
                <span className="text-fg-subtle">{t('smoothing.phi')}</span>
                <span className="font-mono tabular-nums text-fg">{c.phi.toFixed(4)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-fg-subtle">{t('smoothing.ljungBoxQ')}</span>
                <span className="font-mono tabular-nums text-fg">{c.q.toFixed(4)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-fg-subtle">{t('smoothing.months')}</span>
                <span className="font-mono tabular-nums text-fg">
                  {t('common.months', { n: c.historyMonths })} {c.historyMonths >= 36 ? '✓' : '✗'}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-fg-subtle">{t('smoothing.interventionProb')}</span>
                <span
                  className={`font-mono tabular-nums font-semibold ${
                    c.status === 'applied'
                      ? 'text-neg'
                      : c.status === 'watch'
                        ? 'text-warn'
                        : 'text-fg-subtle'
                  }`}
                >
                  {c.prob != null ? `${c.prob.toFixed(1)}%` : t('smoothing.unknown')}
                </span>
              </div>
              {c.prob != null && (
                <div className="h-1.5 bg-border rounded-full mt-1 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${c.status === 'applied' ? 'bg-neg' : 'bg-warn'}`}
                    style={{ width: `${Math.min(c.prob, 100)}%` }}
                  />
                </div>
              )}
            </div>
            <div className="text-xs text-fg-subtle mt-3">{c.note}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
