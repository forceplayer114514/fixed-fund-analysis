import type { Fund, MonthlyReturnRow } from '../../types'
import { useT } from '../../i18n/useT'
import type { I18nKey, I18nParams } from '../../i18n'

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

function formatMonthRange(
  start: string,
  end: string,
  t: (key: I18nKey, params?: I18nParams) => string,
): string {
  const [sy, smStr] = start.split('-')
  const sm = Number(smStr)
  if (start === end) return t('funds.monthSingle', { year: sy, month: sm })
  const [ey, emStr] = end.split('-')
  const em = Number(emStr)
  return sy === ey
    ? t('funds.monthRangeSameYear', { year: sy, from: sm, to: em })
    : t('funds.monthRangeCrossYear', { fromYear: sy, fromMonth: sm, toYear: ey, toMonth: em })
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
  const t = useT()
  return (
    <>
      {/* 查看数据面板 (月利率原始序列, 不做任何计算) */}
      {fund && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="card p-6 w-full max-w-md max-h-[85vh] overflow-y-auto">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-base font-medium text-fg">
                {t('funds.returnsDrawerTitle', { name: fund.fund_name, n: returns.length })}
              </h2>
              <button className="text-fg-subtle text-xl" onClick={onClose}>
                &times;
              </button>
            </div>
            {loading && <div className="text-fg-subtle text-sm">{t('common.loading')}</div>}
            {!loading && returns.length === 0 && (
              <div className="text-fg-subtle text-sm py-6 text-center">{t('common.noData')}</div>
            )}
            {!loading && returns.length > 0 && (
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th text-left py-2">{t('funds.colYearMonth')}</th>
                    <th className="th text-right py-2">{t('funds.colNetReturn')}</th>
                  </tr>
                </thead>
                <tbody>
                  {returns.map(r => (
                    <tr key={r.date} className="border-b border-border even:bg-sunken hover:bg-accent-soft">
                      <td className="py-1.5 text-fg">{r.date.slice(0, 7)}</td>
                      <td className={`num py-1.5 ${r.net_return < 0 ? 'text-neg' : 'text-fg'}`}>
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
          <div className="card p-6 w-full max-w-md max-h-[85vh] overflow-y-auto">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-base font-medium text-fg">{t('funds.rbaHistoryTitle')}</h2>
              <button className="text-fg-subtle text-xl" onClick={onToggleRbaHistory}>
                &times;
              </button>
            </div>
            {rbaHistoryLoading && <div className="text-fg-subtle text-sm">{t('common.loading')}</div>}
            {!rbaHistoryLoading && rbaHistory.length === 0 && (
              <div className="text-fg-subtle text-sm py-6 text-center">{t('common.noData')}</div>
            )}
            {!rbaHistoryLoading && rbaHistory.length > 0 && (
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th text-left py-2">{t('funds.colRbaPeriod')}</th>
                    <th className="th text-right py-2">{t('funds.colRbaTarget')}</th>
                  </tr>
                </thead>
                <tbody>
                  {[...rbaHistory].reverse().map(p => (
                    <tr key={p.start_month} className="border-b border-border even:bg-sunken hover:bg-accent-soft">
                      <td className="py-1.5 text-fg">
                        {formatMonthRange(p.start_month, p.end_month, t)}
                      </td>
                      <td className="num py-1.5 text-fg">
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
