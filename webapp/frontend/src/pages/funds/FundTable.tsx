import type { Fund } from '../../types'
import { useT } from '../../i18n/useT'

interface FundTableProps {
  funds: Fund[]
  fundsLoading: boolean
  activeJobs: Record<string, string>
  recomputing: string | null
  updatingFundId: string | null
  deleteConfirm: string | null
  onRecompute: (fundId: string) => void
  onUpdate: (fund: Fund) => void
  onToggleHidden: (fund: Fund) => void
  onRequestDelete: (fundId: string) => void
  onConfirmDelete: (fundId: string) => void
  onCancelDelete: () => void
  onOpenReview: (fundId: string) => void
  onOpenData: (fund: Fund) => void
}

export default function FundTable({
  funds,
  fundsLoading,
  activeJobs,
  recomputing,
  updatingFundId,
  deleteConfirm,
  onRecompute,
  onUpdate,
  onToggleHidden,
  onRequestDelete,
  onConfirmDelete,
  onCancelDelete,
  onOpenReview,
  onOpenData,
}: FundTableProps) {
  const t = useT()
  return (
    <div className="card overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <th className="th text-left py-3 px-4">{t('funds.colId')}</th>
            <th className="th text-left py-3 px-4">{t('funds.colName')}</th>
            <th className="th text-left py-3 px-4">{t('funds.colSourceName')}</th>
            <th className="th text-left py-3 px-4">APIR</th>
            <th className="th text-left py-3 px-4">{t('funds.colDataThrough')}</th>
            <th className="th text-left py-3 px-4">{t('funds.colDataStatus')}</th>
            <th className="th text-left py-3 px-4">{t('funds.colLiveStatus')}</th>
            <th className="th text-left py-3 px-4">{t('funds.colPending')}</th>
            <th className="th text-left py-3 px-4">{t('funds.colActions')}</th>
          </tr>
        </thead>
        <tbody>
          {funds.map(f => (
            <tr
              key={f.fund_id}
              className={`border-b border-border ${
                f.is_hidden
                  ? 'bg-sunken opacity-60'
                  : f.gap_count > 0
                    ? 'bg-neg-soft'
                    : 'even:bg-sunken hover:bg-accent-soft'
              }`}
            >
              <td className="py-3 px-4 text-fg-muted text-xs">{f.fund_id}</td>
              <td className="py-3 px-4 font-medium text-fg">
                {f.fund_name}
                {f.is_hidden && (
                  <span className="ml-1.5 text-xs text-fg-subtle font-normal">{t('funds.hiddenTag')}</span>
                )}
              </td>
              <td
                className={
                  f.discovered_source_name
                    && f.discovered_source_name !== f.fund_name
                    ? "py-3 px-4 text-neg font-semibold"
                    : "py-3 px-4 text-fg-muted"
                }
                title={
                  f.discovered_source_name
                    && f.discovered_source_name !== f.fund_name
                    ? t('funds.nameCheckTip', { input: f.fund_name, discovered: f.discovered_source_name })
                    : undefined
                }
              >
                {f.discovered_source_name ?? '—'}
              </td>
              <td className="py-3 px-4 text-fg-muted">{f.apir_code ?? '—'}</td>
              <td className="py-3 px-4 text-fg-muted">{f.data_cutoff_month ?? '—'}</td>
              <td className="py-3 px-4">
                {f.gap_count > 0 ? (
                  <span className="text-neg text-xs font-medium">{t('funds.gapMonths', { n: f.gap_count })}</span>
                ) : (
                  <span className="text-pos text-xs">{t('funds.statusComplete')}</span>
                )}
              </td>
              <td className="py-3 px-4">
                {(() => {
                  const st = activeJobs[f.fund_id]
                  if (!st) return <span className="text-fg-subtle text-xs">—</span>
                  const label = st === 'ingesting_l2_pdf' ? t('funds.statusExtracting') : t('funds.statusSearching')
                  return (
                    <span className="text-xs text-accent bg-accent-soft rounded px-2 py-0.5">
                      {label}
                    </span>
                  )
                })()}
              </td>
              <td className="py-3 px-4">
                {f.pending_count > 0 ? (
                  <button
                    className="text-xs text-warn bg-warn-soft border border-warn-border rounded px-2 py-0.5 hover:opacity-90"
                    onClick={() => onOpenReview(f.fund_id)}
                  >
                    {f.pending_count} {t('funds.colPending')}
                  </button>
                ) : (
                  <span className="text-fg-subtle text-xs">—</span>
                )}
              </td>
              <td className="py-3 px-4">
                <button
                  className="text-xs text-accent border border-border-strong rounded px-2.5 py-1 mr-2 hover:bg-accent-soft disabled:opacity-50"
                  disabled={!!activeJobs[f.fund_id] || updatingFundId === f.fund_id}
                  onClick={() => onUpdate(f)}
                >
                  {updatingFundId === f.fund_id ? t('funds.startingJob') : t('funds.updateData')}
                </button>
                <button
                  className="text-xs text-accent border border-border-strong rounded px-2.5 py-1 mr-2 hover:bg-accent-soft disabled:opacity-50"
                  disabled={recomputing === f.fund_id}
                  title={f.gap_count > 0 ? t('funds.gapBlocksRecompute') : undefined}
                  onClick={() => onRecompute(f.fund_id)}
                >
                  {recomputing === f.fund_id ? t('funds.computing') : t('funds.recompute')}
                </button>
                <button
                  className="text-xs text-accent border border-border-strong rounded px-2.5 py-1 mr-2 hover:bg-accent-soft"
                  onClick={() => onOpenData(f)}
                >
                  {t('funds.viewData')}
                </button>
                <button
                  className="text-xs text-fg-muted border border-border-strong rounded px-2.5 py-1 mr-2 hover:bg-sunken"
                  title={t('funds.hideHint')}
                  onClick={() => onToggleHidden(f)}
                >
                  {f.is_hidden ? t('funds.unhide') : t('funds.hide')}
                </button>
                {deleteConfirm === f.fund_id ? (
                  <span className="text-xs">
                    {t('funds.deleteConfirmPrompt')}
                    <button className="text-neg ml-1 mr-1" onClick={() => onConfirmDelete(f.fund_id)}>
                      {t('funds.yes')}
                    </button>
                    <button className="text-fg-muted" onClick={onCancelDelete}>
                      {t('funds.no')}
                    </button>
                  </span>
                ) : (
                  <button
                    className="text-xs text-neg border border-neg-border rounded px-2.5 py-1 hover:bg-neg-soft"
                    onClick={() => onRequestDelete(f.fund_id)}
                  >
                    {t('funds.delete')}
                  </button>
                )}
              </td>
            </tr>
          ))}
          {funds.length === 0 && !fundsLoading && (
            <tr>
              <td colSpan={9} className="py-10 text-center text-fg-subtle">
                {t('funds.emptyState')}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
