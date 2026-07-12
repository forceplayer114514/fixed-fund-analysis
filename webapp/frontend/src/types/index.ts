/** 基金元信息（来自 GET /api/funds） */
export interface Fund {
  fund_id: string
  fund_name: string
  apir_code: string | null
  confirmed_url: string
  fetch_method: string
  url_type: string
  max_pdf_pages: number | null
  data_cutoff_month: string | null
  has_metrics: boolean
}

/** 5 维指标（来自 compare 端点或 recompute 返回） */
export interface FundMetrics {
  fund_id: string
  fund_name: string | null
  date_period: string
  history_months: number
  is_short_history_warning: boolean
  unsmoothing_coefficient_phi: number
  is_geltner_applied: boolean
  orig_annualized_excess_return: number
  un_annualized_excess_return: number | null
  orig_max_drawdown: number
  un_max_drawdown: number
  orig_omega_ratio: number | null
  un_omega_ratio: number | null
  orig_excess_win_rate: number
  un_excess_win_rate: number
  orig_max_underperform_months: number
  un_max_underperform_months: number
  orig_annualized_volatility: number
  un_annualized_volatility: number
  ljung_box_q: number
  is_q_significant: boolean
}

/** compare 端点返回 */
export interface CompareResponse {
  period: string
  funds: FundMetrics[]
}

/** 时序数据（来自 time-series 端点） */
export interface FundSeries {
  fund_id: string
  fund_name: string
  dates: string[]
  orig_nav: number[]
  unsm_nav: number[] | null
  is_geltner_applied: boolean
}

export interface TimeSeriesResponse {
  period: string
  months: string[]
  series: FundSeries[]
}

/** 异常记录 */
export interface Anomaly {
  id: number
  fund_id: string
  date: string
  value: number
  z_score: number
  threshold_sigma: number
  mean: number
  stdev: number
  fund_name: string | null
  /** 对应 monthly_returns 行主键，人工纠错 PATCH 用（非 anomalies.id） */
  monthly_return_id: number | null
}

/** 纠错请求体 */
export interface MonthlyReturnPatch {
  net_return: number
  commentary_truth?: number | null
}

/** 添加基金请求体 */
export interface FundCreatePayload {
  fund_id: string
  fund_name: string
  apir_code?: string | null
  confirmed_url: string
  fetch_method: string
  url_type: string
  max_pdf_pages?: number | null
}
