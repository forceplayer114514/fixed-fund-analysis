"""skills/lib/extract.py 纯函数单元测试。

不测网络；PDF 测试用 fitz 现场生成最小 PDF（无需外部文件）。
"""
from __future__ import annotations

import concurrent.futures
import datetime
import os
import tempfile

import pytest
import requests

from lib.extract import (
    MONTH_MAP,
    check_gaps,
    clean_spacing,
    download_file,
    extract_month_prefix,
    get_last_day_of_month,
    parse_date_string,
    parse_pdf_text,
)


# --- 1. MONTH_MAP ---
def test_month_map_complete():
    """月份名->数字映射完整，含全称与缩写。"""
    assert MONTH_MAP["january"] == 1
    assert MONTH_MAP["december"] == 12
    assert MONTH_MAP["jan"] == 1
    assert MONTH_MAP["sept"] == 9
    assert MONTH_MAP["sep"] == 9
    assert MONTH_MAP["mar"] == 3
    assert MONTH_MAP["nov"] == 11
    # 全部 12 个月全称都在
    for i, name in enumerate(
        ["january", "february", "march", "april", "may", "june",
         "july", "august", "september", "october", "november", "december"],
        start=1,
    ):
        assert MONTH_MAP[name] == i


# --- 2. clean_spacing ---
def test_clean_spacing():
    """压缩多余空白为单个空格（不 strip 首尾，与原实现一致）。"""
    assert clean_spacing("a   b") == "a b"
    assert clean_spacing("a\t\tb") == "a b"
    assert clean_spacing("a\n\nb") == "a b"
    # 原实现只把空白串压缩成单个空格，首尾的空白串同样压缩为单个空格，不 strip
    assert clean_spacing("  leading and trailing  ") == " leading and trailing "
    assert clean_spacing("nospace") == "nospace"


# --- 3. get_last_day_of_month ---
def test_get_last_day_of_month():
    """返回该月最后一天 date。"""
    assert get_last_day_of_month(2025, 1) == datetime.date(2025, 1, 31)
    assert get_last_day_of_month(2024, 2) == datetime.date(2024, 2, 29)  # 闰年
    assert get_last_day_of_month(2025, 2) == datetime.date(2025, 2, 28)  # 平年
    assert get_last_day_of_month(2025, 12) == datetime.date(2025, 12, 31)
    assert get_last_day_of_month(2025, 4) == datetime.date(2025, 4, 30)
    assert get_last_day_of_month(2025, 11) == datetime.date(2025, 11, 30)


# --- 4. extract_month_prefix ---
def test_extract_month_prefix():
    """从文件名提取 YYYY-MM，支持多种正则策略。"""
    assert extract_month_prefix("20250131-Report.pdf") == "2025-01"
    assert extract_month_prefix("Report-202502.pdf") == "2025-02"
    assert extract_month_prefix("April-2025.pdf") == "2025-04"
    # 月份名在前
    assert extract_month_prefix("Nov-2024-factsheet.pdf") == "2024-11"
    # 年在月份名前
    assert extract_month_prefix("2025-March-factsheet.pdf") == "2025-03"
    # 完整月份名
    assert extract_month_prefix("September-2025.pdf") == "2025-09"
    # 无法识别
    assert extract_month_prefix("random-file-no-date.pdf") is None


def test_extract_month_prefix_rejects_invalid_month():
    """策略1/2 误匹配非法月份(如 URL hash 262821=2628年21月)-> None,不提 2628-21。

    端到端 stake discover 发现:assets.contentstack.io URL 含 hash 262821 被策略1
    误提为 YYYYMMDD 2628-21(month=21 非法),_ym_to_month_end 拒入 failed_months。
    策略1/2 须校验 1<=month<=12。
    """
    assert extract_month_prefix("prefix-262821-suffix.pdf") is None
    assert extract_month_prefix("26282199.pdf") is None  # 2628年21月 非法


# --- 5. parse_date_string ---
def test_parse_date_string():
    """多种日期格式统一解析成月末 YYYY-MM-DD。"""
    assert parse_date_string("April 2025") == "2025-04-30"
    assert parse_date_string("Apr-2025") == "2025-04-30"
    assert parse_date_string("2025-04") == "2025-04-30"
    assert parse_date_string("2025-04-15") == "2025-04-30"
    assert parse_date_string("March 2025") == "2025-03-31"
    # 缩写 + 空格
    assert parse_date_string("Sept 2025") == "2025-09-30"
    assert parse_date_string("Dec 2024") == "2024-12-31"
    # 年在月份名前
    assert parse_date_string("2025-March") == "2025-03-31"
    # 斜杠分隔
    assert parse_date_string("2025/04/15") == "2025-04-30"
    # 闰年 2 月
    assert parse_date_string("February 2024") == "2024-02-29"
    # 无法解析返回 None
    assert parse_date_string("not a date") is None


# --- 6. check_gaps 无缺口 ---
def test_check_gaps_no_gap():
    """连续月份无缺口。"""
    assert check_gaps(["2025-01-31", "2025-02-28", "2025-03-31"]) == []
    assert check_gaps(["2025-12-31", "2026-01-31"]) == []


# --- 7. check_gaps 找到缺口 ---
def test_check_gaps_finds_missing():
    """检测到中间缺失月份。"""
    assert check_gaps(["2025-01-31", "2025-03-31"]) == ["2025-02"]
    assert check_gaps(["2025-01-31", "2025-02-28", "2025-04-30"]) == ["2025-03"]
    # 跨年缺口
    assert check_gaps(["2025-12-31", "2026-02-28"]) == ["2026-01"]
    # 多个月缺口
    assert check_gaps(["2025-01-31", "2025-05-31"]) == [
        "2025-02", "2025-03", "2025-04",
    ]


# --- 8. check_gaps 乱序 ---
def test_check_gaps_unordered():
    """乱序输入仍正确检测首尾间缺口。"""
    assert check_gaps(["2025-03-31", "2025-01-31"]) == ["2025-02"]
    assert check_gaps(
        ["2025-04-30", "2025-01-31", "2025-05-31", "2025-02-28"]
    ) == ["2025-03"]


# --- 9. check_gaps 单元素 / 空 ---
def test_check_gaps_single_element():
    """单元素或空列表无缺口。"""
    assert check_gaps(["2025-01-31"]) == []
    assert check_gaps([]) == []


