import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator

class FundEntrySchema(BaseModel):
    fund_name: str = Field(..., description="The name of the fund.")

    apir_code: str = Field(..., description="APIR code for the fund.")

    confirmed_url: str = Field(..., description="The validated data source URL.")

    fetch_method: str = Field(..., description="Method used to fetch the data (e.g., 'requests+BeautifulSoup').")

    url_type: str = Field(..., description="Type of the URL document (e.g., 'compliance_docs_monthly_reports').")

    # 历史保留字段（选填）
    target_leverage: Optional[float] = Field(
        default=None,
        description="自 commit 7ffbe48 起，杠杆和信用利差的计算逻辑已从 metrics.py 移除（原因：杠杆率数据无法可靠解析），这两个字段目前仅作历史保留，不参与任何计算。"
    )

    borrowing_spread: Optional[float] = Field(
        default=None,
        description="自 commit 7ffbe48 起，杠杆和信用利差的计算逻辑已从 metrics.py 移除（原因：杠杆率数据无法可靠解析），这两个字段目前仅作历史保留，不参与任何计算。"
    )

    # 其他可能存在的选填字段
    verification_signal: Optional[str] = Field(None, description="Verification signal details if available.")
    verified_at: Optional[str] = Field(None, description="Date when the registry URL was verified (YYYY-MM-DD).")
    max_pdf_pages: Optional[int] = Field(None, description="Maximum number of PDF pages to parse for performance optimization.")

    @field_validator('apir_code')
    @classmethod
    def validate_apir_code(cls, v: str) -> str:
        # 取消APIR格式强校验，以适配部分无标准APIR代码的直销基金
        return v

    @field_validator('verified_at')
    @classmethod
    def validate_verified_at(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', v):
                raise ValueError(f"Invalid verified_at format: {v}. Must be YYYY-MM-DD.")
        return v
