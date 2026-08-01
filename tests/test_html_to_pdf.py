"""llm_ingest.html_to_pdf 单测.

覆盖 (docs/superpowers/plans/2026-07-20-html-rendered-pdf-channel.md 测试计划):
  1. _filter_hover_rows: 过滤 hoverinfo!=text / 短序列 trace, 清理 <br/>
  2. _build_appendix_html: 拼表格 + html.escape 防注入
  3. 无图表数据 (sections 为空) 时不注入附录, 仍正常打印
  4. playwright 不可用 -> HtmlToPdfError
  5. 输出文件为空/不存在 -> HtmlToPdfError
  6. 正常路径: goto/evaluate/pdf 调用顺序 + 注入附录 HTML 内容
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest.html_to_pdf import (
    HtmlToPdfError,
    _build_appendix_html,
    _filter_hover_rows,
    render_html_to_pdf,
)


# --------------------- _filter_hover_rows ---------------------

def test_filter_hover_rows_keeps_real_series_drops_short_and_non_text():
    plots = [{
        "plotId": "chart1",
        "traces": [
            {  # 真实序列: hoverinfo=text, 6 点
                "name": "FRHY Institutional",
                "hoverinfo": "text",
                "text": [
                    "FRHY<br />2022-12-31: $100.98",
                    "FRHY<br />2023-01-31: $101.50",
                    "FRHY<br />2023-02-28: $101.90",
                    "FRHY<br />2023-03-31: $102.10",
                    "FRHY<br />2023-04-30: $104.74",
                    "FRHY<br />2023-05-31: $105.00",
                ],
            },
            {  # 单点图例标记: 长度 <5, 应过滤
                "name": "legend marker",
                "hoverinfo": "text",
                "text": ["single point"],
            },
            {  # hoverinfo 不是 text (比如普通数值 hover), 应过滤
                "name": "axis trace",
                "hoverinfo": "x+y",
                "text": ["a", "b", "c", "d", "e", "f"],
            },
        ],
    }]
    sections = _filter_hover_rows(plots)
    assert len(sections) == 1
    assert sections[0]["plotId"] == "chart1"
    rows = sections[0]["rows"]
    assert len(rows) == 6
    assert rows[0]["series"] == "FRHY Institutional"
    # <br /> 清理成 " | ", 不是原样保留
    assert rows[0]["text"] == "FRHY | 2022-12-31: $100.98"
    assert "2023-04-30: $104.74" in rows[4]["text"]


def test_filter_hover_rows_no_qualifying_trace_returns_empty():
    plots = [{"plotId": "chart1", "traces": [
        {"name": "legend", "hoverinfo": "text", "text": ["x"]},
    ]}]
    assert _filter_hover_rows(plots) == []


def test_filter_hover_rows_empty_plots_returns_empty():
    assert _filter_hover_rows([]) == []


# --------------------- _build_appendix_html ---------------------

def test_build_appendix_html_contains_rows_and_escapes():
    sections = [{
        "plotId": "chart<1>",
        "rows": [{"series": "A & B", "text": "2026-06-30: $136.19"}],
    }]
    out = _build_appendix_html(sections)
    assert "chart&lt;1&gt;" in out
    assert "A &amp; B" in out
    assert "2026-06-30: $136.19" in out
    assert "<table" in out


def test_build_appendix_html_empty_sections_returns_empty_string():
    assert _build_appendix_html([]) == ""


# --------------------- render_html_to_pdf ---------------------

def _mock_playwright_chain(evaluate_side_effect, pdf_side_effect=None):
    """构造 sync_playwright() -> chromium.launch() -> new_page() 的 mock 链."""
    page = MagicMock()
    page.evaluate = MagicMock(side_effect=evaluate_side_effect)
    if pdf_side_effect is not None:
        page.pdf = MagicMock(side_effect=pdf_side_effect)
    else:
        page.pdf = MagicMock()
    browser = MagicMock()
    browser.new_page = MagicMock(return_value=page)
    p = MagicMock()
    p.chromium.launch = MagicMock(return_value=browser)
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=p)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx, page, browser


def test_paper_width_covers_content_width_instead_of_a4(tmp_path):
    """2026-08-01 回归: 固定 A4 打印会把超出可打印宽度的文字**横向裁掉**。

    A4 纵向可打印宽约 774px, Coolabah 报告页内容 1200px 宽, 于是每行都在同一列
    被切断 -- 实测抬头变成 'Fund: Coolabah Global Floating-Rate High Yield Co',
    基金名截断直接把身份闸判成兄弟基金 (identity_mismatch), 17 个月全卡在
    pending; 数字被截断 (6.45 -> 6.4) 更危险, 那是个看起来完全合法的错值。
    """
    out_path = tmp_path / "out.pdf"

    def fake_pdf(**kwargs):
        Path(kwargs["path"]).write_bytes(b"%PDF-1.4 fake pdf bytes")

    ctx, page, browser = _mock_playwright_chain(
        evaluate_side_effect=[[], {"w": 3200}], pdf_side_effect=fake_pdf)
    with patch("playwright.sync_api.sync_playwright", return_value=ctx):
        render_html_to_pdf("https://example.com/wide-report", out_path)

    kwargs = page.pdf.call_args.kwargs
    assert "format" not in kwargs, "固定纸张格式会裁掉超宽内容"
    assert kwargs["width"].endswith("px")
    assert int(kwargs["width"][:-2]) >= 3200, "纸张宽度必须盖住内容宽度"
    assert kwargs["height"].endswith("px"), "高度固定分页 (内容近 4 万像素高)"


def test_playwright_unavailable_raises_html_to_pdf_error(tmp_path):
    with patch.dict(sys.modules, {"playwright.sync_api": None}):
        with pytest.raises(HtmlToPdfError, match="playwright 不可用"):
            render_html_to_pdf("https://example.com/report", tmp_path / "out.pdf")


def test_empty_output_file_raises_html_to_pdf_error(tmp_path):
    out_path = tmp_path / "out.pdf"

    def fake_pdf(**kwargs):
        # 模拟渲染"成功返回"但文件为空/未生成 (out_path 不存在)
        pass

    ctx, page, browser = _mock_playwright_chain(
        evaluate_side_effect=[[], {"w": 1200}], pdf_side_effect=fake_pdf)
    with patch("playwright.sync_api.sync_playwright", return_value=ctx):
        with pytest.raises(HtmlToPdfError, match="空文件|渲染失败"):
            render_html_to_pdf("https://example.com/report", out_path)


def test_no_chart_data_skips_appendix_injection_but_still_prints(tmp_path):
    out_path = tmp_path / "out.pdf"

    def fake_pdf(**kwargs):
        Path(kwargs["path"]).write_bytes(b"%PDF-1.4 fake pdf bytes")

    # page.evaluate 第一次调用 (dump traces) 返回空列表 -> 无图表数据
    ctx, page, browser = _mock_playwright_chain(
        evaluate_side_effect=[[], {"w": 1200}], pdf_side_effect=fake_pdf)
    with patch("playwright.sync_api.sync_playwright", return_value=ctx):
        result = render_html_to_pdf("https://example.com/plain-report", out_path)

    assert result == out_path
    assert out_path.exists() and out_path.stat().st_size > 0
    # 无图表数据时 evaluate 只有 dump + 量内容宽度两次, 不该有注入附录那次
    assert page.evaluate.call_count == 2
    page.pdf.assert_called_once()


def test_chart_data_present_injects_appendix_then_prints(tmp_path):
    out_path = tmp_path / "out.pdf"

    def fake_pdf(**kwargs):
        Path(kwargs["path"]).write_bytes(b"%PDF-1.4 fake pdf bytes")

    plots = [{
        "plotId": "chart1",
        "traces": [{
            "name": "FRHY",
            "hoverinfo": "text",
            "text": [f"FRHY<br />2026-0{i}-30: $1{i}0.00" for i in range(1, 6)],
        }],
    }]
    ctx, page, browser = _mock_playwright_chain(
        evaluate_side_effect=[plots, None, {"w": 1200}], pdf_side_effect=fake_pdf,
    )
    with patch("playwright.sync_api.sync_playwright", return_value=ctx):
        result = render_html_to_pdf("https://example.com/plotly-report", out_path)

    assert result == out_path
    # 有图表数据: evaluate 调用 3 次 (dump + 注入 + 量内容宽度), 第二次带
    # appendix html 参数
    assert page.evaluate.call_count == 3
    inject_call_args = page.evaluate.call_args_list[1]
    injected_html = inject_call_args[0][1]
    assert "FRHY" in injected_html
    assert "2026-01-30" in injected_html
    page.goto.assert_called_once()
    assert page.goto.call_args.kwargs["wait_until"] == "networkidle"
    browser.close.assert_called_once()