# --- 10. parse_pdf_text（用 fitz 现场造 PDF）---
def test_parse_pdf_text_with_test_pdf():
    """用 fitz 创建最小 PDF，验证 parse_pdf_text 提取文本。"""
    try:
        import fitz  # noqa: F401
    except ImportError:
        pytest.skip("PyMuPDF not installed")

    fitz = pytest.importorskip("fitz")
    expected_text = "Hello World Monthly Report Test PDF"
    tmp_path = tempfile.mktemp(suffix=".pdf")
    txt_cache_path = os.path.splitext(tmp_path)[0] + ".txt"
    try:
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 72), expected_text, fontsize=12)
        doc.save(tmp_path)
        doc.close()

        result = parse_pdf_text(tmp_path)
        assert "Hello World" in result
        assert "Monthly Report" in result
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        if os.path.exists(txt_cache_path):
            os.remove(txt_cache_path)


def test_parse_pdf_text_max_pages(tmp_path):
    """max_pages 限制读取页数。"""
    fitz = pytest.importorskip("fitz")
    pdf_path = str(tmp_path / "multi.pdf")
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((50, 72), f"PAGE_{i}_MARKER", fontsize=12)
    doc.save(pdf_path)
    doc.close()

    # 只读第 1 页，不应包含第 2 页标记
    only_first = parse_pdf_text(pdf_path, max_pages=1)
    assert "PAGE_0_MARKER" in only_first
    assert "PAGE_1_MARKER" not in only_first

    # 读全部
    all_pages = parse_pdf_text(pdf_path)
    assert "PAGE_0_MARKER" in all_pages
    assert "PAGE_1_MARKER" in all_pages
    assert "PAGE_2_MARKER" in all_pages


def _make_test_pdf(path: str, text: str) -> None:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), text, fontsize=12)
    doc.save(path)
    doc.close()


def test_parse_pdf_text_writes_txt_cache(tmp_path):
    """提取后应在同目录落盘同名 .txt，首行为页数标记。"""
    pytest.importorskip("fitz")
    pdf_path = str(tmp_path / "2024-01.pdf")
    _make_test_pdf(pdf_path, "CACHE_MARKER_TEXT")

    result = parse_pdf_text(pdf_path)
    assert "CACHE_MARKER_TEXT" in result

    txt_path = str(tmp_path / "2024-01.txt")
    assert os.path.exists(txt_path)
    with open(txt_path, "r", encoding="utf-8") as f:
        header = f.readline().strip()
        cached_text = f.read()
    assert header == "#PAGES:ALL"
    assert cached_text == result


def test_parse_pdf_text_cache_hit_skips_reparse(tmp_path):
    """.txt 缓存 mtime >= pdf mtime 时，即使 pdf 内容已变也应返回缓存旧文本。"""
    pytest.importorskip("fitz")
    pdf_path = str(tmp_path / "2024-02.pdf")
    _make_test_pdf(pdf_path, "VERSION_ONE_MARKER")

    first = parse_pdf_text(pdf_path)
    assert "VERSION_ONE_MARKER" in first
    original_pdf_mtime = os.path.getmtime(pdf_path)

    # 覆盖 pdf 内容为新版本，但把 mtime 强制拨回原值（模拟"未变化"场景）
    _make_test_pdf(pdf_path, "VERSION_TWO_MARKER")
    os.utime(pdf_path, (original_pdf_mtime, original_pdf_mtime))

    second = parse_pdf_text(pdf_path)
    assert "VERSION_ONE_MARKER" in second
    assert "VERSION_TWO_MARKER" not in second


def test_parse_pdf_text_mtime_invalidates_cache(tmp_path):
    """pdf mtime 晚于 .txt 缓存时应重新解析，返回新内容。"""
    pytest.importorskip("fitz")
    pdf_path = str(tmp_path / "2024-03.pdf")
    _make_test_pdf(pdf_path, "VERSION_ONE_MARKER")

    first = parse_pdf_text(pdf_path)
    assert "VERSION_ONE_MARKER" in first

    # 覆盖 pdf 内容为新版本，mtime 拨到明显更晚 -> 缓存应失效
    _make_test_pdf(pdf_path, "VERSION_TWO_MARKER")
    future = os.path.getmtime(pdf_path) + 100
    os.utime(pdf_path, (future, future))

    second = parse_pdf_text(pdf_path)
    assert "VERSION_TWO_MARKER" in second
    assert "VERSION_ONE_MARKER" not in second


from lib.extract import (
    extract_commentary_return,
    extract_perf_rolling,
    extract_pdf_one,
    extract_pdf_links_from_archive,
    extract_bentham_net_return,
    extract_bentham_rolling,
    extract_gci_rolling,
)


# --- 11. extract_commentary_return ---
def test_extract_commentary_return_positive():
    """正数（省略正号）。"""
    text = "The Fund returned 0.53% in April 2025."
    assert extract_commentary_return(text) == 0.0053


def test_extract_commentary_return_negative():
    """负数（捕获符号）。"""
    text = "The Fund returned -0.26% in March 2026."
    assert extract_commentary_return(text) == -0.0026


def test_extract_commentary_return_with_plus():
    """显式正号。"""
    text = "returned +1.05% for the month"
    assert extract_commentary_return(text) == 0.0105


def test_extract_commentary_return_no_match():
    """无匹配返回 None。"""
    assert extract_commentary_return("No percentage here.") is None


def test_extract_commentary_return_first_match():
    """取第一个 returned 匹配（当月声明，非后续滚动收益）。"""
    text = "returned 0.53% in April. Over 3 months returned 1.50%."
    assert extract_commentary_return(text) == 0.0053


# --- 12. extract_perf_rolling ---
def test_extract_perf_rolling_normal():
    """正常 5 列 5 值。"""
    text = (
        "Performance (after fees) 1 month 3 months 6 months "
        "12 months Since inception\n"
        "Class A 0.53% 1.50% 3.00% 5.89% 6.91%"
    )
    r = extract_perf_rolling(text)
    assert r["1mo"] == 0.0053
    assert r["3mo"] == 0.0150
    assert r["6mo"] == 0.0300
    assert r["12mo"] == 0.0589
    assert r["inception"] == 0.0691
    assert r["parse_error"] is False


