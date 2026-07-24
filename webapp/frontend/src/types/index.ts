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
  /** confirmed_gaps 表该基金行数（数据完整性标记，>0 表示有缺口） */
  gap_count: number
  /** pending_review 表 state='pending' 行数（LLM 摄取两闸未过待人工审核） */
  pending_count: number
  /** Spec B: fundmonitors 页面实际抓到的基金名 (与 fund_name 不同时前端标红) */
  discovered_source_name?: string | null
  /** 隐藏：不出现在对比看板(FundChips/默认选中)，基金管理页仍可见 */
  is_hidden: boolean
}

/** 因数据缺口被排除对比的基金（compare/time-series 降级返回） */
export interface ExcludedFund {
  fund_id: string
  reason: string
}

/** 5 维指标（来自 compare 端点或 recompute 返回） */
export interface FundMetrics {
  fund_id: string
  fund_name: string | null
  date_period: string
  history_months: number
  excess_sample_months: number
  is_short_history_warning: boolean
  unsmoothing_coefficient_phi: number
  is_geltner_applied: boolean
  // 维度1：进攻（年化收益率=基金口径几何年化；年化超额=超额口径复利年化）
  orig_annualized_return: number
  un_annualized_return: number
  orig_annualized_excess_return: number
  un_annualized_excess_return: number | null
  // 维度2：防守（最大回撤 + 恢复月数；recovery_months=null 表示无回撤）
  orig_max_drawdown: number
  un_max_drawdown: number
  orig_recovery_months: number | null
  un_recovery_months: number | null
  orig_dd_recovered: boolean
  un_dd_recovered: boolean
  // 维度3：性价比（夏普比率-超额口径；n<2 或 std=0 时为 null。现金基准下等于旧「信息比率」，2026-07 正名）
  orig_sharpe_ratio: number | null
  un_sharpe_ratio: number | null
  // 维度4：体感与煎熬度
  orig_excess_win_rate: number
  un_excess_win_rate: number
  orig_max_underperform_months: number
  un_max_underperform_months: number
  // 维度5：真实性辅助
  orig_annualized_volatility: number
  un_annualized_volatility: number
  ljung_box_q: number
  is_q_significant: boolean
}

/** compare 端点返回 */
export interface CompareResponse {
  period: string
  funds: FundMetrics[]
  /** 因数据缺口被跳过的基金（robustness 降级，不拖垮整批） */
  excluded?: ExcludedFund[]
}

/** 时序数据（来自 time-series 端点） */
export interface FundSeries {
  fund_id: string
  fund_name: string
  dates: string[]
  /** 逐月原始收益（对齐 dates）；Phase 2 图表 rebase/回撤/滚动超额/热力图由此计算 */
  returns: number[]
  /** 去平滑逐月收益；未触发 Geltner 为 null */
  unsm_returns: number[] | null
  orig_nav: number[]
  unsm_nav: number[] | null
  is_geltner_applied: boolean
}

export interface TimeSeriesResponse {
  period: string
  months: string[]
  /** 全局 RBA 月利率，对齐 months，缺失月为 null（PDD 1.7 scoped） */
  rba: (number | null)[]
  series: FundSeries[]
  /** 因数据缺口被跳过的基金（robustness 降级，不拖垮整批） */
  excluded?: ExcludedFund[]
}

/** 异常记录 */
export interface Anomaly {
  id: number
  fund_id: string
  date: string
  /** 'return_outlier' = MAD 离群点；'rba_missing' = RBA 基准缺失（PDD 1.7） */
  type: string
  reason: string | null
  /** 以下数值字段仅 return_outlier 有值；rba_missing 行为 null */
  value: number | null
  z_score: number | null
  threshold_sigma: number | null
  mean: number | null
  stdev: number | null
  fund_name: string | null
  /** 对应 monthly_returns 行主键，人工纠错 PATCH 用（非 anomalies.id）。rba_missing 为 null */
  monthly_return_id: number | null
}

/** 纠错请求体 */
export interface MonthlyReturnPatch {
  net_return: number
  commentary_truth?: number | null
}

/** GET /api/funds/{id}/returns 单行 (原始逐月序列, "查看数据"面板用) */
export interface MonthlyReturnRow {
  date: string
  net_return: number
  commentary_truth: number | null
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

/** LLM 摄取请求体 (POST /api/ingest/funds). 只有 fund_name 必填; 其余全选填。 */
export interface IngestRequest {
  fund_name: string
  fund_id?: string | null
  apir_code?: string | null
  confirmed_url?: string | null
  issuer?: string | null
  issuer_domain?: string | null
  asx_code?: string | null
  max_pdf_pages?: number | null
  limit?: number | null
}

/** LLM 摄取任务状态 (GET /api/ingest/jobs/{id}) */
export interface IngestJob {
  job_id: string
  fund_id: string
  state: 'queued' | 'ingesting_l1_fundmonitors' | 'discovering_l2_pdf'
    | 'ingesting_l2_pdf' | 'succeeded' | 'failed'
  started_at: string | null
  finished_at: string | null
  stats: { monthly?: number; pending?: number; gap?: number; download_fail?: number } | null
  log_tail: string[] | null
  error: string | null
}

/** PATCH /api/pending/{id}/approve 返回体
 *  - action='approved': pending 已入 monthly_returns, 指标已重算
 *  - action='skipped_authoritative_covered': 该月已由权威源覆盖 (existing_tag 说明是哪种),
 *    pending 未采纳, monthly_returns 不动
 *  - action='skipped_l3_covered': 旧后端语义, 前端兼容 fallback
 */
export interface ApprovePendingResponse {
  ok: boolean
  fund_id: string
  date: string
  action: 'approved' | 'skipped_authoritative_covered' | 'skipped_l3_covered' | string
  /** 仅 skipped_authoritative_covered 有: 'fundmonitors_table' | 'llm' | 其他 */
  existing_tag?: string
  message?: string
}

/** pending_review 记录 (两闸未过, 待人工审核) */
export interface PendingReview {
  id: number
  fund_id: string
  fund_name: string | null
  date: string  // YYYY-MM-DD
  net_return: number
  source_quote: string | null
  extract_method: string
  gate_result: string | null   // 如 "q0r1f1a1"
  review_state: string
  review_reason: string | null
  candidates_json: string | null  // JSON string, 前端 parse 显示原始上下文
  created_at: string | null
}
