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

    def fake_parallel(links, dest_dir, max_workers=None, extractor=None, return_full=False, max_pages=None):
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

    def fake_parallel(links, dest_dir, max_workers=None, extractor=None, return_full=False, max_pages=None):
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

    def fake_parallel(links, dest_dir, max_workers=None, extractor=None, return_full=False, max_pages=None):
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


# --- ingest_discovery(DiscoveryReport 入库分流,M4)---

from lib.strategies import DiscoveryReport
from lib.ingest import ingest_discovery
from lib.db import list_confirmed_gaps, list_pending_review
from lib.extract import ExtractedReturn

_PE = {"1mo": None, "3mo": None, "6mo": None, "12mo": None,
       "inception": None, "parse_error": True}


def _er(v, ambiguous=False):
    """构造 ExtractedReturn(测试用,source_quote 空)。"""
    return ExtractedReturn(value=v, source_quote="", ambiguous=ambiguous)


def _disc_report(fund_id="disc_fund", links=None, records=None, gaps=None,
                 inception_date="2025-02-28"):
    """构造 DiscoveryReport(per_level_payload L1 links 或 L3 records)。"""
    if links is not None:
        payload = {"fetch_method": "pdf", "confirmed_url": "https://example.com/archive",
                   "links": links}
        obtained = [ym for ym, _ in links]
        level = "L1"
    else:
        payload = {"fetch_method": "html", "confirmed_url": "https://fm.com/profile",
                   "records": records}
        obtained = [d[:7] for d, _ in records]
        level = "L3"
    return DiscoveryReport(
        fund_id=fund_id, inception_date=inception_date,
        obtained=obtained, gaps=gaps or [],
        per_level_contribution={level: len(obtained)},
        per_level_payload={level: payload},
    )


@pytest.mark.unit
def test_ingest_discovery_success(monkeypatch, tmp_path):
    """ingest_discovery 全流程:L1 links -> 下载提取 -> gate 通过 -> 入库。"""
    db_path = str(tmp_path / "test.db")
    report = _disc_report(
        links=[("2025-02", "https://x/feb.pdf"), ("2025-03", "https://x/mar.pdf"),
               ("2025-04", "https://x/apr.pdf")],
    )

    def fake_parallel(links, dest_dir, max_workers=None, extractor=None, return_full=False, max_pages=None):
        return [("2025-02", _er(0.005), _PE), ("2025-03", _er(0.006), _PE), ("2025-04", _er(0.004), _PE)]

    monkeypatch.setattr("lib.ingest.download_and_extract_parallel", fake_parallel)
    result = ingest_discovery(report, "Disc Fund", db_path=db_path, extractor_name="bentham")
    assert result["gate_pass"] is True
    assert result["months"] == 3
    conn = get_connection(db_path)
    try:
        rows = get_monthly_returns(conn, "disc_fund")
        assert len(rows) == 3
        fund = get_fund(conn, "disc_fund")
        assert fund["inception_date"] == "2025-02-28"
        assert fund["inception_assumed"] == 0
    finally:
        conn.close()


@pytest.mark.unit
def test_ingest_discovery_gaps_to_confirmed_gaps(monkeypatch, tmp_path):
    """缺口非失败:report.gaps 写 confirmed_gaps,obtained 入库(修正3.2.4)。"""
    db_path = str(tmp_path / "test.db")
    report = _disc_report(
        links=[("2025-02", "https://x/feb.pdf"), ("2025-04", "https://x/apr.pdf")],
        gaps=["2025-03"],
    )

    def fake_parallel(links, dest_dir, max_workers=None, extractor=None, return_full=False, max_pages=None):
        return [("2025-02", _er(0.005), _PE), ("2025-04", _er(0.004), _PE)]

    monkeypatch.setattr("lib.ingest.download_and_extract_parallel", fake_parallel)
    result = ingest_discovery(report, "Gap Fund", db_path=db_path, extractor_name="bentham")
    assert result["gate_pass"] is True  # 缺口非 fail
    assert result["months"] == 2
    assert result["gaps"] == ["2025-03"]
    conn = get_connection(db_path)
    try:
        assert len(get_monthly_returns(conn, "disc_fund")) == 2
        gaps = list_confirmed_gaps(conn, "disc_fund")
        assert [g["missing_month"] for g in gaps] == ["2025-03"]
    finally:
        conn.close()


