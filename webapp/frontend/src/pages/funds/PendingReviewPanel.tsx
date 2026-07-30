import type { PendingReview } from '../../types'

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
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl p-6 w-full max-w-3xl max-h-[85vh] overflow-y-auto">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-base font-medium">
            待审核: {fundId} ({items.length} 条)
          </h2>
          <button className="text-gray-400 text-xl" onClick={onClose}>
            &times;
          </button>
        </div>
        {loading && <div className="text-gray-400 text-sm">加载中…</div>}
        {!loading && items.length === 0 && (
          <div className="text-gray-400 text-sm py-6 text-center">无待审记录</div>
        )}
        <div className="space-y-3">
          {items.map(p => (
            <div key={p.id} className="border border-amber-200 bg-amber-50/40 rounded-lg p-3 text-sm">
              <div className="flex justify-between items-start">
                <div>
                  <span className="font-medium">{p.date.slice(0, 7)}</span>{' '}
                  <span className="text-gray-600">
                    月度净收益: {(p.net_return * 100).toFixed(4)}%
                  </span>
                </div>
                <div className="text-xs text-gray-500">gate: {p.gate_result ?? '—'}</div>
              </div>
              <div className="mt-1 text-xs text-red-600">未过闸: {p.review_reason ?? '—'}</div>
              {p.source_quote && (
                <div className="mt-2 text-xs text-gray-500 bg-white border border-gray-100 rounded p-2">
                  <div className="text-gray-400 mb-1">source_quote:</div>
                  <div className="whitespace-pre-wrap break-words">{p.source_quote}</div>
                </div>
              )}
              <div className="mt-2 flex gap-2">
                <button
                  className="text-xs bg-green-600 text-white px-3 py-1 rounded hover:bg-green-700"
                  onClick={() => onApprove(p.id)}
                >
                  通过 (写入 monthly_returns)
                </button>
                <button
                  className="text-xs bg-gray-200 text-gray-700 px-3 py-1 rounded hover:bg-gray-300"
                  onClick={() => onReject(p.id)}
                >
                  拒绝
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