def test_extract_perf_rolling_with_dash():
    """含 '-' 空列（基金未满 12 个月）。"""
    text = (
        "Performance 1 month 3 months 6 months 12 months Since inception\n"
        "Class A 0.53% 1.50% - - 2.00%"
    )
    r = extract_perf_rolling(text)
    assert r["1mo"] == 0.0053
    assert r["3mo"] == 0.0150
    assert r["6mo"] is None
    assert r["12mo"] is None
    assert r["inception"] == 0.0200
    assert r["parse_error"] is False


def test_extract_perf_rolling_misaligned():
    """Nov 2025 特例：4 值 5 列（12mo=inception 合并）-> parse_error 但部分填充。"""
    text = (
        "Performance 1 month 3 months 6 months 12 months Since inception\n"
        "Class A 0.42% 1.20% 2.50% 5.89%"
    )
    r = extract_perf_rolling(text)
    assert r["parse_error"] is True
    assert r["1mo"] == 0.0042
    assert r["12mo"] == 0.0589
    assert r["inception"] is None


def test_extract_perf_rolling_no_table():
    """无 performance 表 -> parse_error。"""
    r = extract_perf_rolling("No table here.")
    assert r["parse_error"] is True


def test_extract_perf_rolling_negative_value():
    """滚动收益含负值。"""
    text = (
        "Performance 1 month 3 months 6 months 12 months Since inception\n"
        "Class A -0.26% -0.50% 1.00% 2.00% 3.00%"
    )
    r = extract_perf_rolling(text)
    assert r["1mo"] == -0.0026
    assert r["3mo"] == -0.0050
    assert r["parse_error"] is False


# --- 12b. extract_gci_rolling (GCI 表标签行逐行数值) ---
def test_extract_gci_rolling_new_format_nta_label():
    """2023-02 起新格式 'NTA Net Return (%)' 6 列(1/3/6/1Yr/3Yr Ann/Incep Ann)。"""
    text = (
        "Fund Performance\n1 Mth\n3 Mth\n6 Mth\n1 Yr\n3 Yr (Ann)\nIncep (Ann)\n"
        "NTA Net Return (%)\n0.71\n2.03\n3.79\n5.65\n5.11\n5.07\n"
        "Distribution (¢/unit)\n1.17\n"
    )
    r = extract_gci_rolling(text)
    assert r["parse_error"] is False
    assert r["1mo"] == pytest.approx(0.0071)
    assert r["3mo"] == pytest.approx(0.0203)
    assert r["6mo"] == pytest.approx(0.0379)
    assert r["12mo"] == pytest.approx(0.0565)
    assert r["inception"] == pytest.approx(0.0507)


def test_extract_gci_rolling_old_format_no_nta_prefix():
    """2023-02 前旧格式 'Net Return (%)(无 NTA 前缀)' 6 列,同样解析。"""
    text = (
        "Fund Performance\n1 Mth\n3 Mth\n6 Mth\n1 Yr\n3 Yr (Ann)\nIncep (Ann)\n"
        "Net Return (%)\n0.48\n1.78\n3.16\n6.29\n5.17\n5.07\n"
        "RBA Cash Rate (%)\n0.01\n"
    )
    r = extract_gci_rolling(text)
    assert r["parse_error"] is False
    assert r["1mo"] == pytest.approx(0.0048)
    assert r["3mo"] == pytest.approx(0.0178)
    assert r["6mo"] == pytest.approx(0.0316)
    assert r["12mo"] == pytest.approx(0.0629)
    assert r["inception"] == pytest.approx(0.0507)


def test_extract_gci_rolling_old_format_dash_for_missing_1yr():
    """早期基金不足 1 年,1 Yr 列为 –(en-dash):12mo=None,inception 仍取最后值。"""
    text = (
        "Fund Performance\n1 Mth\n3 Mth\n6 Mth\n1 Yr\nIncep (Ann)\n"
        "Net Return (%)\n0.42\n1.37\n2.68\n–\n4.57\n"
        "RBA Cash Rate (%)\n0.12\n"
    )
    r = extract_gci_rolling(text)
    assert r["parse_error"] is False
    assert r["1mo"] == pytest.approx(0.0042)
    assert r["3mo"] == pytest.approx(0.0137)
    assert r["6mo"] == pytest.approx(0.0268)
    assert r["12mo"] is None
    assert r["inception"] == pytest.approx(0.0457)


def test_extract_gci_rolling_excludes_excess_return_row():
    """'Net Excess Return (%)' 行(紧随 Net Return)不被误当主标签。"""
    text = (
        "1 Mth\n3 Mth\n6 Mth\n1 Yr\nIncep (Ann)\n"
        "Net Return (%)\n0.42\n1.37\n2.68\n–\n4.57\n"
        "Net Excess Return (%)\n0.30\n1.00\n1.92\n–\n3.03\n"
    )
    r = extract_gci_rolling(text)
    assert r["parse_error"] is False
    assert r["1mo"] == pytest.approx(0.0042)
    assert r["inception"] == pytest.approx(0.0457)


def test_extract_gci_rolling_footnote_label():
    """2022-08~12 月报标签带脚注 'Net Return2 (%)'。"""
    text = (
        "1 Mth\n3 Mth\n6 Mth\n1 Yr\n3 Yr (Ann)\nIncep (Ann)\n"
        "Net Return2 (%)\n0.60\n1.78\n3.16\n6.29\n5.17\n5.07\n"
        "RBA Cash Rate (%)\n0.01\n"
    )
    r = extract_gci_rolling(text)
    assert r["parse_error"] is False
    assert r["1mo"] == pytest.approx(0.0060)
    assert r["12mo"] == pytest.approx(0.0629)
    assert r["inception"] == pytest.approx(0.0507)


def test_extract_gci_rolling_parens_negative_1mo():
    """2020-03 COVID 砸盘月:1mo 为会计括号负数 '(0.45)' = -0.45%。"""
    text = (
        "1 Mth\n3 Mth\n6 Mth\n1 Yr\nIncep (Ann)\n"
        "Net Return (%)\n(0.45)\n0.27\n1.46\n4.35\n4.51\n"
        "RBA Cash Rate (%)\n0.04\n"
    )
    r = extract_gci_rolling(text)
    assert r["parse_error"] is False
    assert r["1mo"] == pytest.approx(-0.0045)
    assert r["3mo"] == pytest.approx(0.0027)
    assert r["6mo"] == pytest.approx(0.0146)
    assert r["12mo"] == pytest.approx(0.0435)
    assert r["inception"] == pytest.approx(0.0451)


