import pytest
import requests
import asyncio
from unittest.mock import patch, MagicMock
from scripts.discover_source import parse_sitemap, verify_candidates_async

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

@pytest.mark.unit
def test_parse_sitemap_depth_limit() -> None:
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://example.com/performance</loc>
      </url>
      <url>
        <loc>https://example.com/a/b/c/d/performance</loc>
      </url>
      <url>
        <loc>https://example.com/a/b/c/d/e/performance</loc>
      </url>
      <url>
        <loc>https://example.com/not-matching</loc>
      </url>
    </urlset>
    """
    with patch('requests.get') as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = sitemap_xml.encode('utf-8')
        mock_get.return_value = mock_resp

        result = parse_sitemap("https://example.com", ["performance"])
        assert "https://example.com/performance" in result
        assert "https://example.com/a/b/c/d/performance" in result
        assert "https://example.com/a/b/c/d/e/performance" not in result
        assert len(result) == 2

@pytest.mark.unit
def test_verify_candidates_async() -> None:
    async def mock_verify_url_async(session, url, fund_id, fund_name, apir_code):
        if url == "https://example.com/success":
            return True, url
        return False, url

    with patch('scripts.discover_source.verify_url_async', side_effect=mock_verify_url_async):
        candidates = [
            "https://example.com/fail1",
            "https://example.com/success",
            "https://example.com/fail2"
        ]
        result = asyncio.run(verify_candidates_async(candidates, "test_fund", "Test Fund", "TST1234AU"))
        assert result == "https://example.com/success"

