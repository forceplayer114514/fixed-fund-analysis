import { useEffect } from 'react'
import { useStore } from '../store/useStore'
import AnomalyTable from '../components/AnomalyTable'
import SmoothingCards from '../components/SmoothingCards'

export default function AnomaliesPage() {
  const fetchFunds = useStore(s => s.fetchFunds)
  const fetchAnomalies = useStore(s => s.fetchAnomalies)
  const fetchCompare = useStore(s => s.fetchCompare)
  const funds = useStore(s => s.funds)
  const anomaliesLoading = useStore(s => s.anomaliesLoading)

  useEffect(() => {
    fetchFunds()
    fetchAnomalies()
  }, [])

  useEffect(() => {
    if (funds.length > 0) {
      fetchCompare()
    }
  }, [funds.length])

  return (
    <div>
      <h1 className="text-xl font-semibold mb-5">异常审计</h1>
      {anomaliesLoading && (
        <div className="text-gray-400 text-sm mb-4">加载异常数据...</div>
      )}
      <AnomalyTable />
      <SmoothingCards />
    </div>
  )
}