# --- 13. extract_pdf_one（mock parse_pdf_text）---
def test_extract_pdf_one(monkeypatch):
    """组合：parse_pdf_text -> commentary + rolling。"""
    fake_text = (
        "The Fund returned 0.53% in April. "
        "Performance 1 month 3 months 6 months 12 months Since inception "
        "Class A 0.53% 1.50% 3.00% 5.89% 6.91%"
    )
    monkeypatch.setattr(
        "lib.extract.parse_pdf_text", lambda p, max_pages=None: fake_text
    )
    commentary, rolling = extract_pdf_one("/fake/path.pdf")
    assert commentary == 0.0053
    assert rolling["12mo"] == 0.0589
    assert rolling["parse_error"] is False


def test_extract_pdf_one_no_commentary(monkeypatch):
    """Commentary 缺失返回 None，rolling 仍解析。"""
    fake_text = (
        "Performance 1 month 3 months 6 months 12 months Since inception "
        "Class A 0.53% 1.50% 3.00% 5.89% 6.91%"
    )
    monkeypatch.setattr(
        "lib.extract.parse_pdf_text", lambda p, max_pages=None: fake_text
    )
    commentary, rolling = extract_pdf_one("/fake/path.pdf")
    assert commentary is None
    assert rolling["1mo"] == 0.0053


# --- 14. extract_pdf_links_from_archive ---
def test_extract_pdf_links_from_archive_markdown_links():
    """markdown 链接 [text](url.pdf)。"""
    md = (
        "# Monthly Performance Reports\n"
        "- April 2025: [Report](https://example.com/apr-2025.pdf)\n"
        "- March 2025: [Report](https://example.com/mar-2025.pdf)\n"
    )
    links = extract_pdf_links_from_archive(md)
    assert ("2025-04", "https://example.com/apr-2025.pdf") in links
    assert ("2025-03", "https://example.com/mar-2025.pdf") in links
    assert len(links) == 2


def test_extract_pdf_links_from_archive_bare_url():
    """裸 url.pdf。"""
    md = "May 2025 Report: https://example.com/may-2025.pdf"
    links = extract_pdf_links_from_archive(md)
    assert ("2025-05", "https://example.com/may-2025.pdf") in links


def test_extract_pdf_links_from_archive_empty():
    """空输入返回空列表。"""
    assert extract_pdf_links_from_archive("") == []


def test_extract_pdf_links_from_archive_dedup():
    """重复链接去重。"""
    md = (
        "April 2025: https://example.com/apr-2025.pdf\n"
        "April 2025 again: https://example.com/apr-2025.pdf"
    )
    links = extract_pdf_links_from_archive(md)
    assert len(links) == 1


def test_extract_pdf_links_from_archive_link_text_priority():
    """链接文本优先于 URL 文件名（Stake 格式：文件名 2 位年份，文本 4 位年份）。

    端到端验收发现：URL 文件名 April25/Aug25 等 2 位年份不匹配 extract_month_prefix
    的 4 位年份正则，且 URL hash 可能误匹配。链接文本 "April 2025" 更可靠。
    """
    md = (
        "* [April 2025](https://assets.contentstack.io/AccumulateMonthly_April25.pdf)\n"
        "* [Aug 2025](https://assets.contentstack.io/Accumulate_Report_Aug25.pdf)\n"
        "* [Sept 2025](https://assets.contentstack.io/blt7e585600966a7a12/AccumulateReport_Sept_2025.pdf)\n"
    )
    links = extract_pdf_links_from_archive(md)
    assert len(links) == 3
    yms = [ym for ym, _ in links]
    assert "2025-04" in yms  # 从链接文本提，非 URL 的 April25
    assert "2025-08" in yms
    assert "2025-09" in yms  # URL 含 hash blt7e585... 不应误匹配


def test_extract_pdf_links_from_archive_link_text_no_month_fallback_url():
    """链接文本无月份（如 'Report'）时回退到 URL 提取。"""
    md = "- [Report](https://example.com/apr-2025.pdf)"
    links = extract_pdf_links_from_archive(md)
    assert ("2025-04", "https://example.com/apr-2025.pdf") in links


from lib.extract import (
    download_and_extract_parallel,
    verify_monthly_vs_rolling,
    gate_check,
)


# --- 15. verify_monthly_vs_rolling ---
def test_verify_monthly_vs_rolling_pass():
    """3 月窗口复利验证通过（Stake 2026-03~05 真实数据）。"""
    # 复利 = (1-0.0026)(1+0.0061)(1+0.0105)-1 ≈ 0.01400
    monthly = [
        ("2026-03-31", -0.0026),
        ("2026-04-30", 0.0061),
        ("2026-05-31", 0.0105),
    ]
    rolling = {
        "1mo": 0.0105, "3mo": 0.0140, "6mo": None,
        "12mo": None, "inception": None, "parse_error": False,
    }
    r = verify_monthly_vs_rolling(monthly, rolling)
    assert r["3mo"]["pass"] is True
    assert r["pass"] is True


def test_verify_monthly_vs_rolling_fail():
    """构造错误序列：复利不匹配 rolling。"""
    monthly = [
        ("2026-03-31", 0.05),
        ("2026-04-30", 0.05),
        ("2026-05-31", 0.05),
    ]
    rolling = {
        "1mo": 0.05, "3mo": 0.01, "6mo": None,
        "12mo": None, "inception": None, "parse_error": False,
    }
    r = verify_monthly_vs_rolling(monthly, rolling)
    # 复利 0.1576 != rolling 0.01
    assert r["3mo"]["pass"] is False
    assert r["pass"] is False


def test_verify_monthly_vs_rolling_skip_missing_window():
    """monthly 不足 N 月或 rolling 缺列 -> 跳过该窗口。"""
    monthly = [("2026-05-31", 0.0105)]
    rolling = {
        "1mo": 0.0105, "3mo": 0.0140, "6mo": None,
        "12mo": None, "inception": None, "parse_error": False,
    }
    r = verify_monthly_vs_rolling(monthly, rolling)
    # 3mo 窗口因 monthly 不足跳过，pass=False（无窗口通过）
    assert r["3mo"]["pass"] is False
    assert r["pass"] is False


