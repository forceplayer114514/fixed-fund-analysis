interface Props {
  label: string
  value: string | number
  rank?: number
}

export default function MetricCard({ label, value, rank }: Props) {
  return (
    <div className="flex-1 min-w-[140px] bg-white rounded-lg p-4 shadow-sm">
      <div className="text-xs text-gray-400 mb-1">{label}</div>
      <div className="text-lg font-semibold">
        {value ?? '—'}
        {rank != null && <span className="text-xs text-gray-400 font-normal ml-1">({rank})</span>}
      </div>
    </div>
  )
}
