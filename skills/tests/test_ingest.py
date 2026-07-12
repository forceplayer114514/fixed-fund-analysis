"""lib/ingest.py 全自动流水线测试。

mock download_and_extract_parallel（不触网），用 db_path 指向 tmp_path 临时 DB
（add_fund 自管理 conn 并 close，测试用新 conn 验证）。
"""
from __future__ import annotations

import pytest

from lib.ingest import add_fund
from lib.db import ensure_tables, get_connection, get_monthly_returns, get_fund


def _rolling(one_mo, three=None):
    """构造 extract_perf_rolling 风格的 rolling dict。"""
    return {"1mo": one_mo, "3mo": three, "6mo": None, "12mo": None,
            "inception": None, "parse_error": False}


@pytest.mark.unit
def test_add_fund_success(monkeypatch, tmp_path):
    """全自动流水线成功：3 月数据，复利验证通过，入库。"""
    db_path = str(tmp_path / "test.db")
    archive = tmp_path / "archive.md"
    archive.write_text(
        "March 2025: https://example.com/mar-2025.pdf\n"
        "April 2025: https://example.com/apr-2025.pdf\n"
        "May 2025: https://example.com/may-2025.pdf\n"
    )

    def fake_parallel(links, dest_dir, max_workers=None):
        return [
            ("2025-03", -0.0051, _rolling(-0.0051)),
            ("2025-04", 0.0068, _rolling(0.0068)),
            # 3mo 复利 = (1-0.0051)(1+0.0068)(1+0.0066)-1 ≈ 0.00827
            ("2025-05", 0.0066, _rolling(0.0066, three=0.0083)),
        ]

    monkeypatch.setattr("lib.ingest.download_and_extract_parallel", fake_parallel)

    result = add_fund(
        "test_fund", "Test Fund", str(archive),
        confirmed_url="https://example.com/archive",
        verified_at="2026-07-12", db_path=db_path,
    )
    assert result["gate_pass"] is True
    assert result["months"] == 3
    assert result["start"] == "2025-03-31"
    assert result["end"] == "2025-05-31"
    assert result["short_history_warning"] is True  # 3 < 36

    # add_fund 已 close 它的 conn，用新 conn 验证
    conn = get_connection(db_path)
    try:
        rows = get_monthly_returns(conn, "test_fund")
        assert len(rows) == 3
        assert rows[0]["net_return"] == -0.0051
        assert rows[0]["commentary_truth"] == -0.0051  # commentary_truth = net_return
        fund = get_fund(conn, "test_fund")
        assert fund["confirmed_url"] == "https://example.com/archive"
        assert fund["verified_at"] == "2026-07-12"
    finally:
        conn.close()


@pytest.mark.unit
def test_add_fund_gate_fail_not_ingested(monkeypatch, tmp_path):
    """gate 失败（缺口）不入库。"""
    db_path = str(tmp_path / "test2.db")
    archive = tmp_path / "archive.md"
    archive.write_text(
        "March 2025: https://example.com/mar-2025.pdf\n"
        "May 2025: https://example.com/may-2025.pdf\n"  # 缺 04
    )

    def fake_parallel(links, dest_dir, max_workers=None):
        return [
            ("2025-03", 0.005, _rolling(0.005)),
            ("2025-05", 0.005, _rolling(0.005)),
        ]

    monkeypatch.setattr("lib.ingest.download_and_extract_parallel", fake_parallel)

    result = add_fund(
        "test_fund2", "Test Fund 2", str(archive),
        confirmed_url="https://example.com/archive", db_path=db_path,
    )
    assert result["gate_pass"] is False
    assert any("缺口" in e for e in result["errors"])
    # 未入库（gate fail 时 add_fund 未建表，验证前 ensure_tables）
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)
        assert get_monthly_returns(conn, "test_fund2") == []
        assert get_fund(conn, "test_fund2") is None
    finally:
        conn.close()


@pytest.mark.unit
def test_add_fund_no_pdf_links(monkeypatch, tmp_path):
    """归档页无 PDF 链接 -> gate_fail，不入库。"""
    db_path = str(tmp_path / "test3.db")
    archive = tmp_path / "archive.md"
    archive.write_text("No links here.")

    monkeypatch.setattr(
        "lib.ingest.download_and_extract_parallel",
        lambda links, dest_dir, max_workers=None: [],
    )

    result = add_fund(
        "test_fund3", "Test Fund 3", str(archive),
        confirmed_url="https://example.com/archive", db_path=db_path,
    )
    assert result["gate_pass"] is False
    assert result["months"] == 0


@pytest.mark.unit
def test_add_fund_extraction_failure_isolation(monkeypatch, tmp_path):
    """单 PDF 提取失败（commentary=None）被排除，导致缺口 -> gate_fail。"""
    db_path = str(tmp_path / "test4.db")
    archive = tmp_path / "archive.md"
    archive.write_text(
        "March 2025: https://example.com/mar-2025.pdf\n"
        "April 2025: https://example.com/apr-2025.pdf\n"
        "May 2025: https://example.com/may-2025.pdf\n"
    )

    def fake_parallel(links, dest_dir, max_workers=None):
        return [
            ("2025-03", 0.005, _rolling(0.005)),
            ("2025-04", None, {"1mo": None, "3mo": None, "6mo": None,
                                "12mo": None, "inception": None, "parse_error": True}),
            ("2025-05", 0.005, _rolling(0.005)),
        ]

    monkeypatch.setattr("lib.ingest.download_and_extract_parallel", fake_parallel)

    result = add_fund(
        "test_fund4", "Test Fund 4", str(archive),
        confirmed_url="https://example.com/archive", db_path=db_path,
    )
    # 04 月提取失败被排除 -> records 只有 03,05 -> 缺口 04
    assert result["gate_pass"] is False
    assert "2025-04" in result.get("failed_months", [])
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)  # gate fail 时 add_fund 未建表
        assert get_fund(conn, "test_fund4") is None
    finally:
        conn.close()
