const BASE = ''

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${body}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export const api = {
  listFunds: () => request<import('../types').Fund[]>('/api/funds'),

  createFund: (data: import('../types').FundCreatePayload) =>
    request<import('../types').Fund>('/api/funds', { method: 'POST', body: JSON.stringify(data) }),

  deleteFund: (fundId: string) =>
    request<void>(`/api/funds/${fundId}`, { method: 'DELETE' }),

  recomputeFund: (fundId: string) =>
    request<Record<string, unknown>>(`/api/funds/${fundId}/recompute`, { method: 'POST' }),

  compare: (fundIds: string[], period: string) =>
    request<import('../types').CompareResponse>(
      `/api/metrics/compare?fund_ids=${fundIds.join(',')}&period=${period}`
    ),

  timeSeries: (fundIds: string[], period: string) =>
    request<import('../types').TimeSeriesResponse>(
      `/api/metrics/time-series?fund_ids=${fundIds.join(',')}&period=${period}`
    ),

  listAnomalies: () => request<import('../types').Anomaly[]>('/api/anomalies'),

  patchMonthlyReturn: (id: number, data: import('../types').MonthlyReturnPatch) =>
    request<Record<string, unknown>>(`/api/monthly-returns/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  // ---- LLM 摄取 ----
  startIngest: (data: import('../types').IngestRequest) =>
    request<import('../types').IngestJob>('/api/ingest/funds', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  getIngestJob: (jobId: string) =>
    request<import('../types').IngestJob>(`/api/ingest/jobs/${jobId}`),

  listActiveJobs: () =>
    request<{ job_id: string; fund_id: string; state: string }[]>('/api/ingest/jobs/active'),

  listPending: (fundId?: string) => {
    const q = fundId ? `?fund_id=${encodeURIComponent(fundId)}` : ''
    return request<import('../types').PendingReview[]>(`/api/pending${q}`)
  },

  approvePending: (reviewId: number) =>
    request<import('../types').ApprovePendingResponse>(`/api/pending/${reviewId}/approve`, {
      method: 'PATCH',
    }),

  rejectPending: (reviewId: number, reason?: string) => {
    const q = reason ? `?reason=${encodeURIComponent(reason)}` : ''
    return request<Record<string, unknown>>(`/api/pending/${reviewId}/reject${q}`, {
      method: 'PATCH',
    })
  },
}
