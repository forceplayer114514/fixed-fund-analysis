"""issuer_rules.get_issuer_rule 单测.

覆盖:
  1. 关键词命中 (fund_name / issuer 任一含关键词) -> 返回对应规则片段
  2. 大小写不敏感
  3. 多关键词命中 -> 全部拼接
  4. 无命中 -> 空字符串 (不强加任何发行商假设)
  5. extract_from_pdf/html/csv 按 fund_name/issuer 动态拼入规则, 不匹配则不附加
"""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest.issuer_rules import get_issuer_rule


def test_no_match_returns_empty():
    assert get_issuer_rule("Yarra Enhanced Income Fund", "Yarra Capital") == ""


def test_bentham_keyword_in_fund_name_matches():
    rule = get_issuer_rule("Bentham Global Income Fund", "")
    assert "Bentham" in rule
    assert "gross" in rule


def test_case_insensitive_match():
    rule = get_issuer_rule("BENTHAM GLOBAL INCOME FUND", "")
    assert "Bentham" in rule


def test_match_via_issuer_field_not_fund_name():
    rule = get_issuer_rule("Some Fund", "Coolabah Capital Investments")
    assert "Coolabah" in rule


def test_multiple_keyword_hits_concatenated():
    rule = get_issuer_rule("Stake x Macquarie combo fund", "")
    assert "Stake" in rule
    assert "Macquarie" in rule


def test_empty_inputs_return_empty():
    assert get_issuer_rule("", "") == ""
    assert get_issuer_rule() == ""


# ---------- 抽取函数动态拼接验证 ----------

def _mock_resp(payload: dict) -> MagicMock:
    r = MagicMock()
    r.text = json.dumps(payload)
    return r


def test_extract_from_pdf_injects_matched_rule(tmp_path, monkeypatch):
    """fund_name 命中 Bentham -> prompt 含专项规则; 未命中时不含."""
    from llm_ingest import extract as ex_mod
    from llm_ingest import pdf as pdf_mod

    pdf_p = tmp_path / "2026-05.pdf"
    pdf_p.write_bytes(b"%PDF-1.4\n%dummy\n")
    monkeypatch.setattr(pdf_mod, "clip_pages", lambda path, max_pages=2: b"stub")
    monkeypatch.setattr(pdf_mod, "page_count", lambda path: 1)

    captured = {}

    class _FakeClient:
        def messages_with_pdf(self, prompt, pdf_bytes, max_tokens=None):
            captured["prompt"] = prompt
            return _mock_resp({"ym": "2026-05", "kind": "not_found", "not_found": True})

    ex_mod.extract_from_pdf(pdf_p, "2026-05", client=_FakeClient(),
                            fund_name="Bentham Global Income Fund")
    assert "Bentham" in captured["prompt"]
    assert "发行商专项" in captured["prompt"]


def test_extract_from_pdf_no_rule_when_no_match(tmp_path, monkeypatch):
    from llm_ingest import extract as ex_mod
    from llm_ingest import pdf as pdf_mod

    pdf_p = tmp_path / "2026-05.pdf"
    pdf_p.write_bytes(b"%PDF-1.4\n%dummy\n")
    monkeypatch.setattr(pdf_mod, "clip_pages", lambda path, max_pages=2: b"stub")
    monkeypatch.setattr(pdf_mod, "page_count", lambda path: 1)

    captured = {}

    class _FakeClient:
        def messages_with_pdf(self, prompt, pdf_bytes, max_tokens=None):
            captured["prompt"] = prompt
            return _mock_resp({"ym": "2026-05", "kind": "not_found", "not_found": True})

    ex_mod.extract_from_pdf(pdf_p, "2026-05", client=_FakeClient(),
                            fund_name="Yarra Enhanced Income Fund")
    assert "发行商专项" not in captured["prompt"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
