"""P0 name guard + 白名单短路测试 (Spec A).

覆盖 `llm_ingest.fundmonitors` 三块新增能力:
  1. `_tokenize`: 停用词/括号剔除/连字符保留/短 token 过滤/大小写。
  2. `_name_match`: 严格 AND, 首 2000 字全命中, 拦 Tavily 词面误判。
  3. `probe`: 白名单短路跳过 Tavily 跳过 guard; Tavily 通路应用 guard。
  4. `_lookup_whitelist`: 列不存在容错 (task #15 迁移前空 DB 不炸)。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm_ingest import fundmonitors as fm


# ---------------- A. _tokenize (8) ----------------

def test_tokenize_basic() -> None:
    assert fm._tokenize("Bentham Global Income Fund") == [
        "bentham", "global", "income",
    ]


def test_tokenize_strips_parens_share_class() -> None:
    got = fm._tokenize("Coolabah Active Composite Bond Fund (Assisted)")
    assert got == ["coolabah", "active", "composite", "bond"]


def test_tokenize_strips_parens_wholesale() -> None:
    got = fm._tokenize("Bentham Global Income Fund (Wholesale)")
    assert got == ["bentham", "global", "income"]


def test_tokenize_keeps_hyphenated_compound() -> None:
    got = fm._tokenize("Coolabah Floating-Rate High-Yield Fund")
    assert "floating-rate" in got
    assert "high-yield" in got
    # 连字符复合词不该再被拆成裸词
    assert "floating" not in got
    assert "rate" not in got


def test_tokenize_drops_short_tokens() -> None:
    got = fm._tokenize("US Fund AU Class Global Bond")
    # US/AU 长度 2 -> 剔; Fund/Class 停用词 -> 剔
    assert "us" not in got
    assert "au" not in got
    assert "global" in got
    assert "bond" in got


def test_tokenize_case_insensitive() -> None:
    got = fm._tokenize("BENTHAM GLOBAL INCOME FUND")
    # 全部结果都应是小写
    assert all(t == t.lower() for t in got)
    assert got == ["bentham", "global", "income"]


def test_tokenize_removes_stopwords() -> None:
    got = fm._tokenize(
        "The Bentham Capital Management Global Income Fund Trust Limited"
    )
    assert got == ["bentham", "global", "income"]


def test_tokenize_empty_after_stopwords_returns_empty() -> None:
    assert fm._tokenize("The Fund Trust Ltd") == []


# ---------------- B. _name_match (10) ----------------

def test_name_match_exact() -> None:
    md = "Bentham Global Income Fund monthly report ..."
    matched, reason = fm._name_match("Bentham Global Income Fund", md)
    assert matched is True
    assert reason == "ok"


def test_name_match_coolabah_smashup_挡下() -> None:
    """SmarterMoney 页 + Coolabah Floating-Rate High-Yield 查询 → 拦下。"""
    md = (
        "SmarterMoney Active Cash Fund overview and holdings. "
        "This is Coolabah Capital's cash strategy. No floating rate here."
    )
    matched, reason = fm._name_match(
        "Coolabah Floating-Rate High-Yield Fund", md
    )
    assert matched is False
    assert "floating-rate" in reason
    assert "high-yield" in reason


def test_name_match_share_class_variant() -> None:
    """主页面 (Wholesale), 查询 (Retail) -> 括号剔完命中主品牌。"""
    md = "Bentham Global Income Fund (Wholesale) - Full profile ..."
    matched, reason = fm._name_match(
        "Bentham Global Income Fund (Retail)", md
    )
    assert matched is True
    assert reason == "ok"


def test_name_match_partial_miss() -> None:
    md = "Bentham Global Fund report (缺 income 一 token)"
    matched, reason = fm._name_match("Bentham Global Income Fund", md)
    assert matched is False
    assert "income" in reason


def test_name_match_only_head_2000_chars() -> None:
    padding = "x" * 2100
    md = padding + " Bentham Global Income Fund"
    matched, reason = fm._name_match(
        "Bentham Global Income Fund", md, head_chars=2000
    )
    assert matched is False
    # 至少缺 bentham / global / income 之一
    assert "missing_tokens" in reason


def test_name_match_empty_markdown() -> None:
    matched, reason = fm._name_match("Bentham Global Income Fund", "")
    assert matched is False
    assert "missing_tokens" in reason


def test_name_match_empty_query_after_stopword() -> None:
    matched, reason = fm._name_match("The Fund", "some markdown content")
    assert matched is False
    assert reason == "no_tokens_after_stopword_filter"


def test_name_match_hyphenated_query_needs_hyphenated_md() -> None:
    """查 floating-rate, md 里只有 'floating rate' 空格分开 -> 不认。"""
    md = "Coolabah floating rate high yield fund overview"
    matched, reason = fm._name_match(
        "Coolabah Floating-Rate High-Yield Fund", md
    )
    assert matched is False
    assert "floating-rate" in reason


def test_name_match_case_insensitive() -> None:
    md = "BENTHAM GLOBAL INCOME FUND MONTHLY UPDATE"
    matched, reason = fm._name_match("bentham global income fund", md)
    assert matched is True
    assert reason == "ok"


def test_name_match_extra_content_before_fund_name() -> None:
    """2000 字前有别的内容, 只要 head 里 token 全命中就算过。"""
    md = (
        "Some navigation header text and other prose. "
        "Bentham Global Income Fund quarterly review."
    )
    matched, reason = fm._name_match("Bentham Global Income Fund", md)
    assert matched is True
    assert reason == "ok"


# ---------------- C. probe 集成 (monkeypatch) (5) ----------------

# 可通过 gate 的极简 markdown: Historical Performance + 两月连续 (无 YTD 列)
_OK_MD = (
    "Historical Performance\n"
    "| Year | Jan % | Feb % |\n"
    "| 2024 | 0.01 | 0.02 |\n"
)


def _make_conn_with_whitelist(fund_id: str, fmid: int, acc: str) -> sqlite3.Connection:
    """建含 fundmonitors 白名单列的临时 DB 并塞一条命中记录。"""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE funds (
            fund_id TEXT PRIMARY KEY,
            fund_name TEXT,
            fundmonitors_fund_id INTEGER,
            fundmonitors_acc_code TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO funds (fund_id, fund_name, fundmonitors_fund_id, "
        "fundmonitors_acc_code) VALUES (?, ?, ?, ?)",
        (fund_id, "X Fund", fmid, acc),
    )
    conn.commit()
    return conn


def test_probe_whitelist_shortcircuits_tavily(monkeypatch) -> None:
    """白名单命中 -> 不调 Tavily, 直接 fetch, URL 含 FundID=xxx。"""
    conn = _make_conn_with_whitelist("fund_x", 1512, "abcd")

    def _no_tavily(_name: str):  # pragma: no cover
        raise AssertionError("Tavily 不应被调用 (白名单已命中)")

    monkeypatch.setattr(fm, "find_fundid_via_tavily", _no_tavily)

    captured = {}

    def _fake_fetch(url, timeout=30):
        captured["url"] = url
        return (_OK_MD, "ok")

    monkeypatch.setattr(fm, "fetch_profile_markdown", _fake_fetch)

    result = fm.probe("X Fund", fund_id="fund_x", db_conn=conn)
    assert result["status"] == "ok"
    assert "FundID=1512" in captured["url"]
    assert "AccCode=abcd" in captured["url"]


def test_probe_whitelist_skips_name_guard(monkeypatch) -> None:
    """白名单 + markdown 完全不含 fund_name -> 仍返 ok (背书跳 guard)。"""
    conn = _make_conn_with_whitelist("fund_y", 9999, "")

    def _no_tavily(_name: str):  # pragma: no cover
        raise AssertionError("Tavily 不应被调用")

    monkeypatch.setattr(fm, "find_fundid_via_tavily", _no_tavily)
    # markdown 里完全没有 "Totally Unrelated Fund Name" 字面
    monkeypatch.setattr(
        fm, "fetch_profile_markdown", lambda *a, **kw: (_OK_MD, "ok")
    )
    result = fm.probe(
        "Totally Unrelated Fund Name", fund_id="fund_y", db_conn=conn
    )
    assert result["status"] == "ok"


def test_probe_tavily_path_applies_name_guard(monkeypatch) -> None:
    """未白名单 + Tavily 返 URL + fetch 返 SmarterMoney md -> name_mismatch。"""
    monkeypatch.setattr(
        fm, "find_fundid_via_tavily", lambda _n: (999, "fake"),
    )
    smarter_md = (
        "SmarterMoney Active Cash Fund overview. "
        "This is a different product entirely."
    )
    monkeypatch.setattr(
        fm, "fetch_profile_markdown", lambda *a, **kw: (smarter_md, "ok"),
    )
    result = fm.probe("Coolabah Floating-Rate High-Yield Fund")
    assert result["status"] == "name_mismatch"
    assert result["errors"]
    # reason 里应能看到缺失的关键 token
    assert any("floating-rate" in e for e in result["errors"])


def test_probe_no_fundid_returns(monkeypatch) -> None:
    """Tavily 返 None -> status='no_fundid'。"""
    monkeypatch.setattr(fm, "find_fundid_via_tavily", lambda _n: None)
    result = fm.probe("Unknown Fund")
    assert result["status"] == "no_fundid"
    assert result["records"] == []


def test_probe_fetch_fail_bypasses_name_guard(monkeypatch) -> None:
    """fetch_fail 早退, name guard 不该被跑到 (跑到会 raise)。"""
    monkeypatch.setattr(
        fm, "find_fundid_via_tavily", lambda _n: (7, "xx"),
    )
    monkeypatch.setattr(
        fm, "fetch_profile_markdown", lambda *a, **kw: (None, "fetch_fail"),
    )

    def _sentinel(*a, **kw):  # pragma: no cover
        raise AssertionError("fetch_fail 后不应再跑 name_match")

    monkeypatch.setattr(fm, "_name_match", _sentinel)

    result = fm.probe("Bentham Global Income Fund")
    assert result["status"] == "fetch_fail"


# ---------------- D. 白名单列不存在容错 (1) ----------------

def test_lookup_whitelist_column_missing_returns_none() -> None:
    """funds 表没 fundmonitors_fund_id 列 -> 返 None, 不 raise。"""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE funds (fund_id TEXT PRIMARY KEY, fund_name TEXT)"
    )
    conn.execute("INSERT INTO funds VALUES (?, ?)", ("fund_z", "Z Fund"))
    conn.commit()
    assert fm._lookup_whitelist(conn, "fund_z") is None