# --- 16. gate_check ---
def test_gate_check_pass():
    """完整通过流程：无缺口、无捏造、复利通过、字段正常。"""
    records = [
        ("2025-01-31", 0.005),
        ("2025-02-28", 0.006),
        ("2025-03-31", 0.004),
    ]
    # 最近 3 月复利 = 1.005*1.006*1.004-1 ≈ 0.01503，rolling 3mo=0.015 误差<0.5%
    rolling = {
        "1mo": 0.004, "3mo": 0.015, "6mo": None,
        "12mo": None, "inception": None, "parse_error": False,
    }
    pass_ok, errors = gate_check(records, {"2025-03": rolling})
    assert pass_ok is True
    assert errors == []


def test_gate_check_gap_fail():
    """缺口失败。"""
    records = [
        ("2025-01-31", 0.005),
        ("2025-03-31", 0.004),
    ]
    pass_ok, errors = gate_check(records, {})
    assert pass_ok is False
    assert any("缺口" in e for e in errors)


def test_gate_check_fabrication_fail():
    """连续 3 月相同非零值（捏造迹象，参考 213bdd）失败。"""
    records = [
        ("2025-01-31", 0.00657),
        ("2025-02-28", 0.00657),
        ("2025-03-31", 0.00657),
        ("2025-04-30", 0.005),
    ]
    pass_ok, errors = gate_check(records, {})
    assert pass_ok is False
    assert any("ANTI-FABRICATION" in e for e in errors)


def test_gate_check_field_range_fail():
    """字段异常：单月 |r| >= 0.5（50%）失败。"""
    records = [
        ("2025-01-31", 0.005),
        ("2025-02-28", 0.6),  # 60%，异常
        ("2025-03-31", 0.004),
    ]
    pass_ok, errors = gate_check(records, {})
    assert pass_ok is False
    assert any("字段异常" in e for e in errors)


def test_gate_check_rolling_parse_error_skipped():
    """rolling parse_error=True 时跳过复利验证（不因此 fail）。"""
    records = [
        ("2025-01-31", 0.005),
        ("2025-02-28", 0.006),
        ("2025-03-31", 0.004),
    ]
    rolling = {"1mo": None, "3mo": None, "6mo": None,
               "12mo": None, "inception": None, "parse_error": True}
    pass_ok, errors = gate_check(records, {"2025-03": rolling})
    # 缺口无、捏造无、字段正常、复利跳过 -> pass
    assert pass_ok is True


# --- 17. download_and_extract_parallel ---
def test_download_and_extract_parallel_success(monkeypatch, tmp_path):
    """并发下载+提取成功（mock download_file + extract_pdf_one）。"""
    calls = []

    def fake_download(url, filepath, headers=None):
        calls.append(url)
        with open(filepath, "wb") as f:
            f.write(b"fake pdf")

    def fake_extract(pdf_path, max_pages=None):
        return (0.0053, {"1mo": 0.0053, "3mo": None, "6mo": None,
                         "12mo": None, "inception": None, "parse_error": False})

    monkeypatch.setattr("lib.extract.download_file", fake_download)
    monkeypatch.setattr("lib.extract.extract_pdf_one", fake_extract)

    links = [
        ("2025-03", "https://example.com/mar.pdf"),
        ("2025-01", "https://example.com/jan.pdf"),
        ("2025-02", "https://example.com/feb.pdf"),
    ]
    results = download_and_extract_parallel(links, str(tmp_path), max_workers=3)
    # 按 ym 排序
    yms = [r[0] for r in results]
    assert yms == ["2025-01", "2025-02", "2025-03"]
    # 全部成功
    for ym, commentary, rolling in results:
        assert commentary == 0.0053
        assert rolling["parse_error"] is False
    # 3 个 url 都被下载
    assert len(calls) == 3


def test_download_and_extract_parallel_failure_isolation(monkeypatch, tmp_path):
    """单 PDF 下载失败不中断其他（失败隔离）。"""
    def fake_download(url, filepath, headers=None):
        if "fail" in url:
            raise ConnectionError("boom")
        with open(filepath, "wb") as f:
            f.write(b"ok")

    def fake_extract(pdf_path, max_pages=None):
        return (0.0053, {"1mo": 0.0053, "3mo": None, "6mo": None,
                         "12mo": None, "inception": None, "parse_error": False})

    monkeypatch.setattr("lib.extract.download_file", fake_download)
    monkeypatch.setattr("lib.extract.extract_pdf_one", fake_extract)

    links = [
        ("2025-01", "https://example.com/ok.pdf"),
        ("2025-02", "https://example.com/fail.pdf"),
        ("2025-03", "https://example.com/ok2.pdf"),
    ]
    results = download_and_extract_parallel(links, str(tmp_path), max_workers=3)
    by_ym = {r[0]: r for r in results}
    # 成功项
    assert by_ym["2025-01"][1] == 0.0053
    assert by_ym["2025-03"][1] == 0.0053
    # 失败项：commentary=None, parse_error=True
    assert by_ym["2025-02"][1] is None
    assert by_ym["2025-02"][2]["parse_error"] is True


def test_download_and_extract_parallel_default_max_workers(monkeypatch, tmp_path):
    """默认 max_workers 按 IO 并发算(cpu_count*2 封顶24)，不卡在物理核数。"""
    seen_workers = {}
    real_executor = concurrent.futures.ThreadPoolExecutor

    class _SpyExecutor(real_executor):
        def __init__(self, max_workers=None, *a, **kw):
            seen_workers["value"] = max_workers
            super().__init__(max_workers=max_workers, *a, **kw)

    monkeypatch.setattr("lib.extract.os.cpu_count", lambda: 10)
    monkeypatch.setattr(
        "lib.extract.concurrent.futures.ThreadPoolExecutor", _SpyExecutor
    )
    monkeypatch.setattr(
        "lib.extract.download_file",
        lambda url, filepath, headers=None: open(filepath, "wb").write(b"x"),
    )
    monkeypatch.setattr(
        "lib.extract.extract_pdf_one",
        lambda pdf_path, max_pages=None: (
            0.01, {"1mo": 0.01, "3mo": None, "6mo": None,
                   "12mo": None, "inception": None, "parse_error": False},
        ),
    )
    download_and_extract_parallel(
        [("2025-01", "https://example.com/a.pdf")], str(tmp_path)
    )
    # cpu_count=10 -> min(24, 10*2) = 20，而非旧公式 min(16,10)=10
    assert seen_workers["value"] == 20


