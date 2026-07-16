import { useEffect, useState } from 'react'
import { useStore } from '../store/useStore'
import { api } from '../api/client'
import type { IngestJob, PendingReview } from '../types'

const POLL_MS = 1500

export default function FundManagement() {
  const funds = useStore(s => s.funds)
  const fundsLoading = useStore(s => s.fundsLoading)
  const fetchFunds = useStore(s => s.fetchFunds)
  const recomputeFund = useStore(s => s.recomputeFund)
  const deleteFund = useStore(s => s.deleteFund)

  const [recomputing, setRecomputing] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)

  // 摄取表单
  const [addForm, setAddForm] = useState({
    fund_id: '',
    fund_name: '',
    apir_code: '',
    confirmed_url: '',
    issuer: '',
    issuer_domain: '',
    asx_code: '',
  })
  const [addError, setAddError] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)

  // job 状态
  const [job, setJob] = useState<IngestJob | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // 审核抽屉
  const [reviewFund, setReviewFund] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingReview[]>([])
  const [pendingLoading, setPendingLoading] = useState(false)

  useEffect(() => {
    fetchFunds()
  }, [])

  // job 轮询
  useEffect(() => {
    if (!job) return
    if (job.state === 'succeeded' || job.state === 'failed') return
    const t = setInterval(async () => {
      try {
        const j = await api.getIngestJob(job.job_id)
        setJob(j)
        if (j.state === 'succeeded') {
          await fetchFunds()
        }
      } catch {
        // ignore
      }
    }, POLL_MS)
    return () => clearInterval(t)
  }, [job?.job_id, job?.state])

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

  const openReview = async (fundId: string) => {
    setReviewFund(fundId)
    setPendingLoading(true)
    try {
      const list = await api.listPending(fundId)
      setPending(list)
    } catch (e: unknown) {
      // eslint-disable-next-line no-alert
      alert((e as Error).message)
    }
    setPendingLoading(false)
  }

  const handleApprove = async (id: number) => {
    const resp = await api.approvePending(id)
    // 权威源已覆盖 (新 action=skipped_authoritative_covered; 兼容旧 action=skipped_l3_covered)
    if (resp.action === 'skipped_authoritative_covered' || resp.action === 'skipped_l3_covered') {
      const tag = resp.existing_tag
      const tagLabel =
        tag === 'fundmonitors_table' ? 'L3 fundmonitors 表'
        : tag === 'llm' ? 'LLM PDF 提取'
        : (tag ?? '权威源')
      // eslint-disable-next-line no-alert
      alert(`该月已由权威源 (${tagLabel}) 覆盖, pending 未采纳。`)
    }
    setPending(pending.filter(p => p.id !== id))
    await fetchFunds()
  }

  const handleReject = async (id: number) => {
    await api.rejectPending(id, '')
    setPending(pending.filter(p => p.id !== id))
    await fetchFunds()
  }

  const handleAdd = async () => {
    setAddError('')
    if (!addForm.fund_name.trim()) {
      setAddError('基金名 必填 (其余均选填)')
      return
    }
    if (addForm.apir_code && !/^[A-Z]{3}\d{4}AU$/.test(addForm.apir_code)) {
      setAddError('APIR 格式应为 3大写字母+4数字+AU（如 ETL5010AU）')
      return
    }
    setSubmitting(true)
    try {
      const j = await api.startIngest({
        fund_name: addForm.fund_name,
        fund_id: addForm.fund_id || null,
        apir_code: addForm.apir_code || null,
        confirmed_url: addForm.confirmed_url || null,
        issuer: addForm.issuer || null,
        issuer_domain: addForm.issuer_domain || null,
        asx_code: addForm.asx_code || null,
      })
      setJob(j)
    } catch (e: unknown) {
      setAddError((e as Error).message)
    }
    setSubmitting(false)
  }

  const closeModal = () => {
    setShowAdd(false)
    setJob(null)
    setShowAdvanced(false)
    setAddForm({
      fund_id: '', fund_name: '', apir_code: '',
      confirmed_url: '', issuer: '', issuer_domain: '', asx_code: '',
    })
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
              <th className="text-left py-3 px-4 text-gray-500 font-medium">数据状态</th>
              <th className="text-left py-3 px-4 text-gray-500 font-medium">待审</th>
              <th className="text-left py-3 px-4 text-gray-500 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {funds.map(f => (
              <tr key={f.fund_id} className={`border-b border-gray-50 ${f.gap_count > 0 ? 'bg-red-50' : 'hover:bg-gray-50'}`}>
                <td className="py-3 px-4 text-gray-500 text-xs">{f.fund_id}</td>
                <td className="py-3 px-4 font-medium">{f.fund_name}</td>
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
                  {f.pending_count > 0 ? (
                    <button
                      className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-0.5 hover:bg-amber-100"
                      onClick={() => openReview(f.fund_id)}
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
                    disabled={recomputing === f.fund_id}
                    title={f.gap_count > 0 ? '该基金有数据缺口，重算将失败' : undefined}
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
                <td colSpan={7} className="py-10 text-center text-gray-400">
                  暂无基金。点右上"+ 添加基金"起 LLM 摄取任务。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* 添加基金弹窗 (LLM 摄取版) */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-lg max-h-[85vh] overflow-y-auto">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-base font-medium">添加基金 (LLM 摄取)</h2>
              <button className="text-gray-400 text-xl" onClick={closeModal}>
                &times;
              </button>
            </div>

            {job ? (
              <IngestProgress job={job} onClose={closeModal} />
            ) : (
              <div className="space-y-3">
                <div>
                  <label className="text-xs text-gray-500 block mb-1">
                    基金名 <span className="text-red-500">*</span>
                    <span className="text-gray-400 ml-1">(唯一必填)</span>
                  </label>
                  <input
                    className="w-full text-sm border border-gray-200 rounded px-3 py-2"
                    value={addForm.fund_name}
                    onChange={e => setAddForm({ ...addForm, fund_name: e.target.value })}
                    placeholder="如 Bentham Global Income Fund"
                  />
                  <div className="text-xs text-gray-400 mt-1">
                    提交后 Gemini 会自动联网找归档页并抓月度数据。
                  </div>
                </div>

                <button
                  type="button"
                  className="text-xs text-gray-500 hover:text-gray-700 underline"
                  onClick={() => setShowAdvanced(v => !v)}
                >
                  {showAdvanced ? '▼' : '▶'} 高级选项 (全部选填, 用于加速/纠错定位)
                </button>

                {showAdvanced && (
                  <div className="space-y-3 border-l-2 border-gray-100 pl-3">
                    <div>
                      <label className="text-xs text-gray-500 block mb-1">
                        fund_id <span className="text-gray-400">(选填 -- 留空由基金名自动生成 slug)</span>
                      </label>
                      <input
                        className="w-full text-sm border border-gray-200 rounded px-3 py-2"
                        value={addForm.fund_id}
                        onChange={e => setAddForm({ ...addForm, fund_id: e.target.value })}
                        placeholder="如 bentham_global_income_fund"
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
                      <label className="text-xs text-gray-500 block mb-1">归档页 URL (跳过搜索)</label>
                      <input
                        className="w-full text-sm border border-gray-200 rounded px-3 py-2"
                        value={addForm.confirmed_url}
                        onChange={e => setAddForm({ ...addForm, confirmed_url: e.target.value })}
                        placeholder="https://.../monthly-reports"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-500 block mb-1">发行商 (加速搜索)</label>
                      <input
                        className="w-full text-sm border border-gray-200 rounded px-3 py-2"
                        value={addForm.issuer}
                        onChange={e => setAddForm({ ...addForm, issuer: e.target.value })}
                        placeholder="如 Bentham Asset Management"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-500 block mb-1">发行商官网域名</label>
                      <input
                        className="w-full text-sm border border-gray-200 rounded px-3 py-2"
                        value={addForm.issuer_domain}
                        onChange={e => setAddForm({ ...addForm, issuer_domain: e.target.value })}
                        placeholder="如 benthamam.com"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-500 block mb-1">ASX 代码</label>
                      <input
                        className="w-full text-sm border border-gray-200 rounded px-3 py-2"
                        value={addForm.asx_code}
                        onChange={e => setAddForm({ ...addForm, asx_code: e.target.value })}
                        placeholder="如 MXT"
                      />
                    </div>
                  </div>
                )}

                {addError && <div className="text-xs text-red-500">{addError}</div>}
                <button
                  className="w-full text-sm bg-[#1a1a2e] text-white py-2 rounded-lg hover:bg-[#2a2a4e] disabled:opacity-50"
                  onClick={handleAdd}
                  disabled={submitting}
                >
                  {submitting ? '起任务中…' : '开始 LLM 摄取'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 审核抽屉 */}
      {reviewFund && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-3xl max-h-[85vh] overflow-y-auto">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-base font-medium">
                待审核: {reviewFund} ({pending.length} 条)
              </h2>
              <button className="text-gray-400 text-xl" onClick={() => setReviewFund(null)}>
                &times;
              </button>
            </div>
            {pendingLoading && <div className="text-gray-400 text-sm">加载中…</div>}
            {!pendingLoading && pending.length === 0 && (
              <div className="text-gray-400 text-sm py-6 text-center">无待审记录</div>
            )}
            <div className="space-y-3">
              {pending.map(p => (
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
                      onClick={() => handleApprove(p.id)}
                    >
                      通过 (写入 monthly_returns)
                    </button>
                    <button
                      className="text-xs bg-gray-200 text-gray-700 px-3 py-1 rounded hover:bg-gray-300"
                      onClick={() => handleReject(p.id)}
                    >
                      拒绝
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function IngestProgress({ job, onClose }: { job: IngestJob; onClose: () => void }) {
  const done = job.state === 'succeeded' || job.state === 'failed'
  const badge = {
    queued: 'bg-gray-100 text-gray-600',
    discovering: 'bg-blue-100 text-blue-700',
    ingesting: 'bg-blue-100 text-blue-700',
    succeeded: 'bg-green-100 text-green-700',
    failed: 'bg-red-100 text-red-700',
  }[job.state]

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className={`text-xs px-2 py-0.5 rounded ${badge}`}>{job.state}</span>
        <span className="text-xs text-gray-500">job_id: {job.job_id}</span>
      </div>
      {job.stats && (
        <div className="text-xs bg-gray-50 border border-gray-100 rounded p-2">
          <div>monthly: <b>{job.stats.monthly ?? 0}</b>  ·  pending: <b>{job.stats.pending ?? 0}</b>  ·  gap: <b>{job.stats.gap ?? 0}</b>  ·  download_fail: <b>{job.stats.download_fail ?? 0}</b></div>
        </div>
      )}
      {job.error && (
        <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded p-2">
          {job.error}
        </div>
      )}
      <div>
        <div className="text-xs text-gray-500 mb-1">进度日志 (最近 {job.log_tail?.length ?? 0} 条):</div>
        <div className="text-xs bg-black text-green-300 font-mono rounded p-2 max-h-64 overflow-auto">
          {(job.log_tail ?? []).map((l, i) => (
            <div key={i}>{l}</div>
          ))}
        </div>
      </div>
      {done && (
        <button
          className="w-full text-sm bg-[#1a1a2e] text-white py-2 rounded-lg hover:bg-[#2a2a4e]"
          onClick={onClose}
        >
          关闭
        </button>
      )}
    </div>
  )
}
