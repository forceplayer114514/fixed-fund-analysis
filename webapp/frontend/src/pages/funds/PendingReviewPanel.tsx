import type { PendingReview } from '../../types'
import { useT } from '../../i18n/useT'

interface PendingReviewPanelProps {
  fundId: string
  items: PendingReview[]
  loading: boolean
  onApprove: (id: number) => void
  onReject: (id: number) => void
  onClose: () => void
}

export default function PendingReviewPanel({
  fundId,
  items,
  loading,
  onApprove,
  onReject,
  onClose,
}: PendingReviewPanelProps) {
  const t = useT()
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="card p-6 w-full max-w-3xl max-h-[85vh] overflow-y-auto">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-base font-medium text-fg">
            {t('funds.reviewTitle', { fundId, n: items.length })}
          </h2>
          <button className="text-fg-subtle text-xl" onClick={onClose}>
            &times;
          </button>
        </div>
        {loading && <div className="text-fg-subtle text-sm">{t('common.loading')}</div>}
        {!loading && items.length === 0 && (
          <div className="text-fg-subtle text-sm py-6 text-center">{t('funds.noPending')}</div>
        )}
        <div className="space-y-3">
          {items.map(p => (
            <div key={p.id} className="border border-warn-border bg-warn-soft rounded-lg p-3 text-sm">
              <div className="flex justify-between items-start">
                <div>
                  <span className="font-medium text-fg">{p.date.slice(0, 7)}</span>{' '}
                  <span className="text-fg-muted">
                    {t('funds.colNetReturn')}: {(p.net_return * 100).toFixed(4)}%
                  </span>
                </div>
                <div className="text-xs text-fg-muted">gate: {p.gate_result ?? '—'}</div>
              </div>
              <div className="mt-1 text-xs text-neg">{t('funds.reviewReasonLabel')}: {p.review_reason ?? '—'}</div>
              {p.source_quote && (
                <div className="mt-2 text-xs text-fg-muted bg-surface border border-border rounded p-2">
                  <div className="text-fg-subtle mb-1">source_quote:</div>
                  <div className="whitespace-pre-wrap break-words">{p.source_quote}</div>
                </div>
              )}
              <div className="mt-2 flex gap-2">
                <button
                  className="text-xs text-accent-fg bg-accent px-3 py-1 rounded hover:opacity-90"
                  onClick={() => onApprove(p.id)}
                >
                  {t('funds.approveLabel')}
                </button>
                <button
                  className="text-xs bg-sunken text-fg-muted px-3 py-1 rounded hover:opacity-90"
                  onClick={() => onReject(p.id)}
                >
                  {t('funds.reject')}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
