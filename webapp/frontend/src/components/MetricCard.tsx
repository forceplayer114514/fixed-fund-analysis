import WarnBadge from './WarnBadge'
import { useT } from '../i18n/useT'

interface Props {
  label: string
  value: string | number
  rank?: number
  /** 副文本：最大回撤卡片显示恢复月数 */
  subtext?: string
  /** 小样本警告：名次置灰 + 角标 tooltip（PDD 1.5） */
  warn?: boolean
  warnNote?: string
}

export default function MetricCard({ label, value, rank, subtext, warn, warnNote }: Props) {
  const t = useT()
  return (
    <div className="card flex-1 min-w-[150px] p-4">
      <div className="text-xs text-fg-muted mb-1.5">
        {label}
        {warn && warnNote && <WarnBadge note={warnNote} />}
      </div>
      <div className="flex items-baseline gap-2">
        <div className="text-xl font-semibold font-mono tabular-nums text-fg">{value ?? '-'}</div>
        {rank != null && (
          <span
            title={t('metric.rankTitle')}
            className={`text-[11px] font-medium rounded px-1.5 py-0.5 ${
              warn ? 'bg-sunken text-fg-subtle' : 'bg-accent-soft text-accent'
            }`}
          >
            #{rank}
          </span>
        )}
      </div>
      {subtext && <div className="text-xs text-fg-subtle mt-1.5">{subtext}</div>}
    </div>
  )
}
