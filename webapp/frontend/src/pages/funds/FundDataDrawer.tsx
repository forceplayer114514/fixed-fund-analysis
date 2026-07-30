import type { Fund, MonthlyReturnRow } from '../../types'

export interface RbaHistoryRow {
  start_month: string
  end_month: string
  rate: number
}

interface FundDataDrawerProps {
  fund: Fund | null
  returns: MonthlyReturnRow[]
  loading: boolean
  rbaHistory: RbaHistoryRow[]
  rbaHistoryLoading: boolean
  showRbaHistory: boolean
  onToggleRbaHistory: () => void
  onClose: () => void
}

function formatMonthRange(start: string, end: string): string {
  const [sy, sm] = start.split('-')
  if (start === end) return `${sy}年${Number(sm)}月`
  const [ey, em] = end.split('-')
  return sy === ey
    ? `${sy}年${Number(sm)}-${Number(em)}月`
    : `${sy}年${Number(sm)}月 ~ ${ey}年${Number(em)}月`
}

export default function FundDataDrawer({
  fund,
  returns,
  loading,
  rbaHistory,
  rbaHistoryLoading,
  showRbaHistory,
  onToggleRbaHistory,
  onClose,
}: FundDataDrawerProps) {
  return (
    <>
      {/* 查看数据面板 (月利率原始序列, 不做任何计算) */}
      {fund && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-md max-h-[85vh] overflow-y-auto">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-base font-medium">
                {fund.fund_name} 月度净收益 ({returns.length} 条)
              </h2>
              <button className="text-gray-400 text-xl" onClick={onClose}>
                &times;
              </button>
            </div>
            {loading && <div className="text-gray-400 text-sm">加载中…</div>}
            {!loading && returns.length === 0 && (
              <div className="text-gray-400 text-sm py-6 text-center">暂无数据</div>
            )}
            {!loading && returns.length > 0 && (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100">
                    <th className="text-left py-2 text-gray-500 font-medium">年月</th>
                    <th className="text-right py-2 text-gray-500 font-medium">月度净收益</th>
                  </tr>
                </thead>
                <tbody>
                  {returns.map(r => (
                    <tr key={r.date} className="border-b border-gray-50">
                      <td className="py-1.5 text-gray-700">{r.date.slice(0, 7)}</td>
                      <td className={`py-1.5 text-right ${r.net_return < 0 ? 'text-red-600' : 'text-gray-700'}`}>
                        {(r.net_return * 100).toFixed(4)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* RBA 历史利率面板 (按连续相同利率合并区间展示, 不逐月列) */}
      {showRbaHistory && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-md max-h-[85vh] overflow-y-auto">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-base font-medium">RBA 现金利率历史</h2>
              <button className="text-gray-400 text-xl" onClick={onToggleRbaHistory}>
                &times;
              </button>
            </div>
            {rbaHistoryLoading && <div className="text-gray-400 text-sm">加载中…</div>}
            {!rbaHistoryLoading && rbaHistory.length === 0 && (
              <div className="text-gray-400 text-sm py-6 text-center">暂无数据</div>
            )}
            {!rbaHistoryLoading && rbaHistory.length > 0 && (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100">
                    <th className="text-left py-2 text-gray-500 font-medium">期间</th>
                    <th className="text-right py-2 text-gray-500 font-medium">目标利率</th>
                  </tr>
                </thead>
                <tbody>
                  {[...rbaHistory].reverse().map(p => (
                    <tr key={p.start_month} className="border-b border-gray-50">
                      <td className="py-1.5 text-gray-700">
                        {formatMonthRange(p.start_month, p.end_month)}
                      </td>
                      <td className="py-1.5 text-right text-gray-700">
                        {(p.rate * 100).toFixed(2)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </>
  )
}
