import WarnBadge from './WarnBadge'

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
  return (
    <div className="flex-1 min-w-[140px] bg-white rounded-lg p-4 shadow-sm">
      <div className="text-xs text-gray-400 mb-1">
        {label}
        {warn && warnNote && <WarnBadge note={warnNote} />}
      </div>
      <div className="text-lg font-semibold">
        {value ?? '-'}
        {rank != null && (
          <span
            className={`text-xs font-normal ml-1 ${warn ? 'text-gray-300' : 'text-gray-400'}`}
          >
            ({rank})
          </span>
        )}
      </div>
      {subtext && <div className="text-xs text-gray-400 mt-1">{subtext}</div>}
    </div>
  )
}
