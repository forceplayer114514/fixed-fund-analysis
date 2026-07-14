"""A4 ingest 层三层 pending 分流 + EXTRACTOR_MISMATCH + dry_run + verify 端到端。

测 ingest_discovery / update_fund 的 generic 准入分流:
- |r|>=0.5 -> pending abs_return_exceeds_threshold(优先级最高)
- generic+ambiguous -> pending ambiguous_subject(反 benchmark 守卫)
- generic+!ambiguous+!verified -> pending generic_first_use(新基金首批全审)
- generic+!ambiguous+verified -> 直通(update 增量)
- 专属 -> 直通
另测 EXTRACTOR_MISMATCH 指引、dry_run 不入库、verify_extractor 端到端链路。
mock download_and_extract_parallel(不触网),tmp_path 临时 DB。
"""
from __future__ import annotations

import pytest

from lib.strategies import DiscoveryReport
from lib.ingest import ingest_discovery, update_fund
from lib.extract import ExtractedReturn
from lib.db import (
    create_fund as _create_fund,
    ensure_tables,
    get_connection,
    get_extractor_verified,
    get_fund,
    get_monthly_returns,
    list_pending_review,
    upsert_monthly_return as _upsert,
    verify_extractor,
)


_PE = {"1mo": None, "3mo": None, "6mo": None, "12mo": None,
       "inception": None, "parse_error": True}


def _er(v, ambiguous=False):
    return ExtractedReturn(value=v, source_quote="returned {:.2f}%".format(v * 100),
                           ambiguous=ambiguous)


def _disc_report(fund_id="a4_fund", links=None, gaps=None,
                 inception_date="2025-02-28"):
    payload = {"fetch_method": "pdf", "confirmed_url": "https://example.com/archive",
               "links": links}
    obtained = [ym for ym, _ in links]
    return DiscoveryReport(
        fund_id=fund_id, inception_date=inception_date,
        obtained=obtained, gaps=gaps or [],
        per_level_contribution={"L1": len(obtained)},
        per_level_payload={"L1": payload},
    )


def _fake_parallel(rows):
    """rows: [(ym, er_or_None), ...] -> 补 _PE rolling 凑 download_and_extract_parallel 返值。"""
    def _fp(links, dest_dir, max_workers=None, extractor=None, return_full=False):
        out = []
        for ym, er in rows:
            out.append((ym, er, _PE))
        return out
    return _fp


# --- 三层分流:generic 新基金首批全 pending ---


@pytest.mark.unit
def test_generic_new_fund_all_pending_generic_first_use(monkeypatch, tmp_path):
    """generic + 新基金(!verified) + normal returns -> 全进 pending generic_first_use,
    不入 monthly_returns(新基金首批全审,A4a)。"""
    db_path = str(tmp_path / "a4.db")
    report = _disc_report(
        links=[("2025-02", "https://x/feb.pdf"), ("2025-03", "https://x/mar.pdf"),
               ("2025-04", "https://x/apr.pdf")],
    )
    monkeypatch.setattr("lib.ingest.download_and_extract_parallel",
                        _fake_parallel([("2025-02", _er(0.005)),
                                        ("2025-03", _er(0.006)),
                                        ("2025-04", _er(0.004))]))
    result = ingest_discovery(report, "A4 Fund", db_path=db_path)  # 默认 generic
    assert result["gate_pass"] is True
    assert result["months"] == 0  # 全 pending,不入库
    assert result["pending_review_count"] == 3
    conn = get_connection(db_path)
    try:
        assert get_monthly_returns(conn, "a4_fund") == []
        assert get_extractor_verified(conn, "a4_fund") == 0
        pending = list_pending_review(conn, "a4_fund")
        assert {p["review_reason"] for p in pending} == {"generic_first_use"}
    finally:
        conn.close()


@pytest.mark.unit
def test_generic_ambiguous_to_pending_ambiguous_subject(monkeypatch, tmp_path):
    """generic + ambiguous(反 benchmark 守卫命中) -> pending ambiguous_subject,
    即使 !verified 也不走 generic_first_use(ambiguous 优先,A4c)。"""
    db_path = str(tmp_path / "a4.db")
    report = _disc_report(
        links=[("2025-02", "https://x/feb.pdf"), ("2025-03", "https://x/mar.pdf")],
    )
    monkeypatch.setattr("lib.ingest.download_and_extract_parallel",
                        _fake_parallel([("2025-02", _er(0.005)),
                                        ("2025-03", _er(0.007, ambiguous=True))]))
    result = ingest_discovery(report, "Amb Fund", db_path=db_path)
    assert result["pending_review_count"] == 2
    assert result["months"] == 0
    conn = get_connection(db_path)
    try:
        pending = list_pending_review(conn, "a4_fund")
        reasons = {p["review_reason"] for p in pending}
        assert reasons == {"generic_first_use", "ambiguous_subject"}
        amb = [p for p in pending if p["review_reason"] == "ambiguous_subject"][0]
        assert amb["date"] == "2025-03-31"
    finally:
        conn.close()


