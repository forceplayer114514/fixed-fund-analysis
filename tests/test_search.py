"""llm_ingest/search.py Tavily 搜索客户端测试。

Spec G: SearXNG 后端与 SEARCH_BACKEND 分派已删除 (该服务已下线,
localhost:8081 不通, 且该环境变量全仓库从未设置过, 旧默认值一度让每次
搜索都静默降级到 sub2api web_search)。现 tavily_search() 直接调
_tavily_impl, 无分派逻辑。
"""
from unittest.mock import MagicMock

import pytest

from llm_ingest import search as tavily_mod
from llm_ingest.search import (
    TavilyError,
    TavilyResult,
    _tavily_impl,
    tavily_search,
)


def _mock_resp(status_code: int = 200, json_data=None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data or {}
    r.text = text
    return r


class TestTavilySearchDispatch:
    def test_default_backend_is_tavily(self, monkeypatch):
        """tavily_search 必须直接调 _tavily_impl, 无后端分派。"""
        mock_tavily = MagicMock(return_value=[])
        monkeypatch.setattr(tavily_mod, "_tavily_impl", mock_tavily)
        tavily_search("q", search_depth="advanced")
        mock_tavily.assert_called_once()
        assert mock_tavily.call_args[1]["search_depth"] == "advanced"


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
