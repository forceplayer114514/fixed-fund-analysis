"""llm_ingest/tavily.py 搜索后端切换 (Tavily/SearXNG) 测试。

2026-07-19 `tavily替代方案-最终报告.md` 实测后确定: SearXNG 换血做主搜索,
Tavily 降级为 SEARCH_BACKEND 环境变量手动切的应急回退, 不做自动降级。
"""
from unittest.mock import MagicMock

import pytest

from llm_ingest import tavily as tavily_mod
from llm_ingest.tavily import (
    TavilyError,
    TavilyResult,
    _host_blocked,
    _searxng_impl,
    _tavily_impl,
    tavily_search,
)


def _mock_resp(status_code: int = 200, json_data=None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data or {}
    r.text = text
    return r


class TestHostBlocked:
    def test_exact_match_blocked(self):
        assert _host_blocked("https://morningstar.com/x", ["morningstar.com"])

    def test_subdomain_blocked(self):
        assert _host_blocked("https://www.morningstar.com/x", ["morningstar.com"])

    def test_lookalike_domain_not_blocked(self):
        """notmorningstar.com.evil.com 不该被 morningstar.com 误伤."""
        assert not _host_blocked("https://notmorningstar.com.evil.com/x", ["morningstar.com"])

    def test_unrelated_domain_not_blocked(self):
        assert not _host_blocked("https://hellostake.com/x", ["morningstar.com"])


class TestSearxngImpl:
    def test_parses_results(self, monkeypatch):
        resp = _mock_resp(json_data={"results": [
            {"url": "https://hellostake.com/au", "title": "Stake", "content": "snippet"},
            {"url": "https://reddit.com/r/x", "title": "Reddit", "content": "noise"},
        ]})
        monkeypatch.setattr(tavily_mod.requests, "get", MagicMock(return_value=resp))
        out = _searxng_impl("Stake Accumulate Fund", max_results=8)
        assert out == [
            TavilyResult(url="https://hellostake.com/au", title="Stake", content="snippet"),
            TavilyResult(url="https://reddit.com/r/x", title="Reddit", content="noise"),
        ]

    def test_truncates_to_max_results(self, monkeypatch):
        resp = _mock_resp(json_data={"results": [
            {"url": f"https://site{i}.com"} for i in range(10)
        ]})
        monkeypatch.setattr(tavily_mod.requests, "get", MagicMock(return_value=resp))
        out = _searxng_impl("q", max_results=3)
        assert len(out) == 3

    def test_skips_results_without_url(self, monkeypatch):
        resp = _mock_resp(json_data={"results": [{"title": "no url"}, {"url": "https://x.com"}]})
        monkeypatch.setattr(tavily_mod.requests, "get", MagicMock(return_value=resp))
        out = _searxng_impl("q")
        assert [r.url for r in out] == ["https://x.com"]

    def test_non_200_raises_tavily_error(self, monkeypatch):
        resp = _mock_resp(status_code=403, text="Forbidden")
        monkeypatch.setattr(tavily_mod.requests, "get", MagicMock(return_value=resp))
        with pytest.raises(TavilyError):
            _searxng_impl("q")

    def test_network_error_raises_tavily_error(self, monkeypatch):
        import requests as real_requests
        monkeypatch.setattr(
            tavily_mod.requests, "get",
            MagicMock(side_effect=real_requests.RequestException("conn refused")),
        )
        with pytest.raises(TavilyError):
            _searxng_impl("q")

    def test_uses_env_configured_url_and_engines(self, monkeypatch):
        monkeypatch.setenv("SEARXNG_URL", "http://searxng-host:9000")
        monkeypatch.setenv("SEARXNG_ENGINES", "bing")
        mock_get = MagicMock(return_value=_mock_resp(json_data={"results": []}))
        monkeypatch.setattr(tavily_mod.requests, "get", mock_get)
        _searxng_impl("q")
        called_url = mock_get.call_args[0][0]
        called_params = mock_get.call_args[1]["params"]
        assert called_url == "http://searxng-host:9000/search"
        assert called_params["engines"] == "bing"


class TestTavilySearchDispatch:
    def test_default_backend_is_searxng(self, monkeypatch):
        monkeypatch.delenv("SEARCH_BACKEND", raising=False)
        mock_searxng = MagicMock(return_value=[])
        mock_tavily = MagicMock(return_value=[])
        monkeypatch.setattr(tavily_mod, "_searxng_impl", mock_searxng)
        monkeypatch.setattr(tavily_mod, "_tavily_impl", mock_tavily)
        tavily_search("q")
        mock_searxng.assert_called_once()
        mock_tavily.assert_not_called()

    def test_backend_env_var_switches_to_tavily(self, monkeypatch):
        monkeypatch.setenv("SEARCH_BACKEND", "tavily")
        mock_searxng = MagicMock(return_value=[])
        mock_tavily = MagicMock(return_value=[])
        monkeypatch.setattr(tavily_mod, "_searxng_impl", mock_searxng)
        monkeypatch.setattr(tavily_mod, "_tavily_impl", mock_tavily)
        tavily_search("q", search_depth="advanced")
        mock_tavily.assert_called_once()
        mock_searxng.assert_not_called()
        assert mock_tavily.call_args[1]["search_depth"] == "advanced"

    def test_searxng_backend_client_side_excludes_and_overfetches(self, monkeypatch):
        monkeypatch.setenv("SEARCH_BACKEND", "searxng")
        results = [
            TavilyResult(url="https://morningstar.com/x", title="", content=""),
            TavilyResult(url="https://hellostake.com/au", title="", content=""),
            TavilyResult(url="https://lonsec.com.au/y", title="", content=""),
        ]
        mock_searxng = MagicMock(return_value=results)
        monkeypatch.setattr(tavily_mod, "_searxng_impl", mock_searxng)
        out = tavily_search(
            "q", max_results=5, exclude_domains=["morningstar.com", "lonsec.com.au"],
        )
        assert [r.url for r in out] == ["https://hellostake.com/au"]
        # over_fetch: exclude_domains 会让条数变少, 应多取几条再截断
        assert mock_searxng.call_args[1]["max_results"] == 15

    def test_searxng_backend_no_exclude_domains_no_overfetch(self, monkeypatch):
        monkeypatch.setenv("SEARCH_BACKEND", "searxng")
        mock_searxng = MagicMock(return_value=[])
        monkeypatch.setattr(tavily_mod, "_searxng_impl", mock_searxng)
        tavily_search("q", max_results=5)
        assert mock_searxng.call_args[1]["max_results"] == 5


class TestTavilyImplUnchanged:
    """确保原 Tavily 实现行为没被改造动到 (改名为 _tavily_impl, 逻辑不变)."""

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.setattr(tavily_mod, "load_env", lambda: None)
        with pytest.raises(TavilyError):
            _tavily_impl("q")

    def test_parses_results(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
        resp = _mock_resp(json_data={"results": [
            {"url": "https://hellostake.com", "title": "Stake", "content": "snip"},
        ]})
        monkeypatch.setattr(tavily_mod.requests, "post", MagicMock(return_value=resp))
        out = _tavily_impl("q")
        assert out == [TavilyResult(url="https://hellostake.com", title="Stake", content="snip")]

    def test_non_200_raises(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
        resp = _mock_resp(status_code=401, text="bad key")
        monkeypatch.setattr(tavily_mod.requests, "post", MagicMock(return_value=resp))
        with pytest.raises(TavilyError):
            _tavily_impl("q")