@pytest.mark.unit
def test_ingest_discovery_over_threshold_to_pending(monkeypatch, tmp_path):
    """|r|>=0.5 超限月进 pending_review,不入 monthly_returns,不丢弃(§5/确认)。"""
    db_path = str(tmp_path / "test.db")
    report = _disc_report(
        links=[("2025-02", "https://x/feb.pdf"), ("2025-03", "https://x/mar.pdf"),
               ("2025-04", "https://x/apr.pdf")],
    )

    def fake_parallel(links, dest_dir, max_workers=None, extractor=None, return_full=False, max_pages=None):
        return [
            ("2025-02", _er(0.005), _PE),
            ("2025-03", _er(0.6), _PE),   # 60% 超限 -> pending
            ("2025-04", _er(0.004), _PE),
        ]

    monkeypatch.setattr("lib.ingest.download_and_extract_parallel", fake_parallel)
    result = ingest_discovery(report, "Ovr Fund", db_path=db_path, extractor_name="bentham")
    assert result["gate_pass"] is True  # 超限不 fail,进 pending
    assert result["months"] == 2  # 02,04 入库(03 进 pending)
    assert result["pending_review_count"] == 1
    conn = get_connection(db_path)
    try:
        rows = get_monthly_returns(conn, "disc_fund")
        assert [r["date"] for r in rows] == ["2025-02-28", "2025-04-30"]
        pending = list_pending_review(conn, "disc_fund")
        assert len(pending) == 1
        assert pending[0]["review_reason"] == "abs_return_exceeds_threshold"
        assert pending[0]["net_return"] == pytest.approx(0.6)
    finally:
        conn.close()


@pytest.mark.unit
def test_ingest_discovery_html_records_payload(tmp_path):
    """L3 records payload(HTML 表)直接入库,无需下载 PDF。"""
    db_path = str(tmp_path / "test.db")
    records = [("2025-01-31", 0.005), ("2025-02-28", 0.006), ("2025-03-31", 0.004)]
    report = _disc_report(records=records)
    result = ingest_discovery(report, "HTML Disc Fund", db_path=db_path, extractor_name="bentham")
    assert result["gate_pass"] is True
    assert result["months"] == 3
    conn = get_connection(db_path)
    try:
        assert len(get_monthly_returns(conn, "disc_fund")) == 3
    finally:
        conn.close()


@pytest.mark.unit
def test_ingest_discovery_fabrication_fail_not_ingested(monkeypatch, tmp_path):
    """ANTI-FABRICATION(连续3月相同非零值)-> 真 fail,不入库(不进 pending)。"""
    db_path = str(tmp_path / "test.db")
    report = _disc_report(
        links=[("2025-01", "https://x/jan.pdf"), ("2025-02", "https://x/feb.pdf"),
               ("2025-03", "https://x/mar.pdf"), ("2025-04", "https://x/apr.pdf")],
    )

    def fake_parallel(links, dest_dir, max_workers=None, extractor=None, return_full=False, max_pages=None):
        return [("2025-01", _er(0.00657), _PE), ("2025-02", _er(0.00657), _PE),
                ("2025-03", _er(0.00657), _PE), ("2025-04", _er(0.005), _PE)]

    monkeypatch.setattr("lib.ingest.download_and_extract_parallel", fake_parallel)
    result = ingest_discovery(report, "Fab Fund", db_path=db_path, extractor_name="bentham")
    assert result["gate_pass"] is False
    assert any("ANTI-FABRICATION" in e for e in result["errors"])
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)
        assert get_fund(conn, "disc_fund") is None
    finally:
        conn.close()


