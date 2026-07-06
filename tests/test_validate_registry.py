import pytest
import sys
import os

# 将 scripts 目录添加到 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))

from pydantic import ValidationError
from registry_schema import FundEntrySchema
from validate_registry import validate_registry

@pytest.mark.unit
def test_valid_entry():
    # 测试有效条目
    valid_data = {
        "fund_name": "Test Fund",
        "apir_code": "ETL5010AU",  # 3位字母，真实的合法代码
        "confirmed_url": "https://example.com",
        "fetch_method": "requests+BeautifulSoup",
        "url_type": "compliance_docs_monthly_reports"
    }
    # 应该不会引发 ValidationError
    entry = FundEntrySchema(**valid_data)
    assert entry.apir_code == "ETL5010AU"

@pytest.mark.unit
def test_invalid_apir_format():
    # 测试非法的 APIR 格式
    invalid_data_1 = {
        "fund_name": "Invalid Fund 1",
        "apir_code": "ABCDE1234AU",  # 5位字母，APIR代码应为3-4位字母开头
        "confirmed_url": "https://example.com",
        "fetch_method": "requests+BeautifulSoup",
        "url_type": "compliance_docs_monthly_reports"
    }

    invalid_data_2 = {
        "fund_name": "Invalid Fund 2",
        "apir_code": "ET5010AU",  # 只有2位字母
        "confirmed_url": "https://example.com",
        "fetch_method": "requests+BeautifulSoup",
        "url_type": "compliance_docs_monthly_reports"
    }

    with pytest.raises(ValidationError) as excinfo:
        FundEntrySchema(**invalid_data_1)
    assert "Invalid APIR code format" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        FundEntrySchema(**invalid_data_2)
    assert "Invalid APIR code format" in str(excinfo.value)

@pytest.mark.unit
def test_duplicate_apir_code():
    # 测试查重逻辑 (通过 validate_registry 函数)
    registry_data = {
        "fund_a": {
            "fund_name": "Fund A",
            "apir_code": "ETL5010AU",
            "confirmed_url": "https://example.com",
            "fetch_method": "requests+BeautifulSoup",
            "url_type": "compliance_docs_monthly_reports"
        },
        "fund_b": {
            "fund_name": "Fund B",
            "apir_code": "ETL5010AU",  # 重复
            "confirmed_url": "https://example.com",
            "fetch_method": "requests+BeautifulSoup",
            "url_type": "compliance_docs_monthly_reports"
        }
    }

    errors = validate_registry(registry_data)
    assert len(errors) == 1
    assert "Duplicate APIR code" in errors[0]
    assert "fund_b/ETL5010AU" in errors[0]

@pytest.mark.unit
def test_invalid_date_format():
    invalid_data = {
        "fund_name": "Date Test Fund",
        "apir_code": "WHT1234AU",
        "confirmed_url": "https://example.com",
        "fetch_method": "requests+BeautifulSoup",
        "url_type": "compliance_docs_monthly_reports",
        "verified_at": "26/07/2026"  # 错误的格式，应为 YYYY-MM-DD
    }

    with pytest.raises(ValidationError) as excinfo:
        FundEntrySchema(**invalid_data)
    assert "Invalid verified_at format" in str(excinfo.value)
