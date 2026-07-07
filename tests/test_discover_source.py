import pytest
import requests
from unittest.mock import patch, MagicMock

# 模拟快速探测函数
def quick_verify_url(url: str) -> bool:
    try:
        resp = requests.head(url, timeout=5, headers={"User-Agent": "Mozilla"})
        return resp.status_code == 200
    except Exception:
        return False

@pytest.mark.unit
def test_quick_verify_url_success() -> None:
    with patch('requests.head') as mock_head:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_head.return_value = mock_resp
        assert quick_verify_url("https://example.com") is True

@pytest.mark.unit
def test_quick_verify_url_fail() -> None:
    with patch('requests.head', side_effect=requests.RequestException):
        assert quick_verify_url("https://example.com") is False
