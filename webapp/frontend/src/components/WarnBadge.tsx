/**
 * WarnBadge - 小样本警示角标（F4 修复）。
 *
 * 原 HTML title 原生 tooltip 在小热区/短暂 hover 下时有时无；改用 CSS group-hover
 * 即时显隐的气泡，热区固定 16×16px，确保任何 ⚠ 100% 可见说明。
 */
interface Props {
  note: string
}

export default function WarnBadge({ note }: Props) {
  return (
    <span className="relative inline-flex items-center justify-center w-4 h-4 ml-1 cursor-help group align-middle">
      <span className="text-orange-400 text-xs leading-none">⚠</span>
      <span className="pointer-events-none absolute left-1/2 bottom-full z-50 mb-1 -translate-x-1/2 whitespace-nowrap rounded bg-gray-800 px-2 py-1 text-xs font-normal text-white opacity-0 transition-opacity duration-100 group-hover:opacity-100">
        {note}
      </span>
    </span>
  )
}
