"""Pydantic v2 请求/响应模型。APIR 正则校验在此。"""
from __future__ import annotations

import math
import re
from typing import Optional

from pydantic import BaseModel, field_validator


APIR_PATTERN = re.compile(r"^[A-Z]{3}\d{4}AU$")


class FundCreate(BaseModel):
    fund_id: str
    fund_name: str
    apir_code: Optional[str] = None
    confirmed_url: str
    fetch_method: str
    url_type: str
    max_pdf_pages: Optional[int] = None

    @field_validator("apir_code")
    @classmethod
    def validate_apir(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if not APIR_PATTERN.match(v):
            raise ValueError(f"APIR 格式应为 3字母+4数字+AU（如 ETL5010AU），得到: {v}")
        return v


class FundResponse(BaseModel):
    fund_id: str
    fund_name: str
    apir_code: Optional[str] = None
    confirmed_url: str
    fetch_method: str
    url_type: str
    max_pdf_pages: Optional[int] = None
    data_cutoff_month: Optional[str] = None  # 来自 fund_metrics.date_period 或最新 monthly_return
    has_metrics: bool = False
    gap_count: int = 0  # confirmed_gaps 表该基金行数（数据完整性标记）
    pending_count: int = 0  # pending_review 表 state='pending' 行数（LLM 摄取两闸未过待人工审核）
    # Spec B: fundmonitors 页面实际抓到的基金名 (透明展示; 与 fund_name 不同时前端标红)
    discovered_source_name: Optional[str] = None

    model_config = {"from_attributes": True}


class PendingReviewResponse(BaseModel):
    """/api/pending 返回；两闸未过的候选值供人工审核。"""
    id: int
    fund_id: str
    fund_name: Optional[str] = None
    date: str  # YYYY-MM-DD
    net_return: float
    source_quote: Optional[str] = None
    extract_method: str
    gate_result: Optional[str] = None
    review_state: str
    review_reason: Optional[str] = None
    candidates_json: Optional[str] = None  # 原始响应 JSON
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}


class IngestRequest(BaseModel):
    """POST /api/ingest/funds 请求体。只有 fund_name 必填；

    其余全选填 -- fund_id 空则由 fund_name slugify；issuer 空则用 fund_name
    当搜索词交 Gemini 联网自搜。为最小心智负担设计。
    """
    fund_name: str
    fund_id: Optional[str] = None
    apir_code: Optional[str] = None
    confirmed_url: Optional[str] = None  # 选填；留空由 Gemini 联网自搜归档
    issuer: Optional[str] = None
    issuer_domain: Optional[str] = None
    asx_code: Optional[str] = None
    max_pdf_pages: Optional[int] = None
    limit: Optional[int] = None  # 仅处理前 N 个链接 (调试)
    # Spec D: confirmed_url 是单文件多月 HTML/CSV 时, 需给 inception_month
    # (YYYY-MM), 会从 inception 到当月枚举 ym, 对同一 URL 逐月调 LLM 提取.
    inception_month: Optional[str] = None

    @field_validator("apir_code")
    @classmethod
    def validate_apir(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if not APIR_PATTERN.match(v):
            raise ValueError(f"APIR 格式应为 3字母+4数字+AU（如 ETL5010AU），得到: {v}")
        return v


class IngestJobResponse(BaseModel):
    """/api/ingest/jobs/{id} 返回。job 状态存 backend 内存字典。"""
    job_id: str
    fund_id: str
    state: str  # 'queued' | 'discovering' | 'ingesting' | 'succeeded' | 'failed'
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    stats: Optional[dict] = None  # {'monthly': N, 'pending': N, 'gap': N, 'download_fail': N}
    log_tail: Optional[list] = None  # 最后 N 条进度信息
    error: Optional[str] = None


class MonthlyReturnPatch(BaseModel):
    """人工纠错：修改某月净值（用户主动，非自动纠错）。"""
    net_return: float
    commentary_truth: Optional[float] = None


class AnomalyResponse(BaseModel):
    id: int
    fund_id: str
    date: str
    # 类型：return_outlier=MAD 离群点；rba_missing=RBA 基准缺失（PDD 1.7）
    type: str = "return_outlier"
    reason: Optional[str] = None
    # 以下数值字段仅 return_outlier 有值；rba_missing 行为 None
    value: Optional[float] = None
    z_score: Optional[float] = None
    threshold_sigma: Optional[float] = None
    mean: Optional[float] = None
    stdev: Optional[float] = None
    fund_name: Optional[str] = None
    # 该异常点对应的 monthly_returns 行主键，供前端人工纠错 PATCH 定位。
    # 不能用 anomalies.id（独立自增序列），否则会改写无关基金的月度数据。
    # rba_missing 行不提供（基金该月收益本身存在，纠错无意义，应修 RBA）。
    monthly_return_id: Optional[int] = None

    model_config = {"from_attributes": True}


def sanitize_for_json(obj):
    """递归把 inf/NaN float 转为 None（JSON 标准不支持无穷大/NaN）。

    信息比率在 std=0 或 n<2 时已由 calculate_information_ratio 返回 None（Omega 已
    移除，不再有 inf 场景）。compare/time-series/recompute 等返回 metrics 的端点统一
    使用，作为通用防御保留。
    """
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, float) and (math.isinf(obj) or math.isnan(obj)):
        return None
    return obj
