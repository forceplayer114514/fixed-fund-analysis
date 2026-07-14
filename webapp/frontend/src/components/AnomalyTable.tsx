import { useState, useMemo } from 'react'
import { useStore } from '../store/useStore'
import type { Anomaly } from '../types'

function typeBadge(a: Anomaly) {
  if (a.type === 'rba_missing') {
    return (
      <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full font-medium">
        基准缺失
      </span>
    )
  }
  return (
    <span className="text-xs bg-orange-50 text-orange-700 px-2 py-0.5 rounded-full font-medium">
      离群点
    </span>
  )
}

export default function AnomalyTable() {
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
      setEditError('请输入有效数字')
      return
    }
    const val = pct / 100 // 百分比 -> 小数
    if (Math.abs(val) >= 1) {
      setEditError('收益率绝对值应小于 100%')
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
    <div className="bg-white rounded-lg p-5 shadow-sm mb-5">
      <h2 className="text-base font-medium mb-4">
        异常数据{' '}
        <span className="text-xs bg-cyan-50 text-cyan-700 px-2 py-0.5 rounded-full ml-2">
          {anomalies.length} 条
        </span>
      </h2>

      <div className="mb-4">
        <select
          className="text-sm border border-gray-200 rounded px-3 py-1.5 bg-white"
          value={filterFund}
          onChange={e => setFilterFund(e.target.value)}
        >
          <option value="">全部基金（{anomalies.length} 条异常）</option>
          {fundOptions.map(([name, count]) => (
            <option key={name} value={name}>
              {name}（{count} 条异常）
            </option>
          ))}
        </select>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b-2 border-gray-100">
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">基金</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">类型</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">日期</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">收益率</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">Z-Score</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">阈值</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">中位数</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">标准差</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">操作/原因</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(a => {
              const isRba = a.type === 'rba_missing'
              return (
                <tr key={a.id} className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="py-2.5 px-3">{a.fund_name ?? a.fund_id}</td>
                  <td className="py-2.5 px-3">{typeBadge(a)}</td>
                  <td className="py-2.5 px-3">{a.date}</td>
                  <td className="py-2.5 px-3">{a.value != null ? `${(a.value * 100).toFixed(2)}%` : '-'}</td>
                  <td
                    className={`py-2.5 px-3 font-medium ${
                      a.z_score != null && Math.abs(a.z_score) >= 3
                        ? 'text-red-600'
                        : a.z_score != null && Math.abs(a.z_score) >= 2.5
                          ? 'text-orange-500'
                          : ''
                    }`}
                  >
                    {a.z_score != null ? a.z_score.toFixed(2) : '-'}
                  </td>
                  <td className="py-2.5 px-3">{a.threshold_sigma != null ? a.threshold_sigma : '-'}</td>
                  <td className="py-2.5 px-3">{a.mean != null ? `${(a.mean * 100).toFixed(2)}%` : '-'}</td>
                  <td className="py-2.5 px-3">{a.stdev != null ? `${(a.stdev * 100).toFixed(2)}%` : '-'}</td>
                  <td className="py-2.5 px-3">
                    {isRba ? (
                      <span className="text-xs text-gray-500">{a.reason ?? 'RBA 基准缺失'}</span>
                    ) : editingId === a.monthly_return_id ? (
                      <span className="flex gap-1 items-center">
                        <input
                          className="w-20 text-xs border border-gray-200 rounded px-1.5 py-0.5"
                          type="number"
                          step="0.01"
                          value={editValue}
                          onChange={e => setEditValue(e.target.value)}
                          placeholder="5.00"
                        />
                        <span className="text-xs text-gray-400">%</span>
                        <button
                          className="text-xs text-white bg-blue-500 rounded px-2 py-0.5"
                          onClick={() => a.monthly_return_id != null && handleEdit(a.monthly_return_id)}
                        >
                          确认
                        </button>
                        <button
                          className="text-xs text-gray-500"
                          onClick={() => { setEditingId(null); setEditError('') }}
                        >
                          取消
                        </button>
                        {editError && <span className="text-xs text-red-500">{editError}</span>}
                      </span>
                    ) : (
                      <button
                        className="text-xs text-gray-500 border border-gray-200 rounded px-2 py-0.5 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                        disabled={a.monthly_return_id == null}
                        onClick={() => {
                          if (a.monthly_return_id == null) return
                          setEditingId(a.monthly_return_id)
                          setEditValue(a.value != null ? (a.value * 100).toFixed(4) : '')
                          setEditError('')
                        }}
                      >
                        纠错
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={9} className="py-8 text-center text-gray-400">
                  暂无异常数据
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