# --- 14b. download_file 重试 ---
def test_download_file_retries_once_on_timeout(monkeypatch, tmp_path):
    """超时/连接错误重试 1 次，第二次成功则正常写文件。"""
    calls = {"n": 0}

    class _FakeResp:
        def raise_for_status(self):
            pass

        content = b"pdf-bytes"

    def fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.Timeout("slow")
        return _FakeResp()

    monkeypatch.setattr("lib.extract.requests.get", fake_get)
    filepath = str(tmp_path / "f.pdf")
    download_file("https://example.com/f.pdf", filepath)
    assert calls["n"] == 2
    with open(filepath, "rb") as f:
        assert f.read() == b"pdf-bytes"


def test_download_file_raises_after_retry_exhausted(monkeypatch, tmp_path):
    """连续 2 次超时/连接错误直接抛出，不无限重试。"""
    def fake_get(url, headers=None, timeout=None):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr("lib.extract.requests.get", fake_get)
    filepath = str(tmp_path / "f.pdf")
    with pytest.raises(requests.exceptions.ConnectionError):
        download_file("https://example.com/f.pdf", filepath)


def test_download_file_http_error_not_retried(monkeypatch, tmp_path):
    """HTTP 4xx/5xx 状态由 raise_for_status 抛出，不属于重试范围，只请求 1 次。"""
    calls = {"n": 0}

    class _FakeResp:
        def raise_for_status(self):
            raise requests.exceptions.HTTPError("404")

    def fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        return _FakeResp()

    monkeypatch.setattr("lib.extract.requests.get", fake_get)
    filepath = str(tmp_path / "f.pdf")
    with pytest.raises(requests.exceptions.HTTPError):
        download_file("https://example.com/f.pdf", filepath)
    assert calls["n"] == 1


# --- 14. extract_bentham_net_return ---
def test_bentham_net_modern_percent_sign():
    """2022+ 版：% + after fees（无星号）。net -0.02% -> -0.0002。"""
    text = (
        "The Bentham Global Income Fund had a total return (after fees) "
        "of -0.02% in the month of September, underperforming the benchmark."
    )
    assert extract_bentham_net_return(text) == -0.0002


def test_bentham_net_legacy_percent_word():
    """2017 旧版：percent + after fees*（星号）。1.18 percent -> 0.0118。"""
    text = (
        "The Bentham Wholesale Global Income Fund had a total return "
        "(after fees*) of 1.18 percent in the month of January."
    )
    assert extract_bentham_net_return(text) == 0.0118


def test_bentham_net_not_gross():
    """关键：不取 gross before-fees。Commentary 同时给 net 与 gross（before fees），
    extract_commentary_return 的 returned\\s+X% 会取 gross，本提取器须取 net。"""
    text = (
        "had a total return (after fees) of -0.02% in the month of September. "
        "On a before fees basis the fund returned -0.01% for the month."
    )
    assert extract_bentham_net_return(text) == -0.0002


def test_bentham_net_no_match():
    """无匹配返回 None。"""
    assert extract_bentham_net_return("No Bentham commentary here.") is None


# --- 15. extract_bentham_rolling ---
def test_bentham_rolling_normal():
    """2022+ 表：Total return (after fees) 行 1mo/3mo/6mo/12mo 累计值。
    不提 inception（p.a. 与月度复利不可比）。"""
    text = (
        "Total return (after fees)1\n-0.02\n-0.40\n1.29\n2.17\n5.91\n"
        "4.79\n3.66\n4.53\n6.14\n5.99\n6.18\n"
        "Benchmark\n0.20\n0.66\n"
    )
    r = extract_bentham_rolling(text)
    assert r["parse_error"] is False
    assert r["1mo"] == -0.0002
    assert r["3mo"] == -0.004
    assert r["6mo"] == 0.0129
    assert r["12mo"] == 0.0217
    assert r["inception"] is None


def test_bentham_rolling_old_format_no_table():
    """2017 旧版无 performance 表 -> parse_error=True（gate 跳过复利，不致命）。"""
    text = "Monthly Distribution Returns History (%)\nFinancial Year\nJul\nAug\n"
    r = extract_bentham_rolling(text)
    assert r["parse_error"] is True
    assert r["1mo"] is None


def test_bentham_rolling_insufficient_tokens():
    """行标签后 token < 4 -> parse_error。"""
    text = "Total return (after fees)1\n-0.02\n-0.40\nBenchmark\n"
    r = extract_bentham_rolling(text)
    assert r["parse_error"] is True


# --- M3: ExtractedReturn + ArchiveLinks(unparseable 不静默消失)---
from lib.extract import (
    ExtractedReturn,
    extract_archive_links,
    extract_commentary_return_full,
    extract_bentham_net_return_full,
    extract_pdf_one_full,
)


def test_extract_commentary_return_full_has_quote():
    r = extract_commentary_return_full("The Fund returned 0.53% in April 2025.")
    assert isinstance(r, ExtractedReturn)
    assert r.value == 0.0053
    assert "returned 0.53%" in r.source_quote


def test_extract_commentary_return_full_no_match():
    assert extract_commentary_return_full("no pct here") is None


def test_extract_bentham_net_return_full_has_quote():
    text = "had a total return (after fees) of -0.02% in September."
    r = extract_bentham_net_return_full(text)
    assert isinstance(r, ExtractedReturn)
    assert r.value == -0.0002
    assert "after fees" in r.source_quote


def test_extract_pdf_one_full_returns_extracted_return(monkeypatch):
    fake_text = (
        "The Fund returned 0.53% in April. "
        "Performance 1 month 3 months 6 months 12 months Since inception "
        "Class A 0.53% 1.50% 3.00% 5.89% 6.91%"
    )
    monkeypatch.setattr(
        "lib.extract.parse_pdf_text", lambda p, max_pages=None: fake_text
    )
    commentary, rolling = extract_pdf_one_full("/fake/path.pdf")
    assert isinstance(commentary, ExtractedReturn)
    assert commentary.value == 0.0053
    assert "returned 0.53%" in commentary.source_quote
    assert rolling["12mo"] == 0.0589


