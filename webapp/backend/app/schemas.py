"""Pydantic v2 请求/响应模型。APIR 正则校验在此。"""
from __future__ import annotations

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

    model_config = {"from_attributes": True}
