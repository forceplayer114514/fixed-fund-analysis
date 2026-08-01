"""ingest 层接住"自身即月报"指针 (2026-08-01 Coolabah 事故).

发现层判出"这一页自己就是月报"之后, ingest 必须:
  1. 把这一页存进 funds.confirmed_url + url_type + inception_date
  2. **同一个 job 内**直接走渲染通道, 不让用户再点一次"更新数据"
  3. 月份区间取自净值序列自带的首末日期, 不去猜"最新一期是哪个月"
  4. 后续每次更新时 inception_month 不用人工再填 (空则回落读 DB)
计划见 docs/superpowers/plans/2026-08-01-self-report-page-routing.md。
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


FUND_ID = "coolabah_self_report"
FUND_NAME = "Coolabah Global Floating-Rate High Yield Complex"
REPORT_URL = "https://coolabahcapital.com/performance-report-x"


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
        "url_type) VALUES (?, ?, '', 'code', 'archive')",
        (FUND_ID, FUND_NAME),
    )
    conn.commit()
    conn.close()
    yield tmp.name
    Path(tmp.name).unlink(missing_ok=True)


def _req(**over):
    from webapp.backend.app.schemas import IngestRequest
    base = dict(fund_id=FUND_ID, fund_name=FUND_NAME, issuer=None,
                confirmed_url=None, issuer_domain=None, asx_code=None,
                apir_code=None, max_pdf_pages=None, limit=None,
                inception_month=None)
    base.update(over)
    return IngestRequest(**base)


def _self_report_report(first_ym="2025-01", last_ym="2026-06"):
    """造一个 discovery 报告: 0 条 PDF 月报, 但带自报指针."""
    from llm_ingest.discover import ArchivePointer, DiscoveryReport
    ptr = ArchivePointer(
        archive_url=None, pagination_param=None, no_archive=True,
        latest_pdf_url=None, issuer_domain_confirmed="coolabahcapital.com",
        evidence="未判出任何月报 PDF, 但该页自身内嵌本基金净值序列",
        self_report_url=REPORT_URL,
        self_report_kind="performance_report_html",
        self_report_first_ym=first_ym, self_report_last_ym=last_ym,
    )
    return DiscoveryReport(fund_id=FUND_ID, links=[], archive_pointer=ptr)


def _run(req, *, discovery=None, extract_months=None):
    """跑一次 job, 把所有外部调用打桩. 返回 (jid, render_calls, extract_months)."""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest import discover as disc_mod
    from llm_ingest import extract as ex_mod
    from llm_ingest import fundmonitors as fm
    from llm_ingest import html_to_pdf as html_to_pdf_mod
    from llm_ingest import pdf as pdf_mod

    render_calls: list = []
    seen_months: list = extract_months if extract_months is not None else []

    def fake_render(url, out_path):
        render_calls.append(url)
        return out_path

    def fake_extract(pdf_path, ym, **kwargs):
        seen_months.append(ym)
        # 每月给不同数值: 连续相同的精确浮点会被 ANTI-FABRICATION GUARD 判成
        # 插值伪造并打回 pending (那是它该做的事, 不该在这里被误当成 bug)
        pct = 0.60 + len(seen_months) * 0.01
        return ex_mod.Extraction(
            ym=ym, net_return=round(pct / 100, 6),
            source_quote=f"Net Return {pct:.2f}%",
            measure="nav_pair", measure_label_in_pdf="Net Return (%)",
            rolling={}, not_found=False, raw={},
        )

    stack = [
        patch.object(fm, "probe", return_value={
            "status": "no_fundid", "records": [], "ytd_map": {},
            "url": None, "page_fund_name": None, "errors": []}),
        patch.object(html_to_pdf_mod, "render_html_to_pdf", side_effect=fake_render),
        patch.object(ex_mod, "extract_from_pdf", side_effect=fake_extract),
        patch.object(pdf_mod, "full_text", return_value=" ".join(
            f"Net Return {0.60 + i * 0.01:.2f}%" for i in range(1, 40))),
    ]
    if discovery is not None:
        stack.append(patch.object(disc_mod, "run_discovery", return_value=discovery))
    for p in stack:
        p.start()
    try:
        jid = ing._job_new(FUND_ID)
        ing._run_ingest_job(jid, req)
    finally:
        for p in reversed(stack):
            p.stop()
    return jid, render_calls, seen_months


def _fund_row(db_path):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT confirmed_url, url_type, inception_date, inception_assumed "
        "FROM funds WHERE fund_id=?", (FUND_ID,)).fetchone()
    conn.close()
    return row


def test_self_report_pointer_persisted_to_funds_row(tmp_db):
    """自报页三列都要写对, 且 inception_assumed=0 (序列首月是真实数据, 不是假设)。"""
    from webapp.backend.app.routers import ingest as ing
    jid, _renders, _months = _run(_req(), discovery=_self_report_report())
    assert ing._JOBS[jid]["state"] == "succeeded", ing._JOBS[jid].get("error")

    cu, url_type, inception, assumed = _fund_row(tmp_db)
    assert cu == REPORT_URL
    assert url_type == "performance_report_html"
    assert inception == "2025-01-31", "序列首月的月末"
    assert assumed == 0


def test_same_job_continues_into_render_channel(tmp_db):
    """不许"存好地址就收工, 等下一次请求" -- 同一个 job 内就得渲染 + 入库。"""
    from webapp.backend.app.routers import ingest as ing
    jid, renders, months = _run(_req(), discovery=_self_report_report())
    assert renders == [REPORT_URL], "同一 URL 只渲染 1 次"
    assert months, "同一个 job 内就应该提取月份"
    assert ing._JOBS[jid]["stats"]["monthly"] == len(months)


def test_month_range_comes_from_series_not_from_today(tmp_db):
    """区间 = 序列首月次月 .. 序列末月。首月本身没有上月 NAV 可比, 结构上算不出
    收益率, 必须排除; 末月是权威最新一期, 不去拿"今天的上个月"猜。"""
    _jid, _renders, months = _run(
        _req(), discovery=_self_report_report("2025-01", "2025-05"))
    assert months == ["2025-02", "2025-03", "2025-04", "2025-05"]


def test_second_run_reads_inception_from_db_when_request_omits_it(tmp_db):
    """第二次"更新数据": confirmed_url 已存好, 表单里没有成立月份 -> 从 DB 读,
    照样走渲染通道。回落失败会掉进 parse_archive_page, 0 links 后清空
    confirmed_url 重新搜索 -- 就是那个死循环。"""
    from webapp.backend.app.routers import ingest as ing
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "UPDATE funds SET confirmed_url=?, url_type='performance_report_html', "
        "inception_date='2025-01-31' WHERE fund_id=?", (REPORT_URL, FUND_ID))
    conn.commit()
    conn.close()

    # discovery 不打桩: 走到这里就说明回落失败了 (真去联网搜索)
    jid, renders, months = _run(_req(confirmed_url=REPORT_URL))
    assert ing._JOBS[jid]["state"] == "succeeded", ing._JOBS[jid].get("error")
    assert renders == [REPORT_URL]
    assert months, "应走单文件多月渲染通道"
    assert _fund_row(tmp_db)[0] == REPORT_URL, "confirmed_url 不许被清空"


def test_plain_pdf_fund_behaviour_unchanged(tmp_db):
    """回归: 自报指针为空的普通 PDF 基金, 一个字节都不该变。"""
    from webapp.backend.app.routers import ingest as ing
    from llm_ingest.discover import ArchivePointer, DiscoveryReport
    pdf_url = "https://issuer.com/Monthly_Jun26.pdf"
    rep = DiscoveryReport(
        fund_id=FUND_ID, links=[("2026-06", pdf_url)],
        archive_pointer=ArchivePointer(
            archive_url="https://issuer.com/reports", pagination_param=None,
            no_archive=False, latest_pdf_url=pdf_url,
            issuer_domain_confirmed="issuer.com", evidence="归档页确认"),
    )
    with patch.object(__import__("llm_ingest.cli", fromlist=["cli"]),
                      "_download_pdf", return_value=True):
        jid, renders, months = _run(_req(), discovery=rep)
    assert ing._JOBS[jid]["state"] == "succeeded", ing._JOBS[jid].get("error")
    assert renders == [], "PDF 基金不该触发 HTML 渲染"
    assert months == ["2026-06"]
    cu, url_type, inception, _assumed = _fund_row(tmp_db)
    assert cu == "https://issuer.com/reports", "仍是记住归档页那条老路径"
    assert url_type == "archive"
    assert inception is None


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
