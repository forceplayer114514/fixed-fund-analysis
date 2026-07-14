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
    create_fund,
    ensure_tables,
    get_connection,
    upsert_monthly_return,
)
from lib.extract import (
    download_and_extract_parallel,
    extract_pdf_links_from_archive,
    extract_pdf_one_bentham,
    extract_perf_rolling,
    gate_check,
    gate_check_table,
    get_last_day_of_month,
    parse_html_monthly_table,
    parse_pdf_text,
    parse_plotly_nav_series,
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
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
