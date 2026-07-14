"""lib/ingest.py 全自动流水线测试。

mock download_and_extract_parallel（不触网），用 db_path 指向 tmp_path 临时 DB
（add_fund 自管理 conn 并 close，测试用新 conn 验证）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.ingest import add_fund, add_fund_from_html_table
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

    def fake_parallel(links, dest_dir, max_workers=None, extractor=None):
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

    def fake_parallel(links, dest_dir, max_workers=None, extractor=None):
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

    def fake_parallel(links, dest_dir, max_workers=None, extractor=None):
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


# --- add_fund_from_html_table（HTML 表格源流水线）---

# 连续 18 月（2025 全年 + 2026 Jan-Jun），无缺口，YTD 复利可通过 gate。
_FUNDMONITORS_CONTINUOUS_MD = """\
# Smarter Money Long Short Credit Fund (LSCF)

Historical Performance  (all figures shown here are net of fees unless otherwise stated)

| Year | Jan % | Feb % | Mar % | Apr % | May % | Jun % | Jul % | Aug % | Sep % | Oct % | Nov % | Dec % | YTD % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **2026** | 0.83 | 0.07 | -0.19 | 0.99 | 0.56 | 0.61 | N/R | N/R | N/R | N/R | N/R | N/R | 2.90 |
| **2025** | 0.61 | 0.62 | -0.15 | -0.62 | 1.66 | 0.66 | 1.02 | 0.64 | 0.69 | 0.37 | 0.14 | 0.56 | 6.36 |

Historical Financial Year Performance  (all figures shown here are are percentage per month net of fees unless otherwise stated)

