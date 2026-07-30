import { useState, useMemo } from 'react'
import { useStore } from '../store/useStore'
import type { Anomaly } from '../types'
import { useT } from '../i18n/useT'
import type { I18nKey, I18nParams } from '../i18n'

function typeBadge(a: Anomaly, t: (key: I18nKey, params?: I18nParams) => string) {
  if (a.type === 'rba_missing') {
    return (
      <span className="text-xs bg-sunken text-fg-muted px-2 py-0.5 rounded-full font-medium">
        {t('anomaly.badgeRbaMissing')}
      </span>
    )
  }
  return (
    <span className="text-xs bg-warn-soft text-warn px-2 py-0.5 rounded-full font-medium">
      {t('anomaly.badgeOutlier')}
    </span>
  )
}

export default function AnomalyTable() {
  const t = useT()
  const anomalies = useStore(s => s.anomalies)
  const patchMonthlyReturn = useStore(s => s.patchMonthlyReturn)
  const [filterFund, setFilterFund] = useState<string>('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editValue, setEditValue] = useState('')
  const [editError, setEditError] = useState('')

  const fundOptions = useMemo(() => {
    const map = new Map<string, number>()
    anomalies.forEach(a => {
      const key = a.fund_name ?? a.fund_id
      map.set(key, (map.get(key) ?? 0) + 1)
    })
    return Array.from(map.entries())
  }, [anomalies])

  const filtered = useMemo(() => {
    if (!filterFund) return anomalies
    return anomalies.filter(a => (a.fund_name ?? a.fund_id) === filterFund)
  }, [anomalies, filterFund])

  const handleEdit = async (mrId: number) => {
    const pct = parseFloat(editValue)
    if (isNaN(pct)) {
      setEditError(t('anomaly.invalidNumber'))
      return
    }
    const val = pct / 100 // 百分比 -> 小数
    if (Math.abs(val) >= 1) {
      setEditError(t('anomaly.returnTooLarge'))
      return
    }
    try {
      await patchMonthlyReturn(mrId, val)
      setEditingId(null)
      setEditValue('')
      setEditError('')
    } catch {
      // error handled by store
    }
  }

  return (
    <div className="card p-5 mb-5">
      <h2 className="text-base font-medium mb-4 text-fg">
        {t('anomaly.heading')}{' '}
        <span className="text-xs bg-accent-soft text-accent px-2 py-0.5 rounded-full ml-2">
          {t('anomaly.countSuffix', { n: anomalies.length })}
        </span>
      </h2>

      <div className="mb-4">
        <select
          className="text-sm border border-border-strong rounded px-3 py-1.5 bg-surface text-fg"
          value={filterFund}
          onChange={e => setFilterFund(e.target.value)}
        >
          <option value="">{t('anomaly.filterAll', { n: anomalies.length })}</option>
          {fundOptions.map(([name, count]) => (
            <option key={name} value={name}>
              {t('anomaly.filterOption', { name, n: count })}
            </option>
          ))}
        </select>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th text-left px-3 py-2.5">{t('anomaly.fund')}</th>
              <th className="th text-left px-3 py-2.5">{t('anomaly.type')}</th>
              <th className="th text-left px-3 py-2.5">{t('anomaly.date')}</th>
              <th className="th text-left px-3 py-2.5">{t('anomaly.return')}</th>
              <th className="th text-left px-3 py-2.5">Z-Score</th>
              <th className="th text-left px-3 py-2.5">{t('anomaly.threshold')}</th>
              <th className="th text-left px-3 py-2.5">{t('anomaly.median')}</th>
              <th className="th text-left px-3 py-2.5">{t('anomaly.stdev')}</th>
              <th className="th text-left px-3 py-2.5">{t('anomaly.actionReason')}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(a => {
              const isRba = a.type === 'rba_missing'
              return (
                <tr key={a.id} className="border-b border-border even:bg-sunken hover:bg-accent-soft">
                  <td className="py-2.5 px-3 text-fg">{a.fund_name ?? a.fund_id}</td>
                  <td className="py-2.5 px-3">{typeBadge(a, t)}</td>
                  <td className="py-2.5 px-3 text-fg">{a.date}</td>
                  <td className="num py-2.5 px-3">{a.value != null ? `${(a.value * 100).toFixed(2)}%` : '-'}</td>
                  <td
                    className={`num py-2.5 px-3 font-medium ${
                      a.z_score != null && Math.abs(a.z_score) >= 3
                        ? 'text-neg'
                        : a.z_score != null && Math.abs(a.z_score) >= 2.5
                          ? 'text-warn'
                          : 'text-fg'
                    }`}
                  >
                    {a.z_score != null ? a.z_score.toFixed(2) : '-'}
                  </td>
                  <td className="num py-2.5 px-3">{a.threshold_sigma != null ? a.threshold_sigma : '-'}</td>
                  <td className="num py-2.5 px-3">{a.mean != null ? `${(a.mean * 100).toFixed(2)}%` : '-'}</td>
                  <td className="num py-2.5 px-3">{a.stdev != null ? `${(a.stdev * 100).toFixed(2)}%` : '-'}</td>
                  <td className="py-2.5 px-3">
                    {isRba ? (
                      <span className="text-xs text-fg-muted">{a.reason ?? t('anomaly.rbaMissing')}</span>
                    ) : editingId === a.monthly_return_id ? (
                      <span className="flex gap-1 items-center">
                        <input
                          className="w-20 text-xs border border-border-strong bg-surface text-fg rounded px-1.5 py-0.5"
                          type="number"
                          step="0.01"
                          value={editValue}
                          onChange={e => setEditValue(e.target.value)}
                          placeholder="5.00"
                        />
                        <span className="text-xs text-fg-subtle">%</span>
                        <button
                          className="text-xs text-accent-fg bg-accent rounded px-2 py-0.5"
                          onClick={() => a.monthly_return_id != null && handleEdit(a.monthly_return_id)}
                        >
                          {t('anomaly.confirm')}
                        </button>
                        <button
                          className="text-xs text-fg-muted"
                          onClick={() => { setEditingId(null); setEditError('') }}
                        >
                          {t('anomaly.cancel')}
                        </button>
                        {editError && <span className="text-xs text-neg">{editError}</span>}
                      </span>
                    ) : (
                      <button
                        className="text-xs text-fg-muted border border-border-strong rounded px-2 py-0.5 hover:bg-sunken disabled:opacity-40 disabled:cursor-not-allowed"
                        disabled={a.monthly_return_id == null}
                        onClick={() => {
                          if (a.monthly_return_id == null) return
                          setEditingId(a.monthly_return_id)
                          setEditValue(a.value != null ? (a.value * 100).toFixed(4) : '')
                          setEditError('')
                        }}
                      >
                        {t('anomaly.fix')}
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={9} className="py-8 text-center text-fg-subtle">
                  {t('anomaly.noData')}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
