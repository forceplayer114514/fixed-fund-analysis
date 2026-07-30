import { useEffect, useState } from 'react'
import { useStore } from '../store/useStore'
import { api } from '../api/client'
import type { Fund, IngestJob, MonthlyReturnRow, PendingReview } from '../types'
import FundTable from './funds/FundTable'
import AddFundPanel, { type AddFormState, type AddFormError } from './funds/AddFundPanel'
import IngestJobPanel from './funds/IngestJobPanel'
import PendingReviewPanel from './funds/PendingReviewPanel'
import FundDataDrawer, { type RbaHistoryRow } from './funds/FundDataDrawer'
import { useT } from '../i18n/useT'

const POLL_MS = 1500

export default function FundManagement() {
  const t = useT()
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
  const [addForm, setAddForm] = useState<AddFormState>({
    fund_id: '',
    fund_name: '',
    apir_code: '',
    confirmed_url: '',
    issuer: '',
    issuer_domain: '',
    asx_code: '',
    // Spec G: 搜索引擎选择, 每次摄取生效, 不记在基金上
    search_engine: 'tavily',
  })
  const [addError, setAddError] = useState<AddFormError | null>(null)
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
  const [rbaHistory, setRbaHistory] = useState<RbaHistoryRow[]>([])
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

  const handleToggleHidden = async (f: Fund) => {
    try {
      await api.setFundHidden(f.fund_id, !f.is_hidden)
      await fetchFunds()
    } catch (e: unknown) {
      // eslint-disable-next-line no-alert
      alert((e as Error).message)
    }
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

  const handleApprove = async (id: number) => {
    const resp = await api.approvePending(id)
    // 权威源已覆盖 (新 action=skipped_authoritative_covered; 兼容旧 action=skipped_l3_covered)
    if (resp.action === 'skipped_authoritative_covered' || resp.action === 'skipped_l3_covered') {
      const tag = resp.existing_tag
      const tagLabel =
        tag === 'fundmonitors_table' ? t('funds.sourceL3')
        : tag === 'llm' ? t('funds.sourceLlmPdf')
        : (tag ?? t('funds.sourceAuthoritative'))
      // eslint-disable-next-line no-alert
      alert(t('funds.coveredByAuthoritative', { source: tagLabel }))
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
    setAddError(null)
    if (!addForm.fund_name.trim()) {
      setAddError({ message: t('funds.nameRequired'), backend: false })
      return
    }
    if (addForm.apir_code && !/^[A-Z]{3}\d{4}AU$/.test(addForm.apir_code)) {
      setAddError({ message: t('funds.apirInvalid'), backend: false })
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
        search_engine: addForm.search_engine,
      })
      setJob(j)
      // 后端 start_ingest 已同步 upsert 一次, 这里立刻刷新让新行马上出现在表格里
      // (不必等下一轮表格轮询检测到 job 转终态才 fetchFunds)
      await fetchFunds()
    } catch (e: unknown) {
      setAddError({ message: (e as Error).message, backend: true })
    }
    setSubmitting(false)
  }

  const closeModal = () => {
    setShowAdd(false)
    setJob(null)
    setShowAdvanced(false)
    setAddError(null)
    setAddForm({
      fund_id: '', fund_name: '', apir_code: '',
      confirmed_url: '', issuer: '', issuer_domain: '', asx_code: '',
      search_engine: 'tavily',
    })
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-5">
        <h1 className="text-xl font-semibold text-fg">{t('funds.title')}</h1>
        <div className="flex gap-2">
          <button
            className="text-sm border border-border-strong text-fg px-4 py-2 rounded-lg hover:bg-sunken"
            onClick={openRbaHistory}
          >
            {t('funds.viewRbaHistory')}
          </button>
          <button
            className="text-sm bg-accent text-accent-fg px-4 py-2 rounded-lg hover:opacity-90"
            onClick={() => setShowAdd(true)}
          >
            {t('funds.addButton')}
          </button>
        </div>
      </div>

      {fundsLoading && <div className="text-fg-subtle">{t('common.loading')}</div>}

      <FundTable
        funds={funds}
        fundsLoading={fundsLoading}
        activeJobs={activeJobs}
        recomputing={recomputing}
        updatingFundId={updatingFundId}
        deleteConfirm={deleteConfirm}
        onRecompute={handleRecompute}
        onUpdate={handleUpdateData}
        onToggleHidden={handleToggleHidden}
        onRequestDelete={setDeleteConfirm}
        onConfirmDelete={handleDelete}
        onCancelDelete={() => setDeleteConfirm(null)}
        onOpenReview={openReview}
        onOpenData={openReturns}
      />

      {/* 添加基金弹窗 (LLM 摄取版) */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="card p-6 w-full max-w-lg max-h-[85vh] overflow-y-auto">
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-base font-medium text-fg">{t('funds.addPanelTitle')}</h2>
              <button className="text-fg-subtle text-xl" onClick={closeModal}>
                &times;
              </button>
            </div>

            {job ? (
              <IngestJobPanel job={job} onClose={closeModal} />
            ) : (
              <AddFundPanel
                open={showAdd}
                form={addForm}
                setForm={setAddForm}
                showAdvanced={showAdvanced}
                setShowAdvanced={setShowAdvanced}
                error={addError}
                submitting={submitting}
                onSubmit={handleAdd}
              />
            )}
          </div>
        </div>
      )}

      {/* 审核抽屉 */}
      {reviewFund && (
        <PendingReviewPanel
          fundId={reviewFund}
          items={pending}
          loading={pendingLoading}
          onApprove={handleApprove}
          onReject={handleReject}
          onClose={() => setReviewFund(null)}
        />
      )}

      <FundDataDrawer
        fund={dataFund}
        returns={returns}
        loading={returnsLoading}
        rbaHistory={rbaHistory}
        rbaHistoryLoading={rbaHistoryLoading}
        showRbaHistory={showRbaHistory}
        onToggleRbaHistory={() => setShowRbaHistory(false)}
        onClose={() => setDataFund(null)}
      />
    </div>
  )
}
