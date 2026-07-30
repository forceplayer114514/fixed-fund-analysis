import type { Fund } from '../../types'

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
  return (
    <div className="bg-white rounded-lg shadow-sm overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b-2 border-gray-100 bg-gray-50">
            <th className="text-left py-3 px-4 text-gray-500 font-medium">基金 ID</th>
            <th className="text-left py-3 px-4 text-gray-500 font-medium">基金名称</th>
            <th className="text-left py-3 px-4 text-gray-500 font-medium">数据源基金名</th>
            <th className="text-left py-3 px-4 text-gray-500 font-medium">APIR</th>
            <th className="text-left py-3 px-4 text-gray-500 font-medium">数据截止</th>
            <th className="text-left py-3 px-4 text-gray-500 font-medium">数据状态</th>
            <th className="text-left py-3 px-4 text-gray-500 font-medium">实时状态</th>
            <th className="text-left py-3 px-4 text-gray-500 font-medium">待审</th>
            <th className="text-left py-3 px-4 text-gray-500 font-medium">操作</th>
          </tr>
        </thead>
        <tbody>
          {funds.map(f => (
            <tr key={f.fund_id} className={`border-b border-gray-50 ${f.is_hidden ? 'bg-gray-50 opacity-60' : f.gap_count > 0 ? 'bg-red-50' : 'hover:bg-gray-50'}`}>
              <td className="py-3 px-4 text-gray-500 text-xs">{f.fund_id}</td>
              <td className="py-3 px-4 font-medium">
                {f.fund_name}
                {f.is_hidden && (
                  <span className="ml-1.5 text-xs text-gray-400 font-normal">(已隐藏)</span>
                )}
              </td>
              <td
                className={
                  f.discovered_source_name
                    && f.discovered_source_name !== f.fund_name
                    ? "py-3 px-4 text-red-600 font-semibold"
                    : "py-3 px-4 text-gray-500"
                }
                title={
                  f.discovered_source_name
                    && f.discovered_source_name !== f.fund_name
                    ? `输入名: ${f.fund_name}\n抓到名: ${f.discovered_source_name}\n请核对是否为同一基金`
                    : undefined
                }
              >
                {f.discovered_source_name ?? '—'}
              </td>
              <td className="py-3 px-4 text-gray-500">{f.apir_code ?? '—'}</td>
              <td className="py-3 px-4 text-gray-500">{f.data_cutoff_month ?? '—'}</td>
              <td className="py-3 px-4">
                {f.gap_count > 0 ? (
                  <span className="text-red-600 text-xs font-medium">缺 {f.gap_count} 月</span>
                ) : (
                  <span className="text-green-600 text-xs">完整</span>
                )}
              </td>
              <td className="py-3 px-4">
                {(() => {
                  const st = activeJobs[f.fund_id]
                  if (!st) return <span className="text-gray-300 text-xs">—</span>
                  const label = st === 'ingesting_l2_pdf' ? '提取中' : '搜索中'
                  return (
                    <span className="text-xs text-blue-700 bg-blue-50 border border-blue-200 rounded px-2 py-0.5">
                      {label}
                    </span>
                  )
                })()}
              </td>
              <td className="py-3 px-4">
                {f.pending_count > 0 ? (
                  <button
                    className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-0.5 hover:bg-amber-100"
                    onClick={() => onOpenReview(f.fund_id)}
                  >
                    {f.pending_count} 待审
                  </button>
                ) : (
                  <span className="text-gray-300 text-xs">—</span>
                )}
              </td>
              <td className="py-3 px-4">
                <button
                  className="text-xs text-blue-600 border border-blue-200 rounded px-2.5 py-1 mr-2 hover:bg-blue-50 disabled:opacity-50"
                  disabled={!!activeJobs[f.fund_id] || updatingFundId === f.fund_id}
                  onClick={() => onUpdate(f)}
                >
                  {updatingFundId === f.fund_id ? '起任务中…' : '更新数据'}
                </button>
                <button
                  className="text-xs text-blue-600 border border-blue-200 rounded px-2.5 py-1 mr-2 hover:bg-blue-50 disabled:opacity-50"
                  disabled={recomputing === f.fund_id}
                  title={f.gap_count > 0 ? '该基金有数据缺口，重算将失败' : undefined}
                  onClick={() => onRecompute(f.fund_id)}
                >
                  {recomputing === f.fund_id ? '计算中...' : '重算'}
                </button>
                <button
                  className="text-xs text-blue-600 border border-blue-200 rounded px-2.5 py-1 mr-2 hover:bg-blue-50"
                  onClick={() => onOpenData(f)}
                >
                  查看数据
                </button>
                <button
                  className="text-xs text-gray-600 border border-gray-200 rounded px-2.5 py-1 mr-2 hover:bg-gray-50"
                  title="隐藏后不出现在对比看板"
                  onClick={() => onToggleHidden(f)}
                >
                  {f.is_hidden ? '取消隐藏' : '隐藏'}
                </button>
                {deleteConfirm === f.fund_id ? (
                  <span className="text-xs">
                    确认删除？
                    <button className="text-red-600 ml-1 mr-1" onClick={() => onConfirmDelete(f.fund_id)}>
                      是
                    </button>
                    <button className="text-gray-500" onClick={onCancelDelete}>
                      否
                    </button>
                  </span>
                ) : (
                  <button
                    className="text-xs text-red-500 border border-red-200 rounded px-2.5 py-1 hover:bg-red-50"
                    onClick={() => onRequestDelete(f.fund_id)}
                  >
                    删除
                  </button>
                )}
              </td>
            </tr>
          ))}
          {funds.length === 0 && !fundsLoading && (
            <tr>
              <td colSpan={9} className="py-10 text-center text-gray-400">
                暂无基金。点右上"+ 添加基金"起 LLM 摄取任务。
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
