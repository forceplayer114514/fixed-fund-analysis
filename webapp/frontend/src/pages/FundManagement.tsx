import { useEffect, useState } from 'react'
import { useStore } from '../store/useStore'
import { api } from '../api/client'

export default function FundManagement() {
  const funds = useStore(s => s.funds)
  const fundsLoading = useStore(s => s.fundsLoading)
  const fetchFunds = useStore(s => s.fetchFunds)
  const recomputeFund = useStore(s => s.recomputeFund)
  const deleteFund = useStore(s => s.deleteFund)
  const [recomputing, setRecomputing] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [addForm, setAddForm] = useState({
    fund_id: '',
    fund_name: '',
    apir_code: '',
    confirmed_url: '',
    fetch_method: 'pdf',
    url_type: '',
  })
  const [addError, setAddError] = useState('')

  useEffect(() => {
    fetchFunds()
  }, [])

  const handleRecompute = async (fundId: string) => {
    setRecomputing(fundId)
    try {
      await recomputeFund(fundId)
    } catch {
      // error handled by store
    }
    setRecomputing(null)
  }

  const handleDelete = async (fundId: string) => {
    try {
      await deleteFund(fundId)
    } catch {
      // error handled by store
    }
    setDeleteConfirm(null)
  }

  const handleAdd = async () => {
    setAddError('')
    try {
      await api.createFund({
        fund_id: addForm.fund_id,
        fund_name: addForm.fund_name,
        apir_code: addForm.apir_code || null,
        confirmed_url: addForm.confirmed_url,
        fetch_method: addForm.fetch_method,
        url_type: addForm.url_type,
      })
      setShowAdd(false)
      setAddForm({
        fund_id: '',
        fund_name: '',
        apir_code: '',
        confirmed_url: '',
        fetch_method: 'pdf',
        url_type: '',
      })
      await fetchFunds()
    } catch (e: unknown) {
      setAddError((e as Error).message)
    }
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-5">
        <h1 className="text-xl font-semibold">基金管理</h1>
        <button
          className="text-sm bg-[#1a1a2e] text-white px-4 py-2 rounded-lg hover:bg-[#2a2a4e]"
          onClick={() => setShowAdd(true)}
        >
          + 添加基金
        </button>
      </div>

      {fundsLoading && <div className="text-gray-400">加载中...</div>}

      <div className="bg-white rounded-lg shadow-sm overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b-2 border-gray-100 bg-gray-50">
              <th className="text-left py-3 px-4 text-gray-500 font-medium">基金 ID</th>
              <th className="text-left py-3 px-4 text-gray-500 font-medium">基金名称</th>
              <th className="text-left py-3 px-4 text-gray-500 font-medium">APIR</th>
              <th className="text-left py-3 px-4 text-gray-500 font-medium">数据截止</th>
              <th className="text-left py-3 px-4 text-gray-500 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {funds.map(f => (
              <tr key={f.fund_id} className="border-b border-gray-50 hover:bg-gray-50">
                <td className="py-3 px-4 text-gray-500 text-xs">{f.fund_id}</td>
                <td className="py-3 px-4 font-medium">{f.fund_name}</td>
                <td className="py-3 px-4 text-gray-500">{f.apir_code ?? '—'}</td>
                <td className="py-3 px-4 text-gray-500">{f.data_cutoff_month ?? '—'}</td>
                <td className="py-3 px-4">
                  <button
                    className="text-xs text-blue-600 border border-blue-200 rounded px-2.5 py-1 mr-2 hover:bg-blue-50 disabled:opacity-50"
                    disabled={recomputing === f.fund_id}
                    onClick={() => handleRecompute(f.fund_id)}
                  >
                    {recomputing === f.fund_id ? '计算中...' : '重算'}
                  </button>
                  {deleteConfirm === f.fund_id ? (
                    <span className="text-xs">
                      确认删除？
                      <button className="text-red-600 ml-1 mr-1" onClick={() => handleDelete(f.fund_id)}>
                        是
                      </button>
                      <button className="text-gray-500" onClick={() => setDeleteConfirm(null)}>
                        否
                      </button>
                    </span>
                  ) : (
                    <button
                      className="text-xs text-red-500 border border-red-200 rounded px-2.5 py-1 hover:bg-red-50"
                      onClick={() => setDeleteConfirm(f.fund_id)}
                    >
                      删除
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {funds.length === 0 && !fundsLoading && (
              <tr>
                <td colSpan={5} className="py-10 text-center text-gray-400">
                  暂无基金数据，请先通过 skills 端添加基金
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 添加基金弹窗 */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-lg max-h-[80vh] overflow-y-auto">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-base font-medium">添加基金</h2>
              <button className="text-gray-400 text-xl" onClick={() => setShowAdd(false)}>
                &times;
              </button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-gray-500 block mb-1">fund_id *</label>
                <input
                  className="w-full text-sm border border-gray-200 rounded px-3 py-2"
                  value={addForm.fund_id}
                  onChange={e => setAddForm({ ...addForm, fund_id: e.target.value })}
                  placeholder="如 bentham_global_income_fund"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">基金名称 *</label>
                <input
                  className="w-full text-sm border border-gray-200 rounded px-3 py-2"
                  value={addForm.fund_name}
                  onChange={e => setAddForm({ ...addForm, fund_name: e.target.value })}
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">APIR 代码</label>
                <input
                  className="w-full text-sm border border-gray-200 rounded px-3 py-2"
                  value={addForm.apir_code}
                  onChange={e => setAddForm({ ...addForm, apir_code: e.target.value })}
                  placeholder="如 ETL5010AU"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">事实单 URL *</label>
                <input
                  className="w-full text-sm border border-gray-200 rounded px-3 py-2"
                  value={addForm.confirmed_url}
                  onChange={e => setAddForm({ ...addForm, confirmed_url: e.target.value })}
                />
              </div>
              <div className="flex gap-3">
                <div className="flex-1">
                  <label className="text-xs text-gray-500 block mb-1">抓取方式 *</label>
                  <select
                    className="w-full text-sm border border-gray-200 rounded px-3 py-2 bg-white"
                    value={addForm.fetch_method}
                    onChange={e => setAddForm({ ...addForm, fetch_method: e.target.value })}
                  >
                    <option value="pdf">PDF</option>
                    <option value="html_plotly">HTML</option>
                  </select>
                </div>
                <div className="flex-1">
                  <label className="text-xs text-gray-500 block mb-1">URL 类型 *</label>
                  <input
                    className="w-full text-sm border border-gray-200 rounded px-3 py-2"
                    value={addForm.url_type}
                    onChange={e => setAddForm({ ...addForm, url_type: e.target.value })}
                    placeholder="如 factsheet"
                  />
                </div>
              </div>
              {addError && <div className="text-xs text-red-500">{addError}</div>}
              <div className="text-xs text-gray-400">
                仅注册元信息，数据抓取需在 skills 端运行 /add_fixed_fund
              </div>
              <button
                className="w-full text-sm bg-[#1a1a2e] text-white py-2 rounded-lg hover:bg-[#2a2a4e]"
                onClick={handleAdd}
              >
                确认添加
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