# --- update_fund(增量复查 + confirmed_gaps 复查 + 滞留报告,M6)---

from lib.ingest import update_fund
from lib.db import create_fund as _create_fund, upsert_monthly_return as _upsert


@pytest.mark.unit
def test_update_fund_new_months(monkeypatch, tmp_path):
    """update_fund 增量复查:现有 02,03 + 归档页含 04 -> 新月 04 入库。"""
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    ensure_tables(conn)
    _create_fund(conn, fund_id="upd_fund", fund_name="Upd Fund",
                 confirmed_url="https://example.com/archive",
                 fetch_method="pdf", url_type="archive_page",
                 inception_date="2025-02-28")
    _upsert(conn, fund_id="upd_fund", date="2025-02-28", net_return=0.005, commentary_truth=0.005)
    _upsert(conn, fund_id="upd_fund", date="2025-03-31", net_return=0.006, commentary_truth=0.006)
    conn.close()

    archive_md = ("[Feb 2025](https://x/feb.pdf)\n[Mar 2025](https://x/mar.pdf)\n"
                  "[Apr 2025](https://x/apr.pdf)")

    def fake_parallel(links, dest_dir, max_workers=None, extractor=None, return_full=False, max_pages=None):
        return [("2025-02", _er(0.005), _PE), ("2025-03", _er(0.006), _PE), ("2025-04", _er(0.004), _PE)]

    monkeypatch.setattr("lib.ingest.download_and_extract_parallel", fake_parallel)
    result = update_fund("upd_fund", db_path=db_path,
                         archive_markdown=archive_md, latest_month="2025-04",
                         extractor_name="bentham")
    assert result["updated"] is True
    assert result["new_months"] == 1  # 04 是新月(02,03 已有)
    assert "stale_pending_reviews" in result
    conn = get_connection(db_path)
    try:
        rows = get_monthly_returns(conn, "upd_fund")
        assert len(rows) == 3  # 02,03,04
        assert [r["date"] for r in rows] == ["2025-02-28", "2025-03-31", "2025-04-30"]
    finally:
        conn.close()


@pytest.mark.unit
def test_update_fund_unregistered(tmp_path):
    """未注册基金 -> updated=False。"""
    db_path = str(tmp_path / "test.db")
    result = update_fund("nope", db_path=db_path, archive_markdown="")
    assert result["updated"] is False
    assert any("未注册" in e for e in result["errors"])


@pytest.mark.unit
def test_update_fund_stale_pending_report(tmp_path, monkeypatch):
    """pending_review 滞留报告:update_fund 输出 >14 天 pending 条目(改3 落地)。"""
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    ensure_tables(conn)
    _create_fund(conn, fund_id="stale_fund", fund_name="Stale Fund",
                 confirmed_url="https://example.com/archive",
                 fetch_method="pdf", url_type="archive_page",
                 inception_date="2025-02-28")
    _upsert(conn, fund_id="stale_fund", date="2025-02-28", net_return=0.005, commentary_truth=0.005)
    # 注入一条滞留 >14 天的 pending_review
    from lib.db import add_pending_review
    add_pending_review(conn, fund_id="stale_fund", date="2025-03-31", net_return=0.6,
                       extract_method="code", review_reason="abs_return_exceeds_threshold")
    conn.execute("UPDATE pending_review SET created_at = datetime('now','-20 days')")
    conn.commit()
    conn.close()

    def fake_parallel(links, dest_dir, max_workers=None, extractor=None, return_full=False, max_pages=None):
        return [("2025-02", 0.005, _PE)]

    monkeypatch.setattr("lib.ingest.download_and_extract_parallel", fake_parallel)
    result = update_fund("stale_fund", db_path=db_path,
                         archive_markdown="Feb 2025: https://x/feb.pdf",
                         latest_month="2025-02")
    assert result["updated"] is True
    assert len(result["stale_pending_reviews"]) == 1
    assert result["stale_pending_reviews"][0]["date"] == "2025-03-31"