| Year | Jul % | Aug % | Sep % | Oct % | Nov % | Dec % | Jan % | Feb % | Mar % | Apr % | May % | Jun % | FYTD % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **2025/2026** | 1.02 | 0.64 | 0.69 | 0.37 | 0.14 | 0.56 | 0.83 | 0.07 | -0.19 | 0.99 | 0.56 | 0.61 | 6.47 |
"""


@pytest.mark.unit
def test_add_fund_from_html_table_success(tmp_path):
    """HTML 表格源流水线成功：2025+2026 部分 18 月，YTD 复利通过，入库。"""
    db_path = str(tmp_path / "test.db")
    table_md = tmp_path / "profile.md"
    table_md.write_text(_FUNDMONITORS_CONTINUOUS_MD)

    result = add_fund_from_html_table(
        "test_html_fund", "Test HTML Fund", str(table_md),
        confirmed_url="https://fundmonitors.com/fund-profile.php?FundID=X",
        apir="SLT2562AU", verified_at="2026-07-13", db_path=db_path,
    )
    assert result["gate_pass"] is True
    assert result["months"] == 18
    assert result["start"] == "2025-01-31"
    assert result["end"] == "2026-06-30"
    assert result["short_history_warning"] is True  # 18 < 36

    conn = get_connection(db_path)
    try:
        rows = get_monthly_returns(conn, "test_html_fund")
        assert len(rows) == 18
        # NAV 复利：起点 1.0 * (1+首月收益)
        assert rows[0]["nav"] == pytest.approx(1.0 * (1 + 0.0061))
        # commentary_truth == net_return（表格源无独立 commentary）
        assert rows[0]["commentary_truth"] == rows[0]["net_return"]
        # 末月 NAV = 全 18 月复利
        expected_last = 1.0
        for r in rows:
            expected_last *= (1 + r["net_return"])
        assert rows[-1]["nav"] == pytest.approx(expected_last)
        # fund 记录字段
        fund = get_fund(conn, "test_html_fund")
        assert fund["fetch_method"] == "html"
        assert fund["url_type"] == "fact_sheet_profile"
        assert fund["apir_code"] == "SLT2562AU"
        assert fund["verified_at"] == "2026-07-13"
    finally:
        conn.close()


@pytest.mark.unit
def test_add_fund_from_html_table_no_table(tmp_path):
    """无 Historical Performance 表 -> gate_pass=False，不入库。"""
    db_path = str(tmp_path / "test.db")
    table_md = tmp_path / "profile.md"
    table_md.write_text("# Some fund\nno table here")

    result = add_fund_from_html_table(
        "test_html_fund2", "Test HTML Fund 2", str(table_md),
        confirmed_url="https://example.com/x", db_path=db_path,
    )
    assert result["gate_pass"] is False
    assert result["months"] == 0
    assert "表格无有效月度数据" in result["errors"]

    conn = get_connection(db_path)
    try:
        ensure_tables(conn)
        assert get_fund(conn, "test_html_fund2") is None
    finally:
        conn.close()


@pytest.mark.unit
def test_add_fund_from_html_table_gap_fail(tmp_path):
    """有缺口（删 2025 某月）-> gate_pass=False，不入库。"""
    db_path = str(tmp_path / "test.db")
    # 构造带缺口的 markdown：2025 缺 Jun（用空字符串制造缺口）
    md = _FUNDMONITORS_CONTINUOUS_MD.replace(
        "| **2025** | 0.61 | 0.62 | -0.15 | -0.62 | 1.66 | 0.66 | 1.02",
        "| **2025** | 0.61 | 0.62 | -0.15 | -0.62 | 1.66 | N/R | 1.02",
    )
    table_md = tmp_path / "profile.md"
    table_md.write_text(md)

    result = add_fund_from_html_table(
        "test_html_fund3", "Test HTML Fund 3", str(table_md),
        confirmed_url="https://example.com/x", db_path=db_path,
    )
    assert result["gate_pass"] is False
    assert any("缺口" in e for e in result["errors"])

    conn = get_connection(db_path)
    try:
        ensure_tables(conn)
        assert get_fund(conn, "test_html_fund3") is None
    finally:
        conn.close()


# --- add_fund_from_plotly_html（Plotly HTML + PDF rolling，Coolabah 模式）---


@pytest.mark.unit
def test_add_fund_from_plotly_html_happy_path(tmp_path, monkeypatch):
    """Plotly HTML + PDF rolling -> 正确入库（compound 一致）。"""
    from lib.ingest import add_fund_from_plotly_html

    # 用 fixture A 的 HTML（5 月 Assisted）
    fix = Path(__file__).parent / "fixtures"
    html_path = tmp_path / "report.html"
    html_path.write_text((fix / "frhy_assisted.html").read_text(), encoding="utf-8")

    # 构造 PDF text 使 extract_perf_rolling 与 NAV 复利一致
    # Assisted NAV: 100->102.08 over 5 mo -> inception = 0.0208
    # 3mo compound (last 3) ≈ 0.01551, rolling 3mo = 1.53% -> 0.0153 (误差 < 0.5%)
    # extract_perf_rolling 需要 "Class A" 标记 + % 符号
    pdf_text = (
        "Performance\n1 month 3 months 6 months 12 months since inception\n"
        "Class A 0.51% 1.53% 2.08% 2.08% 2.08%\n"
    )
    pdf_path = tmp_path / "rolling.pdf"
    pdf_path.write_text(pdf_text, encoding="utf-8")

    # parse_pdf_text 用 PyMuPDF 打开真实 PDF；测试用纯文本文件，mock 返回文本
    monkeypatch.setattr("lib.ingest.parse_pdf_text", lambda path: pdf_text)

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FUND_DB_WRITE_TOKEN", "test")
    result = add_fund_from_plotly_html(
        "test_plotly_assisted", "Test Plotly Assisted", str(html_path),
        confirmed_url="http://x", rolling_pdf_path=str(pdf_path),
        fund_name_pattern="Assisted",
        shareclass_prefix="test_plotly_", db_path=str(db_path),
    )
    assert result["gate_pass"], result["errors"]
    assert result["months"] == 4  # 5 NAV -> 4 monthly returns


@pytest.mark.unit
def test_add_fund_from_plotly_html_compound_mismatch_blocks(tmp_path, monkeypatch):
    """Plotly NAV 与 rolling 不一致 -> consistency block，不入库。"""
    from lib.ingest import add_fund_from_plotly_html

    fix = Path(__file__).parent / "fixtures"
    html_path = tmp_path / "report.html"
    html_path.write_text((fix / "frhy_assisted.html").read_text(), encoding="utf-8")

    # rolling inception 故意写 50.00%（与 NAV 复利 0.0208 严重不符）
    pdf_text = (
        "Performance\n1 month 3 months 6 months 12 months since inception\n"
        "Class A 0.51% 1.53% 2.08% 2.08% 50.00%\n"
    )
    pdf_path = tmp_path / "rolling.pdf"
    pdf_path.write_text(pdf_text, encoding="utf-8")

    monkeypatch.setattr("lib.ingest.parse_pdf_text", lambda path: pdf_text)

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("FUND_DB_WRITE_TOKEN", "test")
    result = add_fund_from_plotly_html(
        "test_plotly_bad", "Test Plotly Bad", str(html_path),
        confirmed_url="http://x", rolling_pdf_path=str(pdf_path),
        fund_name_pattern="Assisted",
        shareclass_prefix="test_plotly_", db_path=str(db_path),
    )
    assert not result["gate_pass"]
    assert any("复利" in e for e in result["errors"])
    # DB 未写入
    from lib.db import get_connection, ensure_tables
    conn = get_connection(str(db_path)); ensure_tables(conn)
    cnt = conn.execute(
        "SELECT COUNT(*) FROM monthly_returns WHERE fund_id=?",
        ("test_plotly_bad",),
    ).fetchone()[0]
    conn.close()
    assert cnt == 0
