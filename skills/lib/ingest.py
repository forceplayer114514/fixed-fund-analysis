"""全自动入库流水线：归档页 -> 并发下载+提取 PDF -> gate_check -> 入库。

agent 只做 MCP 抓取（JS 渲染归档页用 stealthy_fetch），存 markdown 文件后
调本模块的 add_fund / CLI 完成剩余全流程。本模块不 import webapp 代码，
只通过共享 SQLite 与 webapp 联系。

数据完整性：gate_check 是硬 gate，不通过不入库；commentary_truth=net_return
（Commentary 正文值供 webapp 复核）；序列起点=第一份真实研报日期，不反推捏造。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Callable, Optional

from lib.audit import audit_all_funds
from lib.consistency import consistency_check
from lib.db import (
    add_pending_review,
    create_fund,
    ensure_tables,
    get_connection,
    get_extractor_verified,
    get_fund,
    get_monthly_returns,
    list_confirmed_gaps,
    list_stale_pending_reviews,
    record_confirmed_gap,
    remove_confirmed_gap,
    upsert_monthly_return,
    verify_extractor,
)
from lib.extract import (
    ExtractedReturn,
    download_and_extract_parallel,
    extractor_names,
    extract_pdf_links_from_archive,
    extract_pdf_one_bentham,
    extract_perf_rolling,
    gate_check,
    gate_check_table,
    get_extractor,
    get_last_day_of_month,
    is_generic_extractor,
    parse_html_monthly_table,
    parse_pdf_text,
    parse_plotly_nav_series,
    pdf_cache_dir,
)


def _ym_to_month_end(ym: str) -> Optional[str]:
    """'2025-03' -> '2025-03-31'（月末日期）。失败返回 None。"""
    parts = ym.split("-")
    if len(parts) != 2:
        return None
    try:
        year, month = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (1 <= month <= 12):
        return None
    return get_last_day_of_month(year, month).strftime("%Y-%m-%d")


def add_fund(
    fund_id: str,
    name: str,
    archive_html_path: str,
    *,
    confirmed_url: str,
    apir: Optional[str] = None,
    url_type: str = "archive_page",
    fetch_method: str = "pdf",
    verified_at: Optional[str] = None,
    db_path: Optional[str] = None,
    max_workers: Optional[int] = None,
    shareclass_prefix: Optional[str] = None,
    extractor: Optional[Callable[[str], tuple[Optional[float], dict]]] = None,
) -> dict:
    """全自动流水线。

    extractor 默认 None -> extract_pdf_one（Stake 口径）；Bentham 等专属口径
    传 extract_pdf_one_bentham。

    Returns:
        {'months','start','end','gaps','gate_pass','errors',
         'failed_months','short_history_warning'}
    gate_pass=False 时未入库，errors 列出问题。
    """
    # 1. 读归档页
    with open(archive_html_path, "r", encoding="utf-8") as f:
        markdown = f.read()

    # 2. 提 PDF 链接
    links = extract_pdf_links_from_archive(markdown)
    if not links:
        return {"months": 0, "start": None, "end": None, "gaps": [],
                "gate_pass": False, "errors": ["归档页无 PDF 链接"],
                "failed_months": [], "short_history_warning": True}

    # 3. 并发下载+提取
    dest_dir = os.path.join(
        os.path.dirname(os.path.abspath(archive_html_path)), f"{fund_id}_pdfs"
    )
    results = download_and_extract_parallel(
        links, dest_dir, max_workers=max_workers, extractor=extractor
    )

    # 4. 组装 records + rolling_per_month（排除提取失败项）
    records: list[tuple[str, float]] = []
    rolling_per_month: dict = {}
    failed_months: list[str] = []
    for ym, commentary, rolling in results:
        if commentary is None:
            failed_months.append(ym)
            continue
        date = _ym_to_month_end(ym)
        if date is None:
            failed_months.append(ym)
            continue
        records.append((date, commentary))
        rolling_per_month[ym] = rolling

    # 5. gate_check（硬 gate）
    pass_ok, errors = gate_check(records, rolling_per_month)
    if not pass_ok:
        return {"months": len(records), "start": None, "end": None,
                "gaps": [], "gate_pass": False, "errors": errors,
                "failed_months": failed_months,
                "short_history_warning": len(records) < 36}

    # 5.5 consistency_check（跨序列 + 复利全窗口）
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)
        # 取最新月 rolling 供 consistency compound 用
        latest_ym = max(d[:7] for d, _ in records) if records else None
        latest_rolling = rolling_per_month.get(latest_ym, {}) if latest_ym else {}
        c_ok, c_block, c_warn = consistency_check(
            fund_id, records, conn, rolling=latest_rolling,
            shareclass_prefix=shareclass_prefix,
        )
        if not c_ok:
            return {"months": len(records), "start": None, "end": None,
                    "gaps": [], "gate_pass": False,
                    "errors": errors + c_block + [f"[warn] {w}" for w in c_warn],
                    "failed_months": failed_months,
                    "short_history_warning": len(records) < 36}
        # 6. 入库（gate + consistency 通过后才写 DB）
        create_fund(
            conn, fund_id=fund_id, fund_name=name,
            confirmed_url=confirmed_url, fetch_method=fetch_method,
            url_type=url_type, apir_code=apir, verified_at=verified_at,
            shareclass_prefix=shareclass_prefix,
        )
        for date, net_return in records:
            upsert_monthly_return(
                conn, fund_id=fund_id, date=date,
                net_return=net_return, commentary_truth=net_return,
            )
        # 入库后自动 audit
        audit_all_funds(conn)
    finally:
        conn.close()

    # 7. 报告
    records_sorted = sorted(records, key=lambda x: x[0])
    return {
        "months": len(records_sorted),
        "start": records_sorted[0][0] if records_sorted else None,
        "end": records_sorted[-1][0] if records_sorted else None,
        "gaps": [],
        "gate_pass": True,
        "errors": [],
        "failed_months": failed_months,
        "short_history_warning": len(records_sorted) < 36,
    }


def add_fund_from_html_table(
    fund_id: str,
    name: str,
    table_html_path: str,
    *,
    confirmed_url: str,
    apir: Optional[str] = None,
    url_type: str = "fact_sheet_profile",
    fetch_method: str = "html",
    verified_at: Optional[str] = None,
    db_path: Optional[str] = None,
    shareclass_prefix: Optional[str] = None,
) -> dict:
    """HTML 表格源全自动流水线（fundmonitors 等聚合站逐月收益表）。

    读 markdown -> parse_html_monthly_table -> gate_check_table -> 入库。
    用于官方无 PDF 归档、聚合站提供 Year×Month 逐月表的基金。NAV 由
    upsert_monthly_return 自动重算。序列起点=第一份有数据月，不反推捏造。

    Returns:
        {'months','start','end','gaps','gate_pass','errors',
         'failed_months','short_history_warning'}
    gate_pass=False 时未入库，errors 列出问题。
    """
    # 1. 读 HTML/markdown
    with open(table_html_path, "r", encoding="utf-8") as f:
        markdown = f.read()

    # 2. 解析逐月表
    records, ytd_map = parse_html_monthly_table(markdown)
    if not records:
        return {"months": 0, "start": None, "end": None, "gaps": [],
                "gate_pass": False, "errors": ["表格无有效月度数据"],
                "failed_months": [], "short_history_warning": True}

    # 3. gate_check_table（硬 gate）
    pass_ok, errors = gate_check_table(records, ytd_map)
    records_sorted = sorted(records, key=lambda x: x[0])
    if not pass_ok:
        return {"months": len(records_sorted),
                "start": records_sorted[0][0] if records_sorted else None,
                "end": records_sorted[-1][0] if records_sorted else None,
                "gaps": [], "gate_pass": False, "errors": errors,
                "failed_months": [], "short_history_warning": len(records_sorted) < 36}

    # 3.5 consistency_check（跨序列；表格源无 rolling，YTD 已在 gate_check_table 验证）
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)
        c_ok, c_block, c_warn = consistency_check(
            fund_id, records_sorted, conn, rolling=None,
            shareclass_prefix=shareclass_prefix,
        )
        if not c_ok:
            return {"months": len(records_sorted),
                    "start": records_sorted[0][0] if records_sorted else None,
                    "end": records_sorted[-1][0] if records_sorted else None,
                    "gaps": [], "gate_pass": False,
                    "errors": errors + c_block + [f"[warn] {w}" for w in c_warn],
                    "failed_months": [],
                    "short_history_warning": len(records_sorted) < 36}
        # 4. 入库（gate + consistency 通过后才写 DB）
        create_fund(
            conn, fund_id=fund_id, fund_name=name,
            confirmed_url=confirmed_url, fetch_method=fetch_method,
            url_type=url_type, apir_code=apir, verified_at=verified_at,
            shareclass_prefix=shareclass_prefix,
        )
        for date, net_return in records_sorted:
            upsert_monthly_return(
                conn, fund_id=fund_id, date=date,
                net_return=net_return, commentary_truth=net_return,
            )
        # 入库后自动 audit
        audit_all_funds(conn)
    finally:
        conn.close()

    # 5. 报告
    return {
        "months": len(records_sorted),
        "start": records_sorted[0][0],
        "end": records_sorted[-1][0],
        "gaps": [],
        "gate_pass": True,
        "errors": [],
        "failed_months": [],
        "short_history_warning": len(records_sorted) < 36,
    }


def add_fund_from_plotly_html(
    fund_id: str,
    name: str,
    plotly_html_path: str,
    *,
    confirmed_url: str,
    rolling_pdf_path: str,
    fund_name_pattern: str,
    shareclass_prefix: Optional[str] = None,
    apir: Optional[str] = None,
    url_type: str = "plotly_report",
    fetch_method: str = "html",
    verified_at: Optional[str] = None,
    db_path: Optional[str] = None,
) -> dict:
    """Plotly HTML 报告源全自动流水线（Coolabah 模式）。

    parse_plotly_nav_series 按 name 提基金类 NAV -> 月度收益 = nav_t/nav_{t-1}-1
    -> PDF rolling 复利全窗口验证 -> gate_check 单序列 -> consistency_check
    跨序列 -> 入库。NAV 是 net-of-fees total return（含分红再投）。

    Returns:
        {'months','start','end','gaps','gate_pass','errors',
         'failed_months','short_history_warning'}
    gate_pass=False 时未入库，errors 列出问题。
    """
    # 1. 读 Plotly HTML
    with open(plotly_html_path, "r", encoding="utf-8") as f:
        html = f.read()
    nav_series = parse_plotly_nav_series(html, fund_name_pattern)

    # 2. NAV -> 月度收益（nav_t / nav_{t-1} - 1）
    nav_series.sort(key=lambda x: x[0])
    records: list[tuple[str, float]] = []
    for i in range(1, len(nav_series)):
        prev_d, prev_nav = nav_series[i - 1]
        d, nav = nav_series[i]
        r = nav / prev_nav - 1.0
        records.append((d, r))

    # 3. PDF rolling
    pdf_text = parse_pdf_text(rolling_pdf_path)
    rolling = extract_perf_rolling(pdf_text)

    # 4. 单序列 gate（空 rolling_per_month -> 跳过 gate 内部复利，由 consistency 接管）
    pass_ok, errors = gate_check(records, {})
    if not pass_ok:
        return {"months": len(records), "start": None, "end": None,
                "gaps": [], "gate_pass": False, "errors": errors,
                "failed_months": [], "short_history_warning": len(records) < 36}

    # 5. consistency_check（含复利全窗口 + shareclass 跨序列）
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)
        c_ok, c_block, c_warn = consistency_check(
            fund_id, records, conn,
            shareclass_prefix=shareclass_prefix, rolling=rolling,
        )
        if not c_ok:
            return {"months": len(records), "start": None, "end": None,
                    "gaps": [], "gate_pass": False,
                    "errors": errors + c_block + [f"[warn] {w}" for w in c_warn],
                    "failed_months": [],
                    "short_history_warning": len(records) < 36}
        # 6. 入库
        create_fund(
            conn, fund_id=fund_id, fund_name=name,
            confirmed_url=confirmed_url, fetch_method=fetch_method,
            url_type=url_type, apir_code=apir, verified_at=verified_at,
            shareclass_prefix=shareclass_prefix,
        )
        for date, net_return in records:
            upsert_monthly_return(
                conn, fund_id=fund_id, date=date,
                net_return=net_return, commentary_truth=net_return,
            )
        # 入库后自动 audit
        audit_all_funds(conn)
    finally:
        conn.close()

    # 7. 报告
    records_sorted = sorted(records, key=lambda x: x[0])
    return {
        "months": len(records_sorted),
        "start": records_sorted[0][0] if records_sorted else None,
        "end": records_sorted[-1][0] if records_sorted else None,
        "gaps": [],
        "gate_pass": True,
        "errors": [],
        "failed_months": [],
        "short_history_warning": len(records_sorted) < 36,
    }


def ingest_discovery(
    report,
    name: str,
    *,
    db_path: Optional[str] = None,
    extractor: Optional[Callable] = None,
    extractor_name: Optional[str] = "generic",
    max_workers: Optional[int] = None,
    shareclass_prefix: Optional[str] = None,
    apir: Optional[str] = None,
    verified_at: Optional[str] = None,
    url_type: str = "archive_page",
    dest_dir: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """接 DiscoveryReport 入库分流(M4 集合差驱动入库入口)。

    遍历 report.per_level_payload(按 L0->L3 保序):
    - L1/L2 PDF(links):download_and_extract_parallel 下载提取
    - L3 HTML(records):直接用
    - L0 local:跳过(已入库)
    合并 records(去重 by date,低 level 优先)。

    gate_check 分类(缺口非失败,修正3.2.4):
    - 缺口 -> report.gaps 写 confirmed_gaps(不 fail)
    - |r|>=0.5 -> pending_review(不丢弃,§5 闸门/确认);over 月存在时复利
      验证降级(over 月致复利不可靠,不因连带复利 fail)
    - ANTI-FABRICATION / 复利 fail(无 over 时)-> 真 fail 不入库

    create_fund 存 inception_date/inception_assumed(从 report)。

    Returns: {'months','start','end','gaps','gate_pass','errors',
              'failed_months','pending_review_count','short_history_warning'}
    """
    records: list[tuple[str, ExtractedReturn]] = []
    rolling_per_month: dict = {}
    seen_dates: set = set()
    failed_months: list = []
    best_payload: Optional[dict] = None

    # Phase 0:PDF 持久缓存 — dest_dir 缺省用 pdf_cache_dir(fund_id),不再 mkdtemp
    # 后进程结束丢弃。max_pdf_pages 从 funds 表读取(fund 未注册时 None 读全部页)。
    if dest_dir is None:
        dest_dir = pdf_cache_dir(report.fund_id)
    # 读 max_pdf_pages(可能 fund 尚未 create -> None)
    max_pages: Optional[int] = None
    try:
        _tmp_conn = get_connection(db_path)
        try:
            ensure_tables(_tmp_conn)
            _fund_row = get_fund(_tmp_conn, report.fund_id)
            if _fund_row is not None:
                max_pages = _fund_row.get("max_pdf_pages")
        finally:
            _tmp_conn.close()
    except Exception:
        max_pages = None

    for level_key, payload in report.per_level_payload.items():
        if not payload:
            continue
        if best_payload is None and payload.get("fetch_method") != "local":
            best_payload = payload
        if payload.get("fetch_method") == "local":
            continue  # L0 已入库,跳过
        if "links" in payload:
            results = download_and_extract_parallel(
                payload["links"], dest_dir,
                max_workers=max_workers, extractor=extractor, return_full=True,
                max_pages=max_pages,
            )
            for ym, er, rolling in results:
                if er is None:
                    failed_months.append(ym)
                    continue
                date = _ym_to_month_end(ym)
                if date is None:
                    failed_months.append(ym)
                    continue
                if date in seen_dates:
                    continue
                seen_dates.add(date)
                records.append((date, er))
                rolling_per_month[ym] = rolling
        elif "records" in payload:
            for date_str, ret in payload["records"]:
                if date_str in seen_dates:
                    continue
                seen_dates.add(date_str)
                records.append((date_str, ExtractedReturn(
                    value=ret, source_quote="", ambiguous=False)))

    if not records:
        return {"months": 0, "start": None, "end": None, "gaps": report.gaps,
                "gate_pass": False,
                "errors": ["无 records 可入库(提取全失败或无 payload)"],
                "failed_months": failed_months, "pending_review_count": 0,
                "short_history_warning": True,
                "extractor_mismatch": bool(failed_months),
                "mismatch_months": failed_months}

    gate_records = [(d, er.value) for d, er in records]
    _pass_ok, errors = gate_check(gate_records, rolling_per_month)
    over_threshold = {d for d, er in records if abs(er.value) >= 0.5}
    has_over = bool(over_threshold)
    other_errors = [
        e for e in errors
        if "缺口" not in e
        and "字段异常" not in e
        and not (has_over and "复利验证失败" in e)
    ]

    records_sorted = sorted(records, key=lambda x: x[0])
    start = records_sorted[0][0] if records_sorted else None
    end = records_sorted[-1][0] if records_sorted else None

    # EXTRACTOR_MISMATCH(A3):generic 正则没匹配(failed_months 非空)-> 报指引,
    # 不 fail(已提取月仍入库/pending)。提示走 add_fixed_fund.md 4.5 加专属提取器。
    extractor_mismatch = bool(failed_months)
    mismatch_errors: list[str] = []
    if extractor_mismatch:
        mismatch_errors.append(
            f"EXTRACTOR_MISMATCH: 提取器({extractor_name})无法匹配该基金 "
            f"Commentary(失败月: {failed_months})。固定流程(add_fixed_fund.md 4.5): "
            "1.下2-3样本PDF(早期+晚期)REPL探正则(仅探,不发write_token) "
            "2.extract.py加extract_<issuer>_net_return_full+source_quote "
            "3.tests/test_extract.py加测试(早期+晚期) "
            "4.EXTRACTORS注册表加{<issuer>:...} "
            "5.--dry-run全量回跑验收(成功率+gate通过率) "
            "6.pytest通过后--extractor <issuer>重跑discover。禁REPL手写提取入库"
        )

    if other_errors:
        return {"months": len(records_sorted), "start": start, "end": end,
                "gaps": report.gaps, "gate_pass": False,
                "errors": other_errors + mismatch_errors,
                "failed_months": failed_months, "pending_review_count": 0,
                "short_history_warning": len(records_sorted) < 36,
                "extractor_mismatch": extractor_mismatch,
                "mismatch_months": failed_months}

    # dry_run:跑提取+gate不入库,出报告(4.5 全量回跑验收用)
    if dry_run:
        return {"months": len(records_sorted), "start": start, "end": end,
                "gaps": report.gaps, "gate_pass": not extractor_mismatch,
                "errors": mismatch_errors, "failed_months": failed_months,
                "extractor_mismatch": extractor_mismatch,
                "mismatch_months": failed_months,
                "pending_review_count": 0,
                "short_history_warning": len(records_sorted) < 36,
                "dry_run": True}

    # 入库三层分流(A4):
    # |r|>=0.5 -> pending abs_return_exceeds_threshold(无论 generic/专属)
    # generic+ambiguous -> pending ambiguous_subject(反 benchmark 守卫命中)
    # generic+!ambiguous+!verified -> pending generic_first_use(新基金首批全审)
    # generic+!ambiguous+verified -> 直通(update 增量)
    # 专属 -> 直通(gate 已跑)
    confirmed_url = (best_payload or {}).get("confirmed_url", "")
    fetch_method = (best_payload or {}).get("fetch_method", "pdf")
    use_generic = is_generic_extractor(extractor_name)
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)
        already_exists = get_fund(conn, report.fund_id) is not None
        extractor_verified = (
            get_extractor_verified(conn, report.fund_id) if already_exists else 0
        )
        if not already_exists:
            create_fund(
                conn, fund_id=report.fund_id, fund_name=name,
                confirmed_url=confirmed_url, fetch_method=fetch_method,
                url_type=url_type, apir_code=apir, verified_at=verified_at,
                shareclass_prefix=shareclass_prefix,
                inception_date=report.inception_date,
                inception_assumed=1 if report.inception_assumed else 0,
                extractor_verified=0,
            )
        pending_count = 0
        ingested_dates: list[str] = []
        for date, er in records:
            r = er.value
            if abs(r) >= 0.5:
                add_pending_review(
                    conn, fund_id=report.fund_id, date=date, net_return=r,
                    source_quote=er.source_quote, extract_method="code",
                    gate_result="abs_return_exceeds_threshold",
                    review_reason="abs_return_exceeds_threshold",
                )
                pending_count += 1
            elif use_generic and er.ambiguous:
                add_pending_review(
                    conn, fund_id=report.fund_id, date=date, net_return=r,
                    source_quote=er.source_quote, extract_method="code",
                    gate_result="ambiguous_subject",
                    review_reason="ambiguous_subject",
                )
                pending_count += 1
            elif use_generic and not extractor_verified:
                add_pending_review(
                    conn, fund_id=report.fund_id, date=date, net_return=r,
                    source_quote=er.source_quote, extract_method="code",
                    gate_result="generic_first_use",
                    review_reason="generic_first_use",
                )
                pending_count += 1
            else:
                upsert_monthly_return(
                    conn, fund_id=report.fund_id, date=date,
                    net_return=r, commentary_truth=r,
                )
                ingested_dates.append(date)
        exhausted = ",".join(report.per_level_contribution.keys()) or "L0,L1,L2,L3"
        for gap_ym in report.gaps:
            record_confirmed_gap(
                conn, fund_id=report.fund_id, missing_month=gap_ym,
                exhausted_levels=exhausted,
            )
        audit_all_funds(conn)
    finally:
        conn.close()

    ingested_dates.sort()
    return {
        "months": len(ingested_dates),
        "start": ingested_dates[0] if ingested_dates else None,
        "end": ingested_dates[-1] if ingested_dates else None,
        "gaps": report.gaps,
        "gate_pass": True,
        "errors": mismatch_errors,
        "failed_months": failed_months,
        "extractor_mismatch": extractor_mismatch,
        "mismatch_months": failed_months,
        "pending_review_count": pending_count,
        "short_history_warning": len(records_sorted) < 36,
    }


def _collect_records(report, dest_dir=None, extractor=None, max_workers=None,
                     max_pages=None):
    """从 report.per_level_payload 收集 records(L1/L2 links 下载提取,L3 records
    直接,L0 local 跳过)。返回 (records, rolling_per_month, failed_months)。
    去重 by date,低 level 优先。

    Phase 0:dest_dir 缺省用 pdf_cache_dir(fund_id)(持久缓存,反复 update 免重下)。
    max_pages 透传给 download_and_extract_parallel,来源为 funds.max_pdf_pages。
    """
    records: list[tuple[str, float]] = []
    rolling_per_month: dict = {}
    seen_dates: set = set()
    failed_months: list = []
    for level_key, payload in report.per_level_payload.items():
        if not payload or payload.get("fetch_method") == "local":
            continue
        if "links" in payload:
            if dest_dir is None:
                dest_dir = pdf_cache_dir(report.fund_id)
            results = download_and_extract_parallel(
                payload["links"], dest_dir,
                max_workers=max_workers, extractor=extractor, return_full=True,
                max_pages=max_pages,
            )
            for ym, er, rolling in results:
                if er is None:
                    failed_months.append(ym)
                    continue
                date = _ym_to_month_end(ym)
                if date is None:
                    failed_months.append(ym)
                    continue
                if date in seen_dates:
                    continue
                seen_dates.add(date)
                records.append((date, er))
                rolling_per_month[ym] = rolling
        elif "records" in payload:
            for date_str, ret in payload["records"]:
                if date_str in seen_dates:
                    continue
                seen_dates.add(date_str)
                records.append((date_str, ExtractedReturn(
                    value=ret, source_quote="", ambiguous=False)))
    return records, rolling_per_month, failed_months


def update_fund(
    fund_id: str,
    *,
    db_path: Optional[str] = None,
    archive_markdown: Optional[str] = None,
    latest_month: Optional[str] = None,
    extractor: Optional[Callable] = None,
    extractor_name: Optional[str] = "generic",
    max_workers: Optional[int] = None,
    dest_dir: Optional[str] = None,
) -> dict:
    """update 侧:增量复查最新月 + confirmed_gaps 复查 + inception 重探 + 滞留报告(M6)。

    archive_markdown 为 None 时 curl 抓 confirmed_url(生产);测试可直接传入。
    latest_month 为 None 时用当前月-2(≤2 自然月滞后不算缺口)。

    读 fund 配置 + 现有月 + confirmed_gaps -> run_discovery(集合差驱动,L0 读
    现有月 + L1 抓归档页 + L2 CDX 补洞;inception_assumed 时 L2 查更早快照重探
    下界,补3)-> _collect_records -> 新月 upsert(|r|>=0.5 进 pending_review)
    -> confirmed_gaps 增删(新补录的原 gap 移除,report.gaps 写入)
    -> pending_review 滞留报告(review_state='pending' 且 >14 天)。

    Returns: {'updated','new_months','pending_review_count','gaps','failed_months',
              'stale_pending_reviews','inception_assumed'}
    """
    from lib.strategies import run_discovery
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)
        fund = get_fund(conn, fund_id)
        if fund is None:
            return {"updated": False, "errors": [f"基金 {fund_id} 未注册"]}
        existing_rows = get_monthly_returns(conn, fund_id)
        existing_months = {r["date"][:7] for r in existing_rows}
        existing_gaps = {g["missing_month"] for g in list_confirmed_gaps(conn, fund_id)}
        confirmed_url = fund["confirmed_url"]
        if archive_markdown is None:
            import subprocess
            r = subprocess.run(
                ["curl", "-sL", "--max-time", "60", confirmed_url],
                capture_output=True, text=True, timeout=70,
            )
            archive_markdown = r.stdout if r.returncode == 0 and r.stdout else ""
        fund_info = {
            "fund_id": fund_id, "confirmed_url": confirmed_url,
            "archive_markdown": archive_markdown,
            "inception_date": fund["inception_date"],
            "inception_assumed": bool(fund["inception_assumed"]),
            "latest_month": latest_month,
            "db_path": db_path,
        }
        report = run_discovery(fund_info)
        records, _rolling, failed_months = _collect_records(
            report, dest_dir=dest_dir, extractor=extractor, max_workers=max_workers,
            max_pages=fund.get("max_pdf_pages"),
        )

        use_generic = is_generic_extractor(extractor_name)
        extractor_verified = get_extractor_verified(conn, fund_id)
        new_records = [(d, er) for d, er in records if d[:7] not in existing_months]
        upserted = 0
        pending_count = 0
        newly_filled: set = set()
        for date, er in new_records:
            r = er.value
            if abs(r) >= 0.5:
                add_pending_review(
                    conn, fund_id=fund_id, date=date, net_return=r,
                    source_quote=er.source_quote, extract_method="code",
                    gate_result="abs_return_exceeds_threshold",
                    review_reason="abs_return_exceeds_threshold",
                )
                pending_count += 1
            elif use_generic and er.ambiguous:
                add_pending_review(
                    conn, fund_id=fund_id, date=date, net_return=r,
                    source_quote=er.source_quote, extract_method="code",
                    gate_result="ambiguous_subject",
                    review_reason="ambiguous_subject",
                )
                pending_count += 1
            elif use_generic and not extractor_verified:
                add_pending_review(
                    conn, fund_id=fund_id, date=date, net_return=r,
                    source_quote=er.source_quote, extract_method="code",
                    gate_result="generic_first_use",
                    review_reason="generic_first_use",
                )
                pending_count += 1
            else:
                upsert_monthly_return(
                    conn, fund_id=fund_id, date=date,
                    net_return=r, commentary_truth=r,
                )
                upserted += 1
                if date[:7] in existing_gaps:
                    newly_filled.add(date[:7])
        # confirmed_gaps 增删:report.gaps 写入;新补录的原 gap 移除
        exhausted = ",".join(report.per_level_contribution.keys()) or "L0,L1,L2,L3"
        for gap_ym in report.gaps:
            record_confirmed_gap(
                conn, fund_id=fund_id, missing_month=gap_ym,
                exhausted_levels=exhausted,
            )
        for gap_ym in newly_filled:
            remove_confirmed_gap(conn, fund_id, gap_ym)
        audit_all_funds(conn)
        stale = list_stale_pending_reviews(conn, days=14)
        return {
            "updated": True, "new_months": upserted,
            "pending_review_count": pending_count,
            "gaps": sorted(set(report.gaps)),
            "failed_months": failed_months,
            "extractor_mismatch": bool(failed_months),
            "mismatch_months": failed_months,
            "stale_pending_reviews": stale,
            "inception_assumed": bool(fund["inception_assumed"]),
        }
    finally:
        conn.close()


def _cli() -> int:
    parser = argparse.ArgumentParser(description="skills 全自动入库流水线")
    parser.add_argument(
        "--write-token", default=None,
        help="写入令牌（设置 FUND_DB_WRITE_TOKEN 环境变量，Task 6 启用 gate）",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    add_p = sub.add_parser("add", help="新增基金入库")
    add_p.add_argument("--fund-id", required=True)
    add_p.add_argument("--name", required=True)
    add_p.add_argument("--archive-html", required=True, help="归档页 markdown 路径")
    add_p.add_argument("--confirmed-url", required=True, help="归档页 URL（数据源）")
    add_p.add_argument("--apir", default=None)
    add_p.add_argument("--verified-at", default=None)
    add_p.add_argument("--max-workers", type=int, default=None)
    tbl_p = sub.add_parser("add-table", help="新增基金入库（HTML 表格源，如 fundmonitors）")
    tbl_p.add_argument("--fund-id", required=True)
    tbl_p.add_argument("--name", required=True)
    tbl_p.add_argument("--table-html", required=True, help="含逐月收益表的 markdown 路径")
    tbl_p.add_argument("--confirmed-url", required=True, help="数据源 URL")
    tbl_p.add_argument("--apir", default=None)
    tbl_p.add_argument("--verified-at", default=None)
    pl_p = sub.add_parser("add-plotly", help="新增基金入库（Plotly HTML + PDF rolling，Coolabah 模式）")
    pl_p.add_argument("--fund-id", required=True)
    pl_p.add_argument("--name", required=True)
    pl_p.add_argument("--plotly-html", required=True, help="Plotly HTML 报告路径")
    pl_p.add_argument("--rolling-pdf", required=True, help="PDF rolling 报告路径")
    pl_p.add_argument("--fund-name-pattern", required=True, help="份额类 name 过滤模式")
    pl_p.add_argument("--shareclass-prefix", default=None)
    pl_p.add_argument("--confirmed-url", required=True)
    pl_p.add_argument("--apir", default=None)
    pl_p.add_argument("--verified-at", default=None)
    disc_p = sub.add_parser("discover", help="LLM 定位归档页 URL 后代码全流程(抓取+run_discovery+ingest_discovery)")
    disc_p.add_argument("--fund-id", required=True)
    disc_p.add_argument("--name", required=True)
    disc_p.add_argument("--url", required=True, help="已验证归档页 URL(L1,LLM 定位)")
    disc_p.add_argument("--inception-date", default=None)
    disc_p.add_argument("--inception-assumed", action="store_true")
    disc_p.add_argument("--latest-month", default=None)
    disc_p.add_argument("--issuer-domain", default=None)
    disc_p.add_argument("--apir", default=None)
    disc_p.add_argument("--verified-at", default=None)
    disc_p.add_argument("--db-path", default=None)
    disc_p.add_argument("--extractor", default="generic",
                        choices=extractor_names(),
                        help="PDF 提取器(generic=默认 returned X%; 新基金先 generic,"
                             "gate 失败/EXTRACTOR_MISMATCH 走 add_fixed_fund.md 4.5 加专属)")
    disc_p.add_argument("--dry-run", action="store_true",
                        help="跑提取+gate 不入库,出报告(4.5 全量回跑验收用)")
    upd_p = sub.add_parser("update", help="增量复查最新月+confirmed_gaps 复查+pending_review 滞留报告")
    upd_p.add_argument("--fund-id", required=True)
    upd_p.add_argument("--db-path", default=None)
    upd_p.add_argument("--extractor", default="generic", choices=extractor_names(),
                       help="PDF 提取器(须与 discover 时一致)")
    ve_p = sub.add_parser("verify-extractor",
                          help="人工审核通过 generic 首批后:标 extractor_verified=1 + 批量 promote generic_first_use pending")
    ve_p.add_argument("--fund-id", required=True)
    ve_p.add_argument("--db-path", default=None)
    args = parser.parse_args()

    if args.write_token is not None:
        os.environ["FUND_DB_WRITE_TOKEN"] = args.write_token

    if args.command == "add":
        result = add_fund(
            args.fund_id, args.name, args.archive_html,
            confirmed_url=args.confirmed_url, apir=args.apir,
            verified_at=args.verified_at, max_workers=args.max_workers,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["gate_pass"] else 1
    if args.command == "add-table":
        result = add_fund_from_html_table(
            args.fund_id, args.name, args.table_html,
            confirmed_url=args.confirmed_url, apir=args.apir,
            verified_at=args.verified_at,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["gate_pass"] else 1
    if args.command == "add-plotly":
        result = add_fund_from_plotly_html(
            args.fund_id, args.name, args.plotly_html,
            confirmed_url=args.confirmed_url,
            rolling_pdf_path=args.rolling_pdf,
            fund_name_pattern=args.fund_name_pattern,
            shareclass_prefix=args.shareclass_prefix,
            apir=args.apir, verified_at=args.verified_at,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["gate_pass"] else 1
    if args.command == "discover":
        from lib.strategies import run_discovery
        import subprocess
        r = subprocess.run(
            ["curl", "-sL", "--max-time", "60", args.url],
            capture_output=True, text=True, timeout=70,
        )
        archive_html = r.stdout if r.returncode == 0 and r.stdout else ""
        if not archive_html:
            print(json.dumps({"gate_pass": False,
                              "errors": [f"curl 抓归档页失败: {args.url}"]},
                             ensure_ascii=False))
            return 1
        fund_info = {
            "fund_id": args.fund_id, "confirmed_url": args.url,
            "archive_markdown": archive_html,
            "inception_date": args.inception_date,
            "inception_assumed": args.inception_assumed,
            "latest_month": args.latest_month,
            "issuer_domain": args.issuer_domain,
            "db_path": args.db_path,
        }
        report = run_discovery(fund_info)
        extractor = get_extractor(args.extractor)
        result = ingest_discovery(
            report, args.name, db_path=args.db_path,
            apir=args.apir, verified_at=args.verified_at,
            extractor=extractor, extractor_name=args.extractor,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["gate_pass"] else 1
    if args.command == "update":
        result = update_fund(args.fund_id, db_path=args.db_path,
                             extractor_name=args.extractor,
                             extractor=get_extractor(args.extractor))
        print(json.dumps(result, indent=2, ensure_ascii=False, default=list))
        return 0
    if args.command == "verify-extractor":
        conn = get_connection(args.db_path)
        try:
            ensure_tables(conn)
            result = verify_extractor(conn, args.fund_id)
        finally:
            conn.close()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
