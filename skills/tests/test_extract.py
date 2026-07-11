"""skills/lib/extract.py 纯函数单元测试。

不测网络；PDF 测试用 fitz 现场生成最小 PDF（无需外部文件）。
"""
from __future__ import annotations

import datetime
import os
import tempfile

import pytest

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
