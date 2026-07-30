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
            className={`px-3.5 py-1.5 rounded-full text-sm border transition-colors ${
              active
                ? 'border-accent bg-accent-soft text-accent font-medium'
                : 'border-border bg-surface text-fg-muted hover:border-border-strong hover:text-fg'
            }`}
          >
            {f.fund_name}
          </button>
        )
      })}
    </div>
  )
}
