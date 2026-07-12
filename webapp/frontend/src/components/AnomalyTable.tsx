import { useState, useMemo } from 'react'
import { useStore } from '../store/useStore'

export default function AnomalyTable() {
  const anomalies = useStore(s => s.anomalies)
  const patchMonthlyReturn = useStore(s => s.patchMonthlyReturn)
  const [filterFund, setFilterFund] = useState<string>('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editValue, setEditValue] = useState('')

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

  const handleEdit = async (id: number) => {
    const val = parseFloat(editValue)
    if (isNaN(val)) return
    try {
      await patchMonthlyReturn(id, val)
      setEditingId(null)
      setEditValue('')
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
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">日期</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">收益率</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">Z-Score</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">阈值</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">均值</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">标准差</th>
              <th className="text-left py-2.5 px-3 text-gray-500 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(a => (
              <tr key={a.id} className="border-b border-gray-50 hover:bg-gray-50">
                <td className="py-2.5 px-3">{a.fund_name ?? a.fund_id}</td>
                <td className="py-2.5 px-3">{a.date}</td>
                <td className="py-2.5 px-3">{(a.value * 100).toFixed(2)}%</td>
                <td
                  className={`py-2.5 px-3 font-medium ${
                    a.z_score >= 3 ? 'text-red-600' : a.z_score >= 2.5 ? 'text-orange-500' : ''
                  }`}
                >
                  {a.z_score.toFixed(2)}
                </td>
                <td className="py-2.5 px-3">{a.threshold_sigma}</td>
                <td className="py-2.5 px-3">{(a.mean * 100).toFixed(2)}%</td>
                <td className="py-2.5 px-3">{(a.stdev * 100).toFixed(2)}%</td>
                <td className="py-2.5 px-3">
                  {editingId === a.id ? (
                    <span className="flex gap-1">
                      <input
                        className="w-20 text-xs border border-gray-200 rounded px-1.5 py-0.5"
                        type="number"
                        step="0.0001"
                        value={editValue}
                        onChange={e => setEditValue(e.target.value)}
                        placeholder="新值"
                      />
                      <button
                        className="text-xs text-white bg-blue-500 rounded px-2 py-0.5"
                        onClick={() => handleEdit(a.id)}
                      >
                        确认
                      </button>
                      <button className="text-xs text-gray-500" onClick={() => setEditingId(null)}>
                        取消
                      </button>
                    </span>
                  ) : (
                    <button
                      className="text-xs text-gray-500 border border-gray-200 rounded px-2 py-0.5 hover:bg-gray-50"
                      onClick={() => {
                        setEditingId(a.id)
                        setEditValue('')
                      }}
                    >
                      纠错
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={8} className="py-8 text-center text-gray-400">
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
