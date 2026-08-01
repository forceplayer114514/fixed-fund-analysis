"""Spec F: HTML 通道走渲染 PDF (docs/superpowers/plans/2026-07-20-html-rendered-pdf-channel.md).

覆盖 PDD 单测计划第 6 条 (ingest.py 层面): single_file_multi_month 场景下,
同一 url 对应多个月份时, render_html_to_pdf 只应该被调用 1 次 (job 内缓存),
且 extract_from_pdf 调用时 max_pages 恒为 0 (页级裁剪会复现字节窗口切片同一类
bug, 不能沿用常规 PDF 通道的 2 页默认值)。
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def tmp_db(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    monkeypatch.setenv("FUND_DB_PATH", tmp.name)
    conn = sqlite3.connect(tmp.name)
    from llm_ingest import store
    store.ensure_tables_if_missing(conn)
    conn.execute("ALTER TABLE funds ADD COLUMN discovered_source_name TEXT")
    conn.execute("ALTER TABLE funds ADD COLUMN fundmonitors_fund_id INTEGER")
    conn.execute("ALTER TABLE funds ADD COLUMN fundmonitors_acc_code TEXT")
    conn.execute(
        "INSERT INTO funds (fund_id, fund_name, confirmed_url, fetch_method, "
        "url_type, fundmonitors_fund_id) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "html_fund", "HTML Fund",
            "https://example.com/plotly-report", "code",
            "performance_report_html", None,
        ),
    )
    conn.commit()
    conn.close()
    yield tmp.name
    Path(tmp.name).unlink(missing_ok=True)


def _make_req():
    from webapp.backend.app.schemas import IngestRequest
    return IngestRequest(
        fund_id="html_fund", fund_name="HTML Fund",
        issuer=None, confirmed_url="https://example.com/plotly-report",
        issuer_domain=None, asx_code=None, apir_code=None,
        max_pdf_pages=None, limit=None, inception_month="2026-04",
    )


def _expected_months():
    """与 ingest.py 里 single_file_multi_month 同样的"到上个月为止"逻辑,
    避免硬编码绝对月份导致测试在别的月份运行时失效。"""
    from datetime import datetime as _dt
    from llm_ingest import discover as disc_mod
    today = _dt.utcnow()
    end = today.replace(day=1)
    end = (end.replace(year=end.year - 1, month=12) if end.month == 1
           else end.replace(month=end.month - 1))
    end_ym = f"{end.year:04d}-{end.month:02d}"
    all_months = disc_mod._month_range("2026-04", end_ym)
    return all_months[1:] if len(all_months) > 1 else []


def test_single_file_multi_month_renders_once_and_uses_full_pdf(tmp_db):
    """inception_month=2026-04, 枚举到"上个月"为止 -> 2026-05/2026-06 两个月
    共用同一 url -> render_html_to_pdf 只调 1 次; extract_from_pdf 每次都是
    max_pages=0 (全文, 不裁页)。"""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import extract as ex_mod
    from llm_ingest import fundmonitors as fm
    from llm_ingest import pdf as pdf_mod
    from llm_ingest import html_to_pdf as html_to_pdf_mod

    months = _expected_months()
    assert len(months) >= 1, "测试前提: inception_month=2026-04 应枚举出至少 1 个月"
    fake_full_text = "Period Ending " + " ".join(
        f"Net Return {0.60 + i * 0.01:.2f}%" for i in range(1, 40))

    render_calls = []

    def fake_render(url, out_path):
        render_calls.append((url, out_path))
        return out_path

    extract_calls = []

    def fake_extract_from_pdf(pdf_path, ym, **kwargs):
        extract_calls.append({"ym": ym, "max_pages": kwargs.get("max_pages")})
        # 每月给不同数值: 枚举出的月份数随当前日期变化, 一旦达到 3 个月, 连续
        # 相同的精确浮点会被 ANTI-FABRICATION GUARD 判成插值伪造并转 pending
        # (那是它该做的事)。本测试要验的是"只渲染一次 + 全文喂模型", 与那道闸
        # 无关, 别让日期推移把它变成假失败。
        pct = 0.60 + len(extract_calls) * 0.01
        return ex_mod.Extraction(
            ym=ym, net_return=round(pct / 100, 6),
            source_quote=f"Net Return {pct:.2f}%",
            measure="nav_pair", measure_label_in_pdf="Net Return (%)",
            rolling={}, not_found=False, raw={},
        )

    with patch.object(fm, "probe", return_value={
        "status": "no_fundid", "records": [], "ytd_map": {},
        "url": None, "page_fund_name": None, "errors": [],
    }), \
         patch.object(html_to_pdf_mod, "render_html_to_pdf", side_effect=fake_render), \
         patch.object(ex_mod, "extract_from_pdf", side_effect=fake_extract_from_pdf), \
         patch.object(pdf_mod, "full_text", return_value=fake_full_text):
        jid = ing._job_new("html_fund")
        ing._run_ingest_job(jid, _make_req())

    assert ing._JOBS[jid]["state"] == "succeeded", ing._JOBS[jid].get("error")
    assert len(render_calls) == 1, f"应只渲染 1 次, 实际 {len(render_calls)} 次: {render_calls}"
    assert render_calls[0][0] == "https://example.com/plotly-report"

    assert len(extract_calls) == len(months)
    assert {c["ym"] for c in extract_calls} == set(months)
    assert all(c["max_pages"] == 0 for c in extract_calls), (
        "HTML 渲染后的 PDF 必须全文喂模型 (max_pages=0), 页级裁剪会复现"
        "字节窗口切片同一类 bug"
    )

    stats = ing._JOBS[jid]["stats"]
    assert stats.get("monthly") == len(months)


def test_html_render_failure_records_gap_not_crash(tmp_db):
    """渲染失败 (playwright 崩/超时) 记 confirmed_gap(html_render_fail), 不允许
    静默退回旧的字节窗口方法, 也不该让整个 job 崩溃 (若发生在 per-link 循环里;
    single_file_multi_month 的预渲染失败则是整个 job fail-fast, 见另一测试)。
    """
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import fundmonitors as fm
    from llm_ingest import html_to_pdf as html_to_pdf_mod

    with patch.object(fm, "probe", return_value={
        "status": "no_fundid", "records": [], "ytd_map": {},
        "url": None, "page_fund_name": None, "errors": [],
    }), patch.object(
        html_to_pdf_mod, "render_html_to_pdf",
        side_effect=html_to_pdf_mod.HtmlToPdfError("boom"),
    ):
        jid = ing._job_new("html_fund")
        ing._run_ingest_job(jid, _make_req())

    # single_file_multi_month 的预渲染就失败 -> 整个 job fail-fast (与旧版
    # "无法抓取" ValueError 语义一致), 不悄悄退回字节窗口方法。
    assert ing._JOBS[jid]["state"] == "failed"
    conn = sqlite3.connect(tmp_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM confirmed_gaps WHERE fund_id='html_fund'"
    ).fetchone()[0]
    conn.close()
    assert count == 0, "job 都没跑起来, 不该乱记 gap"