@pytest.mark.unit
def test_generic_over_threshold_priority_over_ambiguous(monkeypatch, tmp_path):
    """|r|>=0.5 优先级最高:即使 ambiguous=True 也走 abs_return_exceeds_threshold(A4 分流序)。"""
    db_path = str(tmp_path / "a4.db")
    report = _disc_report(links=[("2025-02", "https://x/feb.pdf")])
    monkeypatch.setattr("lib.ingest.download_and_extract_parallel",
                        _fake_parallel([("2025-02", _er(0.6, ambiguous=True))]))
    result = ingest_discovery(report, "Ovr Fund", db_path=db_path)
    assert result["pending_review_count"] == 1
    conn = get_connection(db_path)
    try:
        pending = list_pending_review(conn, "a4_fund")
        assert pending[0]["review_reason"] == "abs_return_exceeds_threshold"
    finally:
        conn.close()


# --- 专属提取器直通 ---


@pytest.mark.unit
def test_dedicated_bentham_new_fund_direct_ingest(monkeypatch, tmp_path):
    """专属(bentham) + 新基金 + normal -> 直通 monthly_returns(不进 first_use pending)。
    专属提取器信任等级高于 generic(人写时已过样本核验)。"""
    db_path = str(tmp_path / "a4.db")
    report = _disc_report(
        links=[("2025-02", "https://x/feb.pdf"), ("2025-03", "https://x/mar.pdf"),
               ("2025-04", "https://x/apr.pdf")],
    )
    monkeypatch.setattr("lib.ingest.download_and_extract_parallel",
                        _fake_parallel([("2025-02", _er(0.005)),
                                        ("2025-03", _er(0.006)),
                                        ("2025-04", _er(0.004))]))
    result = ingest_discovery(report, "Bentham Fund", db_path=db_path,
                              extractor_name="bentham")
    assert result["months"] == 3
    assert result["pending_review_count"] == 0
    conn = get_connection(db_path)
    try:
        assert len(get_monthly_returns(conn, "a4_fund")) == 3
    finally:
        conn.close()


# --- EXTRACTOR_MISMATCH(A3) ---


@pytest.mark.unit
def test_extractor_mismatch_reports_guidance(monkeypatch, tmp_path):
    """generic 正则没匹配(er=None -> failed_months 非空)-> extractor_mismatch=True,
    errors 含 EXTRACTOR_MISMATCH 指引(走 add_fixed_fund.md 4.5),成功月仍处理(进 pending)。"""
    db_path = str(tmp_path / "a4.db")
    report = _disc_report(
        links=[("2025-02", "https://x/feb.pdf"), ("2025-03", "https://x/mar.pdf")],
    )
    monkeypatch.setattr("lib.ingest.download_and_extract_parallel",
                        _fake_parallel([("2025-02", None),  # 提取失败
                                        ("2025-03", _er(0.005))]))
    result = ingest_discovery(report, "Mis Fund", db_path=db_path)  # generic
    assert result["extractor_mismatch"] is True
    assert result["mismatch_months"] == ["2025-02"]
    assert any("EXTRACTOR_MISMATCH" in e for e in result["errors"])
    assert any("add_fixed_fund.md 4.5" in e for e in result["errors"])
    # 成功月 03 仍处理(generic !verified -> pending generic_first_use)
    assert result["pending_review_count"] == 1
    conn = get_connection(db_path)
    try:
        assert get_monthly_returns(conn, "a4_fund") == []
    finally:
        conn.close()


# --- dry_run(4.5 全量回跑验收) ---


@pytest.mark.unit
def test_dry_run_no_ingest(monkeypatch, tmp_path):
    """dry_run=True -> 跑提取+gate 不入库,出报告(新专属提取器全量回跑验收用)。"""
    db_path = str(tmp_path / "a4.db")
    report = _disc_report(
        links=[("2025-02", "https://x/feb.pdf"), ("2025-03", "https://x/mar.pdf"),
               ("2025-04", "https://x/apr.pdf")],
    )
    monkeypatch.setattr("lib.ingest.download_and_extract_parallel",
                        _fake_parallel([("2025-02", _er(0.005)),
                                        ("2025-03", _er(0.006)),
                                        ("2025-04", _er(0.004))]))
    result = ingest_discovery(report, "Dry Fund", db_path=db_path,
                              extractor_name="bentham", dry_run=True)
    assert result["dry_run"] is True
    assert result["months"] == 3  # 提取成功数
    assert result["gate_pass"] is True
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)  # dry_run 不碰库不建表,测试自建空表验未入库
        assert get_monthly_returns(conn, "a4_fund") == []  # 未入库
        assert get_fund(conn, "a4_fund") is None  # 基金未创建
    finally:
        conn.close()


