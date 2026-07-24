import { useStore } from '../store/useStore'

export default function FundChips() {
  const funds = useStore(s => s.funds)
  const selected = useStore(s => s.selectedFundIds)
  const toggleFund = useStore(s => s.toggleFund)

  return (
    <div className="flex flex-wrap gap-2 mb-5">
      {funds.filter(f => !f.is_hidden).map(f => {
        const active = selected.includes(f.fund_id)
        return (
          <button
            key={f.fund_id}
            onClick={() => toggleFund(f.fund_id)}
            className={`px-4 py-1.5 rounded-full text-sm border-2 transition-colors ${
              active
                ? 'border-cyan-400 bg-cyan-50 text-cyan-800 font-medium'
                : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
            }`}
          >
            {f.fund_name}
          </button>
        )
      })}
    </div>
  )
}
