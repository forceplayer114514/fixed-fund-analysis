"""ingest.py 中 solver 分支的入库测试(mock download_and_extract_candidates)。

覆盖:
- solver 分支自动入库(resolved -> monthly_returns,verify_windows 记录)
- 非 resolved -> pending_review 带 candidates_json
- |r|>=0.5 兜底(优先于 resolved 直通)
- dry_run 不入库,返 solver_report
- update 增量:已入库月作锚点,新月自动定值
"""
from __future__ import annotations

import json

import pytest

from lib.candidates import ReturnCandidate
from lib.db import (
    get_fund,
    get_monthly_returns,
    list_pending_review,
)
from lib.ingest import ingest_discovery, update_fund


class _StubReport:
    """最小 DiscoveryReport 替身。"""
    def __init__(self, fund_id, links, gaps=None, inception_date=None,
                 inception_assumed=False):
        self.fund_id = fund_id
        self.gaps = gaps or []
        self.inception_date = inception_date
        self.inception_assumed = inception_assumed
        self.per_level_payload = {
            "L1": {"fetch_method": "pdf",
                   "confirmed_url": "https://x/archive",
                   "links": links},
        }
        self.per_level_contribution = {"L1": len(links)}


def _c(v, tag="returned_pct", priority=1):
    return ReturnCandidate(
        value=v,
        source_quote=f"returned {v * 100:.4f}%",
        pattern_tag=tag,
        char_pos=0, priority=priority,
    )


def _r(m1=None, m3=None, m6=None, m12=None, precision=2, parse_error=False):
    return {"1mo": m1, "3mo": m3, "6mo": m6, "12mo": m12,
            "inception": None, "parse_error": parse_error,
            "extractor_tag": "perf", "precision": precision}


def _cp(vs):
    p = 1.0
    for v in vs:
        p *= (1.0 + v)
    return p - 1.0


# --- resolved -> monthly_returns 直通入库 ---
def test_solver_resolved_ingests_to_monthly_returns(db_conn, monkeypatch, tmp_path):
    """通过 solve_series 数学准入的月份自动入库,verify_windows 与 pattern_tag
    写入 monthly_returns。"""
    truth = [0.01, 0.02, -0.005, 0.008, 0.015, 0.007]
    yms = [f"2025-{i:02d}" for i in range(1, 7)]
    links = [(ym, f"https://x/{ym}.pdf") for ym in yms]

    def fake_extract(links, dest_dir, max_workers=None, max_pages=None):
        out = []
        for i, (ym, _url) in enumerate(links):
            cands = [_c(truth[i]), _c(truth[i] + 0.05)]  # 真值 + 干扰
            rolling = _r()
            if i >= 2:
                rolling["3mo"] = _cp(truth[i - 2: i + 1])
            if i >= 5:
                rolling["6mo"] = _cp(truth[0:6])
            out.append((ym, cands, rolling))
        return out

    monkeypatch.setattr("lib.ingest.pdf_cache_dir", lambda fid: str(tmp_path))
    monkeypatch.setattr("lib.extract.download_and_extract_candidates", fake_extract)

    db_path = tmp_path / "solver_test.db"
    # 用真实 sqlite,让 ingest 走完整入库路径
    report = _StubReport("stake_test", links, inception_date="2024-08-31")
    result = ingest_discovery(
        report, "Stake Test Fund",
        db_path=str(db_path),
        extractor_name="solver",
        verified_at="2026-07-15",
    )
    assert result["gate_pass"] is True
    # solver_report 结构 + 至少 4 月 resolved(前 2 月无 3mo 约束,可能 unverifiable)
    assert "solver_report" in result
    resolved_yms = [ym for ym, r in result["solver_report"].items()
                    if r["status"] == "resolved"]
    assert len(resolved_yms) >= 3
    # DB 验证:resolved 月已入 monthly_returns,verify_windows 非 NULL
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT date, net_return, verify_windows, pattern_tag, source_quote "
        "FROM monthly_returns WHERE fund_id='stake_test' ORDER BY date"
    ).fetchall()
    conn.close()
    assert len(rows) == len(resolved_yms)
    for row in rows:
        assert row["verify_windows"] is not None
        assert row["verify_windows"] >= 2
        assert row["pattern_tag"] == "returned_pct"
        assert "returned" in row["source_quote"]


