import { useEffect, useState } from 'react'
import { useStore } from '../store/useStore'
import { api } from '../api/client'
import type { Fund, IngestJob, MonthlyReturnRow, PendingReview } from '../types'

const POLL_MS = 1500

export default function FundManagement() {
  const funds = useStore(s => s.funds)
  const fundsLoading = useStore(s => s.fundsLoading)
  const fetchFunds = useStore(s => s.fetchFunds)
  const recomputeFund = useStore(s => s.recomputeFund)
  const deleteFund = useStore(s => s.deleteFund)

  const [recomputing, setRecomputing] = useState<string | null>(null)
  const [updatingFundId, setUpdatingFundId] = useState<string | null>(null)
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

  // 表格级活跃 job 状态 (fund_id -> state), 独立于上面弹窗内单 job 轮询
  const [activeJobs, setActiveJobs] = useState<Record<string, string>>({})

  // 审核抽屉
  const [reviewFund, setReviewFund] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingReview[]>([])
  const [pendingLoading, setPendingLoading] = useState(false)

  // 查看数据面板 (月利率原始序列)
  const [dataFund, setDataFund] = useState<Fund | null>(null)
  const [returns, setReturns] = useState<MonthlyReturnRow[]>([])
  const [returnsLoading, setReturnsLoading] = useState(false)

  const [showRbaHistory, setShowRbaHistory] = useState(false)
  const [rbaHistory, setRbaHistory] = useState<{ start_month: string; end_month: string; rate: number }[]>([])
  const [rbaHistoryLoading, setRbaHistoryLoading] = useState(false)

  useEffect(() => {
    fetchFunds()
  }, [])

  // job 轮询 (弹窗内单 job 实时日志)
  useEffect(() => {
    if (!job) return
    if (job.state === 'succeeded' || job.state === 'failed') return
    const t = setInterval(async () => {
      try {
        const j = await api.getIngestJob(job.job_id)
        setJob(j)
        if (j.state === 'succeeded' || j.state === 'failed') {
          await fetchFunds()
        }
      } catch {
        // ignore
      }
    }, POLL_MS)
    return () => clearInterval(t)
  }, [job?.job_id, job?.state])

  // 表格级活跃 job 轮询 (独立于上面那个, 不依赖弹窗是否打开/是否记得 job_id)
  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const jobs = await api.listActiveJobs()
        if (cancelled) return
        const next: Record<string, string> = {}
        jobs.forEach(j => { next[j.fund_id] = j.state })
        setActiveJobs(prev => {
          // 上一轮在, 这一轮消失 -> 该基金刚转终态, 刷新一次拿最新 gap/pending/cutoff
          const disappeared = Object.keys(prev).filter(fid => !(fid in next))
          if (disappeared.length > 0) fetchFunds()
          return next
        })
      } catch {
        // ignore
      }
    }
    tick()
    const t = setInterval(tick, POLL_MS)
    return () => { cancelled = true; clearInterval(t) }
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

  const handleUpdateData = async (f: Fund) => {
    setUpdatingFundId(f.fund_id)
    try {
      // fund_id 已存在 -> upsert_fund 走更新分支, 等于对已有基金补新月份.
      // issuer/issuer_domain/asx_code 未持久化, 传不了; confirmed_url 非空时
      // 后端直接走归档解析, 不需要它们兜底联网搜索.
      await api.startIngest({
        fund_id: f.fund_id,
        fund_name: f.fund_name,
        confirmed_url: f.confirmed_url || null,
        apir_code: f.apir_code,
        max_pdf_pages: f.max_pdf_pages,
      })
    } catch (e: unknown) {
      // eslint-disable-next-line no-alert
      alert((e as Error).message)
    }
    setUpdatingFundId(null)
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

  const openReturns = async (f: Fund) => {
    setDataFund(f)
    setReturnsLoading(true)
    try {
      const rows = await api.getReturns(f.fund_id)
      setReturns(rows)
    } catch (e: unknown) {
      // eslint-disable-next-line no-alert
      alert((e as Error).message)
    }
    setReturnsLoading(false)
  }

  const openRbaHistory = async () => {
    setShowRbaHistory(true)
    setRbaHistoryLoading(true)
    try {
      const periods = await api.getRbaHistory()
      setRbaHistory(periods)
    } catch (e: unknown) {
      // eslint-disable-next-line no-alert
      alert((e as Error).message)
    }
    setRbaHistoryLoading(false)
  }

  const formatMonthRange = (start: string, end: string): string => {
    const [sy, sm] = start.split('-')
    if (start === end) return `${sy}年${Number(sm)}月`
    const [ey, em] = end.split('-')
    return sy === ey
      ? `${sy}年${Number(sm)}-${Number(em)}月`
      : `${sy}年${Number(sm)}月 ~ ${ey}年${Number(em)}月`
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
      // 后端 start_ingest 已同步 upsert 一次, 这里立刻刷新让新行马上出现在表格里
      // (不必等下一轮表格轮询检测到 job 转终态才 fetchFunds)
      await fetchFunds()
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
        <div className="flex gap-2">
          <button
            className="text-sm border border-gray-200 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-50"
            onClick={openRbaHistory}
          >
            查看 RBA 利率历史
          </button>
          <button
            className="text-sm bg-[#1a1a2e] text-white px-4 py-2 rounded-lg hover:bg-[#2a2a4e]"
            onClick={() => setShowAdd(true)}
          >
            + 添加基金
          </button>
        </div>
      </div>

      {fundsLoading && <div className="text-gray-400">加载中...</div>}

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
              <tr key={f.fund_id} className={`border-b border-gray-50 ${f.gap_count > 0 ? 'bg-red-50' : 'hover:bg-gray-50'}`}>
                <td className="py-3 px-4 text-gray-500 text-xs">{f.fund_id}</td>
                <td className="py-3 px-4 font-medium">{f.fund_name}</td>
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
                    disabled={!!activeJobs[f.fund_id] || updatingFundId === f.fund_id}
                    onClick={() => handleUpdateData(f)}
                  >
                    {updatingFundId === f.fund_id ? '起任务中…' : '更新数据'}
                  </button>
                  <button
                    className="text-xs text-blue-600 border border-blue-200 rounded px-2.5 py-1 mr-2 hover:bg-blue-50 disabled:opacity-50"
                    disabled={recomputing === f.fund_id}
                    title={f.gap_count > 0 ? '该基金有数据缺口，重算将失败' : undefined}
                    onClick={() => handleRecompute(f.fund_id)}
                  >
                    {recomputing === f.fund_id ? '计算中...' : '重算'}
                  </button>
                  <button
                    className="text-xs text-blue-600 border border-blue-200 rounded px-2.5 py-1 mr-2 hover:bg-blue-50"
                    onClick={() => openReturns(f)}
                  >
                    查看数据
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
                <td colSpan={9} className="py-10 text-center text-gray-400">
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

      {/* 查看数据面板 (月利率原始序列, 不做任何计算) */}
      {dataFund && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-md max-h-[85vh] overflow-y-auto">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-base font-medium">
                {dataFund.fund_name} 月度净收益 ({returns.length} 条)
              </h2>
              <button className="text-gray-400 text-xl" onClick={() => setDataFund(null)}>
                &times;
              </button>
            </div>
            {returnsLoading && <div className="text-gray-400 text-sm">加载中…</div>}
            {!returnsLoading && returns.length === 0 && (
              <div className="text-gray-400 text-sm py-6 text-center">暂无数据</div>
            )}
            {!returnsLoading && returns.length > 0 && (
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
              <button className="text-gray-400 text-xl" onClick={() => setShowRbaHistory(false)}>
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
    </div>
  )
}

function IngestProgress({ job, onClose }: { job: IngestJob; onClose: () => void }) {
  const badge = {
    queued: 'bg-gray-100 text-gray-600',
    ingesting_l1_fundmonitors: 'bg-blue-100 text-blue-700',
    discovering_l2_pdf: 'bg-blue-100 text-blue-700',
    ingesting_l2_pdf: 'bg-blue-100 text-blue-700',
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
      <button
        className="w-full text-sm bg-[#1a1a2e] text-white py-2 rounded-lg hover:bg-[#2a2a4e]"
        onClick={onClose}
      >
        关闭 (摄取在后台继续跑, 不受影响)
      </button>
    </div>
  )
}
