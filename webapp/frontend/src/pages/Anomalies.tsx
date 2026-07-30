import { useEffect } from 'react'
import { useStore } from '../store/useStore'
import AnomalyTable from '../components/AnomalyTable'
import SmoothingCards from '../components/SmoothingCards'
import { useT } from '../i18n/useT'

export default function AnomaliesPage() {
  const t = useT()
  const fetchFunds = useStore(s => s.fetchFunds)
  const fetchAnomalies = useStore(s => s.fetchAnomalies)
  const fetchCompare = useStore(s => s.fetchCompare)
  const funds = useStore(s => s.funds)
  const selectedFundIds = useStore(s => s.selectedFundIds)
  const anomaliesLoading = useStore(s => s.anomaliesLoading)

  useEffect(() => {
    fetchFunds()
    fetchAnomalies()
  }, [])

  useEffect(() => {
    if (funds.length > 0) {
      fetchCompare()
    }
  }, [funds.length, selectedFundIds])

  return (
    <div>
      <h1 className="text-xl font-semibold mb-5 text-fg">{t('anomaly.title')}</h1>
      {anomaliesLoading && (
        <div className="text-fg-subtle text-sm mb-4">{t('anomaly.loading')}</div>
      )}
      <AnomalyTable />
      <SmoothingCards />
    </div>
  )
}
