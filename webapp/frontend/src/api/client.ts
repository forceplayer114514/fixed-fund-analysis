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
}