# --- 非 resolved -> pending_review 带 candidates_json ---
def test_solver_non_resolved_to_pending_with_candidates_json(monkeypatch, tmp_path):
    """无候选月 -> no_candidates pending;candidates_json 是有效 JSON list。"""
    links = [("2025-01", "https://x/1.pdf"),
             ("2025-02", "https://x/2.pdf"),
             ("2025-03", "https://x/3.pdf")]

    def fake_extract(links, dest_dir, max_workers=None, max_pages=None):
        # 2025-02 空池 -> no_candidates
        return [
            ("2025-01", [_c(0.01)], _r()),
            ("2025-02", [], _r(parse_error=True)),
            ("2025-03", [_c(0.005)], _r()),
        ]

    monkeypatch.setattr("lib.ingest.pdf_cache_dir", lambda fid: str(tmp_path))
    monkeypatch.setattr("lib.extract.download_and_extract_candidates", fake_extract)

    db_path = tmp_path / "np.db"
    report = _StubReport("fund_np", links, inception_date="2024-11-30")
    result = ingest_discovery(
        report, "NP Fund",
        db_path=str(db_path),
        extractor_name="solver",
        verified_at="2026-07-15",
    )
    # gate_pass 依然 True(空池月非致命,进 pending)
    assert result["pending_review_count"] >= 1
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    prs = conn.execute(
        "SELECT date, review_reason, candidates_json, extract_method "
        "FROM pending_review WHERE fund_id='fund_np'"
    ).fetchall()
    conn.close()
    reasons = {p["review_reason"] for p in prs}
    assert "no_candidates" in reasons
    for p in prs:
        assert p["extract_method"] == "solver"
        # candidates_json 是 JSON list(no_candidates 时空 list)
        assert p["candidates_json"] is not None
        parsed = json.loads(p["candidates_json"])
        assert isinstance(parsed, list)


# --- |r|>=0.5 兜底优先 -> pending abs_return_exceeds_threshold ---
def test_solver_over_threshold_priority(monkeypatch, tmp_path):
    """resolved 月若 |r|>=0.5 -> 强制 pending(异常值必须人工复核)。构造 6 月
    使 3/6mo 双窗口验证通过。"""
    truth = [0.55, 0.60, 0.50, 0.52, 0.58, 0.53]  # 全 >=0.5(灾难年场景)
    yms = [f"2025-{i:02d}" for i in range(1, 7)]
    links = [(ym, f"https://x/{ym}.pdf") for ym in yms]

    def fake_extract(links, dest_dir, max_workers=None, max_pages=None):
        out = []
        for i, (ym, _url) in enumerate(links):
            cands = [_c(truth[i])]
            rolling = _r()
            if i >= 2:
                rolling["3mo"] = _cp(truth[i - 2: i + 1])
            if i >= 5:
                rolling["6mo"] = _cp(truth[0:6])
            out.append((ym, cands, rolling))
        return out

    monkeypatch.setattr("lib.ingest.pdf_cache_dir", lambda fid: str(tmp_path))
    monkeypatch.setattr("lib.extract.download_and_extract_candidates", fake_extract)

    db_path = tmp_path / "over.db"
    report = _StubReport("fund_over", links, inception_date="2024-11-30")
    result = ingest_discovery(
        report, "Over Fund",
        db_path=str(db_path),
        extractor_name="solver",
    )
    # 全 |r|>=0.5 -> 全 pending(即使 solver resolved,ingest 层的 |r|>=0.5
    # 兜底优先于直通入库)
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    prs = conn.execute(
        "SELECT review_reason FROM pending_review WHERE fund_id='fund_over'"
    ).fetchall()
    mr = conn.execute(
        "SELECT date FROM monthly_returns WHERE fund_id='fund_over'"
    ).fetchall()
    conn.close()
    assert len(mr) == 0  # 高值全部不入 monthly_returns
    assert any(p["review_reason"] == "abs_return_exceeds_threshold" for p in prs)


