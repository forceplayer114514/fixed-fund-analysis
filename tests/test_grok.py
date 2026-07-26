"""Spec G: Grok agentic search 客户端. 全部 mock HTTP, 不打网络。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _resp(status: int, payload: dict | None = None, text: str = ""):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload or {}
    m.text = text or json.dumps(payload or {})
    return m


def _ok_payload(content: str, sources: list[str]):
    return {
        "choices": [{"message": {"content": content, "annotations": []}}],
        "search_sources": [{"title": "", "type": "web", "url": u} for u in sources],
    }


class TestGrokAsk:
    def test_parses_content_and_sources(self, monkeypatch):
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        payload = _ok_payload("answer text", ["https://a.com", "https://b.com"])
        with patch.object(grok.requests, "post", return_value=_resp(200, payload)) as p:
            ans = grok.grok_ask("q")
        assert ans.content == "answer text"
        assert ans.sources == ["https://a.com", "https://b.com"]
        assert p.call_count == 1

    def test_retries_on_503_then_succeeds(self, monkeypatch):
        """503 upstream_unavailable = 中转站账号额度耗尽, 重试换账号即成功
        (Spec G 2.7 实测)。"""
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        monkeypatch.setattr(grok.time, "sleep", lambda s: None)
        payload = _ok_payload("ok", ["https://a.com"])
        seq = [_resp(503, {}, "upstream_unavailable"), _resp(200, payload)]
        with patch.object(grok.requests, "post", side_effect=seq) as p:
            ans = grok.grok_ask("q")
        assert ans.content == "ok"
        assert p.call_count == 2

    def test_raises_after_retries_exhausted(self, monkeypatch):
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        monkeypatch.setattr(grok.time, "sleep", lambda s: None)
        seq = [_resp(503, {}, "upstream_unavailable")] * 4
        with patch.object(grok.requests, "post", side_effect=seq) as p:
            with pytest.raises(grok.GrokError):
                grok.grok_ask("q", retries=3)
        assert p.call_count == 4  # 首次 + 3 次重试

    def test_missing_key_raises(self, monkeypatch):
        from llm_ingest import grok
        monkeypatch.delenv("GROK_API_KEY", raising=False)
        monkeypatch.setattr(grok, "load_env", lambda: None)
        with pytest.raises(grok.GrokError):
            grok.grok_ask("q")


class TestAnswerArchive:
    def test_parses_json_answer(self, monkeypatch):
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        content = json.dumps({
            "issuer_domain": "https://gcapinvest.com",
            "archive_url": "https://gcapinvest.com/our-lit",
        })
        payload = _ok_payload(content, ["https://gcapinvest.com/our-lit"])
        with patch.object(grok.requests, "post", return_value=_resp(200, payload)):
            a = grok.answer_archive("Gryphon Capital Income Trust", "Gryphon Capital")
        assert a.archive_url == "https://gcapinvest.com/our-lit"
        assert a.issuer_domain == "https://gcapinvest.com"

    def test_falls_back_to_regex_when_not_json(self, monkeypatch):
        """Grok 有时不听话直接说人话, 正则兜底抽 URL。"""
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        content = "月报归档页是 https://gcapinvest.com/our-lit , 请前往下载。"
        payload = _ok_payload(content, ["https://gcapinvest.com/our-lit"])
        with patch.object(grok.requests, "post", return_value=_resp(200, payload)):
            a = grok.answer_archive("Gryphon Capital Income Trust", "Gryphon Capital")
        assert a.archive_url == "https://gcapinvest.com/our-lit"

    def test_answer_has_no_pdf_field(self, monkeypatch):
        """Spec G 2.5 硬约束: 绝不问 Grok 要 PDF 链接, 它会按文件名规律编造
        且编造出的 URL 能 200 下载成功。ArchiveAnswer 不得有 pdf 字段。"""
        from llm_ingest import grok
        assert not hasattr(grok.ArchiveAnswer, "pdf_urls")
        assert "pdf_urls" not in grok.ArchiveAnswer.__dataclass_fields__

    def test_prompt_does_not_ask_for_pdf_links(self):
        """prompt 里不得出现要求列举 PDF 文件链接的措辞。"""
        from pathlib import Path
        import llm_ingest
        p = Path(llm_ingest.__file__).parent / "prompts" / "grok_archive.md"
        text = p.read_text().lower()
        for bad in ("list the pdf", "pdf urls", "pdf links", "列出.*pdf"):
            assert bad not in text, f"prompt 不得索要 PDF 链接: {bad!r}"


class TestAnswerFundmonitorsId:
    def test_parses_fundid_and_acccode(self, monkeypatch):
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        content = json.dumps({"fund_id": 1512, "acc_code": "fresnjxju"})
        payload = _ok_payload(content, ["https://www.fundmonitors.com/x"])
        with patch.object(grok.requests, "post", return_value=_resp(200, payload)):
            got = grok.answer_fundmonitors_id("Yarra Enhanced Income Fund")
        assert got == (1512, "fresnjxju")

    def test_returns_none_when_not_found(self, monkeypatch):
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        payload = _ok_payload(json.dumps({"fund_id": None}), [])
        with patch.object(grok.requests, "post", return_value=_resp(200, payload)):
            assert grok.answer_fundmonitors_id("Nonexistent Fund") is None

    def test_grok_error_returns_none(self, monkeypatch):
        """上游失败不抛给调用方, 返 None 让 probe 走既有的 no_fundid 分支。"""
        from llm_ingest import grok
        monkeypatch.setenv("GROK_API_KEY", "k")
        monkeypatch.setattr(grok.time, "sleep", lambda s: None)
        seq = [_resp(503, {}, "x")] * 4
        with patch.object(grok.requests, "post", side_effect=seq):
            assert grok.answer_fundmonitors_id("Any Fund") is None