# --- generic verified 老基金 update 增量直通 ---


@pytest.mark.unit
def test_generic_verified_update_direct_ingest(monkeypatch, tmp_path):
    """老基金 extractor_verified=1 + generic + update 增量 normal -> 直通 monthly_returns
    (人工 verify_extractor 后,update 侧增量不再进 first_use pending,A4b)。"""
    db_path = str(tmp_path / "a4.db")
    conn = get_connection(db_path)
    ensure_tables(conn)
    _create_fund(conn, fund_id="a4_fund", fund_name="A4 Fund",
                 confirmed_url="https://example.com/archive",
                 fetch_method="pdf", url_type="archive_page",
                 inception_date="2025-02-28", extractor_verified=1)
    _upsert(conn, fund_id="a4_fund", date="2025-02-28", net_return=0.005, commentary_truth=0.005)
    _upsert(conn, fund_id="a4_fund", date="2025-03-31", net_return=0.006, commentary_truth=0.006)
    conn.close()

    archive_md = ("[Feb 2025](https://x/feb.pdf)\n[Mar 2025](https://x/mar.pdf)\n"
                  "[Apr 2025](https://x/apr.pdf)")
    monkeypatch.setattr("lib.ingest.download_and_extract_parallel",
                        _fake_parallel([("2025-02", _er(0.005)),
                                        ("2025-03", _er(0.006)),
                                        ("2025-04", _er(0.004))]))
    result = update_fund("a4_fund", db_path=db_path,
                         archive_markdown=archive_md, latest_month="2025-04",
                         extractor_name="generic")  # verified -> 直通
    assert result["updated"] is True
    assert result["new_months"] == 1  # 04 新月直通
    assert result["pending_review_count"] == 0
    conn = get_connection(db_path)
    try:
        rows = get_monthly_returns(conn, "a4_fund")
        assert [r["date"] for r in rows] == ["2025-02-28", "2025-03-31", "2025-04-30"]
    finally:
        conn.close()


@pytest.mark.unit
def test_generic_unverified_update_to_pending(monkeypatch, tmp_path):
    """老基金 extractor_verified=0(未 verify)+ generic + update 增量 -> 进 pending
    generic_first_use(verify 前所有增量都审,A4b 双向约束)。"""
    db_path = str(tmp_path / "a4.db")
    conn = get_connection(db_path)
    ensure_tables(conn)
    _create_fund(conn, fund_id="a4_fund", fund_name="A4 Fund",
                 confirmed_url="https://example.com/archive",
                 fetch_method="pdf", url_type="archive_page",
                 inception_date="2025-02-28", extractor_verified=0)
    _upsert(conn, fund_id="a4_fund", date="2025-02-28", net_return=0.005, commentary_truth=0.005)
    conn.close()

    archive_md = "[Feb 2025](https://x/feb.pdf)\n[Mar 2025](https://x/mar.pdf)"
    monkeypatch.setattr("lib.ingest.download_and_extract_parallel",
                        _fake_parallel([("2025-02", _er(0.005)),
                                        ("2025-03", _er(0.006))]))
    result = update_fund("a4_fund", db_path=db_path,
                         archive_markdown=archive_md, latest_month="2025-03",
                         extractor_name="generic")
    assert result["new_months"] == 0  # 03 进 pending 不入库
    assert result["pending_review_count"] == 1


# --- verify_extractor 端到端(discover -> verify 链路) ---


@pytest.mark.unit
def test_verify_extractor_end_to_end(monkeypatch, tmp_path):
    """discover generic 新基金全进 pending -> verify_extractor -> 批量 promote + 标 1。
    验证 A4 完整闭环:首批 pending -> 人工审核 -> verify -> 入库 + 解锁增量直通。"""
    db_path = str(tmp_path / "a4.db")
    report = _disc_report(
        links=[("2025-02", "https://x/feb.pdf"), ("2025-03", "https://x/mar.pdf"),
               ("2025-04", "https://x/apr.pdf")],
    )
    monkeypatch.setattr("lib.ingest.download_and_extract_parallel",
                        _fake_parallel([("2025-02", _er(0.005)),
                                        ("2025-03", _er(0.006)),
                                        ("2025-04", _er(0.004))]))
    ingest_discovery(report, "A4 Fund", db_path=db_path)  # 全 pending
    conn = get_connection(db_path)
    try:
        assert get_extractor_verified(conn, "a4_fund") == 0
        assert get_monthly_returns(conn, "a4_fund") == []
        result = verify_extractor(conn, "a4_fund")
        assert result["promoted_count"] == 3
        assert get_extractor_verified(conn, "a4_fund") == 1
        rows = get_monthly_returns(conn, "a4_fund")
        assert len(rows) == 3
        assert list_pending_review(conn, "a4_fund", state="pending") == []
    finally:
        conn.close()
