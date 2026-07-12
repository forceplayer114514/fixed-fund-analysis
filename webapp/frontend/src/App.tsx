import { Routes, Route } from 'react-router-dom'
import ErrorBoundary from './components/ErrorBoundary'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Anomalies from './pages/Anomalies'
import FundManagement from './pages/FundManagement'

export default function App() {
  return (
    <ErrorBoundary>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/anomalies" element={<Anomalies />} />
          <Route path="/funds" element={<FundManagement />} />
        </Route>
      </Routes>
    </ErrorBoundary>
  )
}
