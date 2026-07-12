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

    model_config = {"from_attributes": True}


class MonthlyReturnPatch(BaseModel):
    """人工纠错：修改某月净值（用户主动，非自动纠错）。"""
    net_return: float
    commentary_truth: Optional[float] = None


class AnomalyResponse(BaseModel):
    id: int
    fund_id: str
    date: str
    value: float
    z_score: float
    threshold_sigma: float
    mean: float
    stdev: float
    fund_name: Optional[str] = None
    # 该异常点对应的 monthly_returns 行主键，供前端人工纠错 PATCH 定位。
    # 不能用 anomalies.id（独立自增序列），否则会改写无关基金的月度数据。
    monthly_return_id: Optional[int] = None

    model_config = {"from_attributes": True}


def sanitize_for_json(obj):
    """递归把 inf/NaN float 转为 None（JSON 标准不支持无穷大/NaN）。

    Omega 比率在无跑输月时为 inf，需转 None 供前端显示为"极佳/无跑输"。
    compare/time-series/recompute 等返回 metrics 的端点统一使用。
    """
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, float) and (math.isinf(obj) or math.isnan(obj)):
        return None
    return obj
