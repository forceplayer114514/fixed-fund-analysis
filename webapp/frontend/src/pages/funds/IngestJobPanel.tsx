import type { IngestJob } from '../../types'
import { useT } from '../../i18n/useT'

interface IngestJobPanelProps {
  job: IngestJob
  onClose: () => void
}

export default function IngestJobPanel({ job, onClose }: IngestJobPanelProps) {
  const t = useT()
  const badge = {
    queued: 'bg-sunken text-fg-muted',
    ingesting_l1_fundmonitors: 'bg-accent-soft text-accent',
    discovering_l2_pdf: 'bg-accent-soft text-accent',
    ingesting_l2_pdf: 'bg-accent-soft text-accent',
    succeeded: 'bg-pos-soft text-pos',
    failed: 'bg-neg-soft text-neg',
  }[job.state]

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className={`text-xs px-2 py-0.5 rounded ${badge}`}>{job.state}</span>
        <span className="text-xs text-fg-muted">job_id: {job.job_id}</span>
      </div>
      {job.stats && (
        <div className="text-xs bg-sunken border border-border rounded p-2 text-fg">
          <div>
            monthly: <b>{job.stats.monthly ?? 0}</b>
            {t('funds.jobPending')}<b>{job.stats.pending ?? 0}</b>
            {t('funds.jobGap')}<b>{job.stats.gap ?? 0}</b>
            {t('funds.jobDownloadFail')}<b>{job.stats.download_fail ?? 0}</b>
          </div>
        </div>
      )}
      {job.error && (
        <div className="text-xs text-neg bg-neg-soft border border-neg-border rounded p-2">
          <span className="text-fg-muted">{t('common.backendMessage')}</span>
          <span>{job.error}</span>
        </div>
      )}
      <div>
        <div className="text-xs text-fg-muted mb-1">{t('funds.progressLog', { n: job.log_tail?.length ?? 0 })}</div>
        <div className="text-xs bg-sunken border border-border text-fg font-mono rounded p-2 max-h-64 overflow-auto">
          <div className="text-fg-muted text-[11px] mb-1">{t('common.backendMessage')}</div>
          {(job.log_tail ?? []).map((l, i) => (
            <div key={i}>{l}</div>
          ))}
        </div>
      </div>
      <button
        className="w-full text-sm bg-accent text-accent-fg py-2 rounded-lg hover:opacity-90"
        onClick={onClose}
      >
        {t('funds.closeJobPanel')}
      </button>
    </div>
  )
}
