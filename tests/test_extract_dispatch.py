"""extract.extract_from_source dispatch 单测.

覆盖 URL 后缀分派 (Spec D Phase D):
  1. .pdf → extract_from_pdf
  2. .html → extract_from_html
  3. .csv → extract_from_csv
  4. .htm 别名
  5. 查询串不影响后缀
  6. 未支持后缀 raise ValueError
  7. file:// url 直读本地
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest.extract import extract_from_source, Extraction


def _mock_extraction(ym="2026-05", not_found=False):
    return Extraction(
        ym=ym, net_return=None if not_found else 0.0065,
        source_quote="mock", measure="net_monthly",
        measure_label_in_pdf="mock", rolling={},
        not_found=not_found, raw={},
    )


# --------------------- 1. PDF → extract_from_pdf ---------------------

def test_pdf_dispatches_to_extract_from_pdf(tmp_path):
    pdf_p = tmp_path / "2026-05.pdf"
    pdf_p.write_bytes(b"%PDF-1.4\n%dummy\n")
    with patch("llm_ingest.extract.extract_from_pdf") as m:
        m.return_value = _mock_extraction()
        result = extract_from_source(
            "https://example.com/2026-05.pdf", "2026-05",
            local_pdf_path=pdf_p,
        )
        m.assert_called_once()
        args, kw = m.call_args
        # 第一位参数是 pdf_path
        assert args[0] == pdf_p
        assert args[1] == "2026-05"
    assert result.net_return == 0.0065


# --------------------- 2. HTML → extract_from_html ---------------------

def test_html_dispatches_to_extract_from_html():
    with patch("llm_ingest.extract_html.extract_from_html") as m:
        m.return_value = _mock_extraction()
        result = extract_from_source(
            "https://example.com/report.html", "2026-05",
            html_text="<p>May 2026 0.65%</p>",
        )
        m.assert_called_once()
        args, kw = m.call_args
        assert "May 2026" in args[0]
    assert result.net_return == 0.0065


# --------------------- 3. CSV → extract_from_csv ---------------------

def test_csv_dispatches_to_extract_from_csv():
    with patch("llm_ingest.extract_csv.extract_from_csv") as m:
        m.return_value = _mock_extraction()
        result = extract_from_source(
            "https://example.com/data.csv", "2026-05",
            csv_text="date,x\n2026-05,0.65\n",
        )
        m.assert_called_once()
    assert result.net_return == 0.0065


# --------------------- 4. .htm 别名 ---------------------

def test_htm_alias_dispatches_to_html():
    with patch("llm_ingest.extract_html.extract_from_html") as m:
        m.return_value = _mock_extraction()
        extract_from_source(
            "https://example.com/report.htm", "2026-05",
            html_text="<p>ok</p>",
        )
        m.assert_called_once()


# --------------------- 5. 查询串不影响后缀识别 ---------------------

def test_query_string_ignored_for_ext():
    with patch("llm_ingest.extract_csv.extract_from_csv") as m:
        m.return_value = _mock_extraction()
        extract_from_source(
            "https://example.com/data.csv?ts=1234&tok=abc", "2026-05",
            csv_text="date,x\n2026-05,0.65\n",
        )
        m.assert_called_once()


# --------------------- 6. 未支持后缀 → ValueError ---------------------

def test_unsupported_ext_raises():
    with pytest.raises(ValueError, match="未支持后缀"):
        extract_from_source(
            "https://example.com/data.xlsx", "2026-05",
        )


def test_pdf_missing_local_path_raises():
    with pytest.raises(ValueError, match="PDF 通道需要 local_pdf_path"):
        extract_from_source("https://example.com/x.pdf", "2026-05")


def test_html_missing_text_and_not_local_raises():
    with pytest.raises(ValueError, match="HTML 通道需要 html_text"):
        extract_from_source("https://example.com/x.html", "2026-05")


def test_csv_missing_text_and_not_local_raises():
    with pytest.raises(ValueError, match="CSV 通道需要 csv_text"):
        extract_from_source("https://example.com/x.csv", "2026-05")


# --------------------- 7. file:// URL 直读本地 ---------------------

def test_file_url_html_reads_local(tmp_path):
    html_file = tmp_path / "report.html"
    html_file.write_text("<p>May 2026 0.65%</p>", encoding="utf-8")
    with patch("llm_ingest.extract_html.extract_from_html") as m:
        m.return_value = _mock_extraction()
        extract_from_source(
            f"file://{html_file}", "2026-05",  # 不显式传 html_text
        )
        m.assert_called_once()
        args, kw = m.call_args
        assert "May 2026" in args[0]


def test_file_url_csv_reads_local(tmp_path):
    csv_file = tmp_path / "d.csv"
    csv_file.write_text("date,x\n2026-05,0.65\n", encoding="utf-8")
    with patch("llm_ingest.extract_csv.extract_from_csv") as m:
        m.return_value = _mock_extraction()
        extract_from_source(f"file://{csv_file}", "2026-05")
        m.assert_called_once()
        args, kw = m.call_args
        assert "2026-05,0.65" in args[0]
