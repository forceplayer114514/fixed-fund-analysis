"""Spec G 4.6: IngestRequest.search_engine 字段。"""
import pytest
from pydantic import ValidationError

from app.schemas import IngestRequest


def test_default_is_tavily():
    r = IngestRequest(fund_name="Some Fund")
    assert r.search_engine == "tavily"


def test_accepts_grok():
    r = IngestRequest(fund_name="Some Fund", search_engine="grok")
    assert r.search_engine == "grok"


def test_rejects_unknown_engine():
    with pytest.raises(ValidationError):
        IngestRequest(fund_name="Some Fund", search_engine="bing")