def test_extract_archive_links_parsed_and_unparseable():
    """★重点2:解析不出月份的 PDF 链接进 unparseable,不静默消失。"""
    md = (
        "# Reports\n"
        "- April 2025: [Report](https://example.com/apr-2025.pdf)\n"
        "- Mystery: [Doc](https://example.com/no-date-here.pdf)\n"
    )
    al = extract_archive_links(md)
    assert ("2025-04", "https://example.com/apr-2025.pdf") in al.parsed
    assert len(al.unparseable) == 1
    assert al.unparseable[0]["url"] == "https://example.com/no-date-here.pdf"
    assert al.unparseable[0]["reason"] == "month_not_parsed"
    assert al.unparseable[0]["raw_text"] == "Doc"
    # parsed 不含 unparseable 的 url
    assert all("no-date-here" not in u for _, u in al.parsed)


# --- 18. KKR KKC Extractor ---
from lib.extract import (
    extract_kkc_net_return_full,
    extract_pdf_one_kkc_full,
)


def test_extract_kkc_net_return_full_patterns():
    # Pattern 1: Total Return (Net) +0.77%
    t1 = "Fund Performance Total Return (Net)\n+0.77%\n+0.25%"
    r1 = extract_kkc_net_return_full(t1)
    assert r1.value == 0.0077
    assert "Total Return (Net) +0.77%" in r1.source_quote

    # Pattern 1b: Total Returns (Net) -0.18%
    t1b = "Fund Performance Total Returns (Net)\n-0.18%\n-0.51%"
    r1b = extract_kkc_net_return_full(t1b)
    assert r1b.value == -0.0018
    assert "Total Returns (Net) -0.18%" in r1b.source_quote

    # Pattern 2: Net Return Based on NTA (%) -0.42%
    t2 = "Net Return Based on NTA (%)\n-0.42%\n-"
    r2 = extract_kkc_net_return_full(t2)
    assert r2.value == -0.0042
    assert "Net Return Based on NTA (%) -0.42%" in r2.source_quote

    # Pattern 3: returned 0.53%
    t3 = "The fund returned 0.53% for the month of January."
    r3 = extract_kkc_net_return_full(t3)
    assert r3.value == 0.0053
    assert "returned 0.53%" in r3.source_quote

    # No match
    assert extract_kkc_net_return_full("no return info here") is None


def test_extract_archive_links_unparseable_bare_url():
    """裸 url.pdf 解析不出月份也进 unparseable。"""
    md = "Report: https://example.com/undated-report.pdf"
    al = extract_archive_links(md)
    assert al.parsed == []
    assert len(al.unparseable) == 1
    assert al.unparseable[0]["raw_text"] == "https://example.com/undated-report.pdf"


def test_extract_archive_links_empty():
    al = extract_archive_links("")
    assert al.parsed == []
    assert al.unparseable == []


def test_extract_archive_links_dedup_parsed():
    """parsed 去重;月数=去重集合大小非链接数(修正3.2.6)。"""
    md = (
        "April 2025: https://example.com/apr-2025.pdf\n"
        "April 2025 again: https://example.com/apr-2025.pdf"
    )
    al = extract_archive_links(md)
    assert len(al.parsed) == 1
    assert len({ym for ym, _ in al.parsed}) == 1


def test_extract_archive_links_html_anchor():
    """HTML <a href> 归档页链接(curl 抓 HTML,非 markdown;3.1 工具隔离)。"""
    html = (
        '<a href="https://example.com/2025-04-report.pdf">April 2025 Report</a>\n'
        '<a href="https://example.com/undated.pdf">Mystery</a>'
    )
    al = extract_archive_links(html)
    assert ("2025-04", "https://example.com/2025-04-report.pdf") in al.parsed
    assert len(al.unparseable) == 1
    assert al.unparseable[0]["raw_text"] == "Mystery"
    assert al.unparseable[0]["url"] == "https://example.com/undated.pdf"


# --- Phase 0: PDF 持久缓存 + max_pages 透传 + 注册表等价性 ---

def test_pdf_cache_dir_env_and_default(tmp_path, monkeypatch):
    """FUND_PDF_CACHE 环境变量优先;缺省时用 <repo_root>/data/pdf_cache/。"""
    from lib.extract import pdf_cache_dir
    monkeypatch.setenv("FUND_PDF_CACHE", str(tmp_path))
    d = pdf_cache_dir("fund_x")
    assert d == os.path.join(str(tmp_path), "fund_x")
    assert os.path.isdir(d)
    # 覆盖 fund_id -> 子目录不同
    d2 = pdf_cache_dir("fund_y")
    assert d != d2


def test_download_file_cached_hit(tmp_path, monkeypatch):
    """已存在且非空的 filepath -> 跳过下载,返回 True。"""
    from lib.extract import download_file_cached
    filepath = str(tmp_path / "cache_hit.pdf")
    with open(filepath, "wb") as f:
        f.write(b"cached content")
    # 若真去下载会失败(URL 不通),命中则不触发 download_file。
    called = {"n": 0}

    def _boom(*a, **kw):
        called["n"] += 1
        raise AssertionError("download_file should not be called on cache hit")

    monkeypatch.setattr("lib.extract.download_file", _boom)
    hit = download_file_cached("https://example.com/x.pdf", filepath)
    assert hit is True
    assert called["n"] == 0


def test_download_file_cached_miss_calls_download(tmp_path, monkeypatch):
    """文件不存在 -> 调 download_file,返回 False。"""
    from lib.extract import download_file_cached
    filepath = str(tmp_path / "cache_miss.pdf")
    called = {"n": 0}

    def _fake(url, fp, headers=None):
        called["n"] += 1
        with open(fp, "wb") as f:
            f.write(b"new content")

    monkeypatch.setattr("lib.extract.download_file", _fake)
    hit = download_file_cached("https://example.com/y.pdf", filepath)
    assert hit is False
    assert called["n"] == 1
    assert os.path.getsize(filepath) > 0


