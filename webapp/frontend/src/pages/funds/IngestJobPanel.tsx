import type { IngestJob } from '../../types'

interface IngestJobPanelProps {
  job: IngestJob
  onClose: () => void
}

export default function IngestJobPanel({ job, onClose }: IngestJobPanelProps) {
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
