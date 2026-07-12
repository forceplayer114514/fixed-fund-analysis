import { useMemo } from 'react'
import { useStore } from '../store/useStore'

function cardBorder(status: string) {
  switch (status) {
    case 'applied':
      return 'border-l-red-500'
    case 'watch':
      return 'border-l-orange-400'
    case 'insufficient':
      return 'border-l-gray-300'
    default:
      return 'border-l-green-500'
  }
}

function statusTag(status: string) {
  switch (status) {
    case 'applied':
      return (
        <span className="text-xs bg-red-50 text-red-700 px-2 py-0.5 rounded-full font-medium">
          需去平滑
        </span>
      )
    case 'watch':
      return (
        <span className="text-xs bg-orange-50 text-orange-700 px-2 py-0.5 rounded-full font-medium">
          建议关注
        </span>
      )
    case 'insufficient':
      return (
        <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full font-medium">
          数据不足
        </span>
      )
    default:
      return (
        <span className="text-xs bg-green-50 text-green-700 px-2 py-0.5 rounded-full font-medium">
          无需去平滑
        </span>
      )
  }
}

export default function SmoothingCards() {
  const compareData = useStore(s => s.compareData)

  const cards = useMemo(() => {
    if (!compareData?.funds) return []
    return compareData.funds.map((m: any) => {
      const historyMonths = m.history_months ?? 0
      const phi = m.unsmoothing_coefficient_phi ?? 0
      const q = m.ljung_box_q ?? 0
      const isSignificant = m.is_q_significant ?? false
      const isGeltner = m.is_geltner_applied ?? false
      const isShort = m.is_short_history_warning ?? true

      let status: string
      let prob: number | null
      let note = ''

      if (isShort) {
        status = 'insufficient'
        prob = null
        note = `防火墙 1 未通过：历史数据 ${historyMonths} 个月，不足 36 个月，无法检验自相关性`
      } else if (isGeltner) {
        status = 'applied'
        prob = Math.min((q / (historyMonths - 1)) * 100, 99.9)
        note = '三重防火墙全部通过，已应用 Geltner 去平滑'
      } else if (phi > 0 && !isSignificant) {
        status = 'watch'
        prob = Math.min((q / (historyMonths - 1)) * 100, 99.9)
        note = 'φ 为正但未达显著，建议持续观测'
      } else {
        status = 'none'
        prob = null
        note = '自相关性不显著（φ≈0 或 Q 检验未通过），无需去平滑'
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
  }, [compareData])

  if (cards.length === 0) return null

  return (
    <div className="bg-white rounded-lg p-5 shadow-sm">
      <h2 className="text-base font-medium mb-4">
        去平滑分析（Geltner 检验）{' '}
        <span className="text-xs bg-cyan-50 text-cyan-700 px-2 py-0.5 rounded-full ml-2">
          {cards.length} 支基金
        </span>
      </h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {cards.map(c => (
          <div
            key={c.fund_id}
            className={`bg-gray-50 rounded-lg p-4 border-l-4 ${cardBorder(c.status)}`}
          >
            <div className="mb-3">{statusTag(c.status)}</div>
            <div className="font-medium text-sm mb-3">{c.fund_name}</div>
            <div className="space-y-1.5 text-xs text-gray-600">
              <div className="flex justify-between">
                <span className="text-gray-400">自相关系数 φ</span>
                <span>{c.phi.toFixed(4)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">Ljung-Box Q</span>
                <span>{c.q.toFixed(4)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">数据月数</span>
                <span>
                  {c.historyMonths} 个月 {c.historyMonths >= 36 ? '✓' : '✗'}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">人为干预概率</span>
                <span
                  className={`font-semibold ${
                    c.status === 'applied'
                      ? 'text-red-600'
                      : c.status === 'watch'
                        ? 'text-orange-500'
                        : 'text-gray-400'
                  }`}
                >
                  {c.prob != null ? `${c.prob.toFixed(1)}%` : '无法判定'}
                </span>
              </div>
              {c.prob != null && (
                <div className="h-1.5 bg-gray-200 rounded-full mt-1 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${c.status === 'applied' ? 'bg-red-500' : 'bg-orange-400'}`}
                    style={{ width: `${Math.min(c.prob, 100)}%` }}
                  />
                </div>
              )}
            </div>
            <div className="text-xs text-gray-400 mt-3">{c.note}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