# --- dry_run 不入库,返 solver_report ---
def test_solver_dry_run_no_ingest(monkeypatch, tmp_path):
    truth = [0.01, 0.02, -0.005]
    yms = ["2025-01", "2025-02", "2025-03"]
    links = [(ym, f"https://x/{ym}.pdf") for ym in yms]
    r3 = _cp(truth)

    def fake_extract(links, dest_dir, max_workers=None, max_pages=None):
        return [
            ("2025-01", [_c(truth[0])], _r()),
            ("2025-02", [_c(truth[1])], _r()),
            ("2025-03", [_c(truth[2])], _r(m3=r3)),
        ]

    monkeypatch.setattr("lib.ingest.pdf_cache_dir", lambda fid: str(tmp_path))
    monkeypatch.setattr("lib.extract.download_and_extract_candidates", fake_extract)

    db_path = tmp_path / "dry.db"
    report = _StubReport("fund_dry", links, inception_date="2024-11-30")
    result = ingest_discovery(
        report, "Dry Fund",
        db_path=str(db_path),
        extractor_name="solver",
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert "solver_report" in result
    # DB 应无 fund 记录
    import sqlite3
    import os
    if os.path.exists(str(db_path)):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM funds WHERE fund_id='fund_dry'"
        ).fetchone()
        conn.close()
        assert row is None  # dry-run 未创建 fund


# --- update solver 增量:已入库月作锚点 ---
def test_solver_update_uses_existing_as_anchor(monkeypatch, tmp_path):
    """已入库月在 update solver 分支作 anchor,新月由约束反解。"""
    # 先手工造一只已入库的 fund + 2 个月
    from lib.db import create_fund, ensure_tables, get_connection, upsert_monthly_return

    db_path = tmp_path / "upd.db"
    conn = get_connection(str(db_path))
    ensure_tables(conn)
    create_fund(
        conn, fund_id="fund_upd", fund_name="Upd Fund",
        confirmed_url="https://x/arch", fetch_method="pdf",
        url_type="archive_page", extractor_verified=1,
    )
    upsert_monthly_return(conn, fund_id="fund_upd",
                          date="2025-01-31", net_return=0.01,
                          commentary_truth=0.01)
    upsert_monthly_return(conn, fund_id="fund_upd",
                          date="2025-02-28", net_return=0.02,
                          commentary_truth=0.02)
    conn.close()

    # update 抓来一个新月 2025-03(候选 0.005),3mo 约束覆盖 01/02/03 +
    # 1mo 确认窗口(该月自身 rolling 1mo=0.005),verify_windows=2 -> resolved
    truth = [0.01, 0.02, 0.005]
    r3 = _cp(truth)

    def fake_extract(links, dest_dir, max_workers=None, max_pages=None):
        return [("2025-03", [_c(0.005)], _r(m1=0.005, m3=r3))]

    # 用 stub run_discovery 直接返回 fund_info
    class _StubDR:
        gaps = []
        inception_date = "2024-12-31"
        inception_assumed = False
        per_level_payload = {
            "L1": {"fetch_method": "pdf", "confirmed_url": "https://x/arch",
                   "links": [("2025-03", "https://x/2025-03.pdf")]},
        }
        per_level_contribution = {"L1": 1}

        def __init__(self, fund_id):
            self.fund_id = fund_id

    monkeypatch.setattr("lib.strategies.run_discovery",
                        lambda info: _StubDR(info["fund_id"]))
    monkeypatch.setattr("lib.ingest.pdf_cache_dir", lambda fid: str(tmp_path))
    monkeypatch.setattr("lib.extract.download_and_extract_candidates", fake_extract)

    result = update_fund(
        "fund_upd", db_path=str(db_path),
        archive_markdown="stub",
        extractor_name="solver",
    )
    assert result["updated"] is True
    # 03 被约束定值(3mo 已定 01/02 -> 唯一反解 03=0.005)
    import sqlite3
    c2 = sqlite3.connect(str(db_path))
    c2.row_factory = sqlite3.Row
    rows = c2.execute(
        "SELECT date, net_return FROM monthly_returns "
        "WHERE fund_id='fund_upd' ORDER BY date"
    ).fetchall()
    c2.close()
    assert len(rows) == 3
    assert rows[2]["date"] == "2025-03-31"
    assert abs(rows[2]["net_return"] - 0.005) < 1e-9