def test_download_file_cached_empty_file_treated_as_miss(tmp_path, monkeypatch):
    """0 字节文件视为坏缓存 -> 重下载(避免命中空文件反复 parse_error)。"""
    from lib.extract import download_file_cached
    filepath = str(tmp_path / "empty.pdf")
    with open(filepath, "wb") as f:
        pass  # 0 字节
    called = {"n": 0}

    def _fake(url, fp, headers=None):
        called["n"] += 1
        with open(fp, "wb") as f:
            f.write(b"real content")

    monkeypatch.setattr("lib.extract.download_file", _fake)
    hit = download_file_cached("https://example.com/z.pdf", filepath)
    assert hit is False
    assert called["n"] == 1


def test_download_and_extract_parallel_cache_hit_no_download(monkeypatch, tmp_path):
    """dest_dir 已有 <ym>.pdf -> 跳过 download_file,只跑 extractor。"""
    # 预置缓存文件
    filepath = os.path.join(str(tmp_path), "2025-05.pdf")
    with open(filepath, "wb") as f:
        f.write(b"cached")

    calls = {"download": 0, "extract": 0}

    def fake_download(url, fp, headers=None):
        calls["download"] += 1
        with open(fp, "wb") as f:
            f.write(b"downloaded")

    def fake_extract(pdf_path, max_pages=None):
        calls["extract"] += 1
        return (0.01, {"1mo": 0.01, "3mo": None, "6mo": None,
                       "12mo": None, "inception": None, "parse_error": False})

    monkeypatch.setattr("lib.extract.download_file", fake_download)
    monkeypatch.setattr("lib.extract.extract_pdf_one", fake_extract)

    results = download_and_extract_parallel(
        [("2025-05", "https://example.com/may.pdf")], str(tmp_path),
        max_workers=1,
    )
    assert calls["download"] == 0
    assert calls["extract"] == 1
    assert results[0][1] == 0.01


def test_download_and_extract_parallel_bad_cache_redownloads(monkeypatch, tmp_path):
    """命中缓存但 extractor parse_error -> 删文件重下一次。第二次仍 parse_error
    则不再重下,保留 parse_error 状态。"""
    filepath = os.path.join(str(tmp_path), "2025-06.pdf")
    with open(filepath, "wb") as f:
        f.write(b"bad cached")

    n_download = {"n": 0}
    n_extract = {"n": 0}

    def fake_download(url, fp, headers=None):
        n_download["n"] += 1
        with open(fp, "wb") as f:
            f.write(b"redownloaded ok")

    def fake_extract(pdf_path, max_pages=None):
        n_extract["n"] += 1
        # 第一次 parse_error,第二次(重下载后)成功
        if n_extract["n"] == 1:
            return (None, {"1mo": None, "3mo": None, "6mo": None,
                           "12mo": None, "inception": None, "parse_error": True})
        return (0.02, {"1mo": 0.02, "3mo": None, "6mo": None,
                       "12mo": None, "inception": None, "parse_error": False})

    monkeypatch.setattr("lib.extract.download_file", fake_download)
    monkeypatch.setattr("lib.extract.extract_pdf_one", fake_extract)

    results = download_and_extract_parallel(
        [("2025-06", "https://example.com/jun.pdf")], str(tmp_path),
        max_workers=1,
    )
    assert n_download["n"] == 1  # 只重下载一次
    assert n_extract["n"] == 2  # 提取两次
    assert results[0][1] == 0.02
    assert results[0][2]["parse_error"] is False


def test_download_and_extract_parallel_max_pages_passthrough(monkeypatch, tmp_path):
    """max_pages 参数透传给 extractor(修复 funds.max_pdf_pages 存而不用)。"""
    seen = {"max_pages": None}

    def fake_download(url, fp, headers=None):
        with open(fp, "wb") as f:
            f.write(b"x")

    def fake_extract(pdf_path, max_pages=None):
        seen["max_pages"] = max_pages
        return (0.0, {"1mo": 0.0, "3mo": None, "6mo": None,
                      "12mo": None, "inception": None, "parse_error": False})

    monkeypatch.setattr("lib.extract.download_file", fake_download)
    monkeypatch.setattr("lib.extract.extract_pdf_one", fake_extract)

    download_and_extract_parallel(
        [("2025-07", "https://example.com/jul.pdf")], str(tmp_path),
        max_workers=1, max_pages=3,
    )
    assert seen["max_pages"] == 3


def test_download_and_extract_parallel_max_pages_none_omits_kwarg(monkeypatch, tmp_path):
    """max_pages=None 时不带该 kwarg 调 extractor(兼容不接受 max_pages 的 mock)。"""
    def fake_download(url, fp, headers=None):
        with open(fp, "wb") as f:
            f.write(b"x")

    # 故意不接受 max_pages 参数
    def strict_extract(pdf_path):
        return (0.0, {"1mo": 0.0, "3mo": None, "6mo": None,
                      "12mo": None, "inception": None, "parse_error": False})

    monkeypatch.setattr("lib.extract.download_file", fake_download)
    results = download_and_extract_parallel(
        [("2025-08", "https://example.com/aug.pdf")], str(tmp_path),
        max_workers=1, extractor=strict_extract,
    )
    # 未抛 TypeError,结果成功
    assert results[0][1] == 0.0


def test_extractor_registry_equivalence():
    """make_pdf_extractor 生成的 EXTRACTORS 行为与旧 4 个包装器完全等价:
    模块级 extract_pdf_one_X_full 名字仍为可调用,签名兼容,失败路径返 None+parse_error。"""
    from lib.extract import (
        EXTRACTORS,
        EXTRACTOR_SPECS,
        extract_pdf_one_full,
        extract_pdf_one_bentham_full,
        extract_pdf_one_kkc_full,
        extract_pdf_one_gci_full,
    )
    # 5 键,与 EXTRACTOR_SPECS 保持同键集(stake 复用 generic)
    assert set(EXTRACTORS.keys()) == {"generic", "stake", "bentham", "kkc", "gci"}
    assert set(EXTRACTOR_SPECS.keys()) == set(EXTRACTORS.keys())
    # generic 与 stake 应指同一 (return_fn, rolling_fn)
    assert EXTRACTOR_SPECS["generic"] == EXTRACTOR_SPECS["stake"]
    # 打不开的路径 -> (None, parse_error=True) — 所有生成器行为一致
    for fn in (extract_pdf_one_full, extract_pdf_one_bentham_full,
               extract_pdf_one_kkc_full, extract_pdf_one_gci_full):
        er, rolling = fn("/no/such/file.pdf")
        assert er is None
        assert rolling["parse_error"] is True
