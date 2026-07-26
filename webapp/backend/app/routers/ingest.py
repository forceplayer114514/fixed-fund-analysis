"""LLM 摄取路由 (Phase 4 薄壳).

POST /api/ingest/funds        起 discovery+ingest 后台任务, 返回 job_id.
GET  /api/ingest/jobs/{id}    查任务状态 (job 状态存内存字典, 进程重启即丢).
GET  /api/pending             列 pending_review 待人工审核记录.
PATCH /api/pending/{id}/approve  人工通过 -> monthly_returns + NAV 重算.
PATCH /api/pending/{id}/reject   人工拒绝 -> 标 rejected.

前端"添加基金"改走此路由触发真实数据抓取,不再只登记元信息。
job 用 threading (BackgroundTasks 会阻塞后续请求处理).
"""
from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Fund, PendingReview
from app.schemas import (
    IngestJobResponse,
    IngestRequest,
    PendingReviewResponse,
    sanitize_for_json,
)

# ---- 内存 job registry (进程内, 重启丢) ----
# key: job_id -> {fund_id, state, started_at, finished_at, stats, log_tail, error}
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_LOG_TAIL_MAX = 50


def _job_new(fund_id: str) -> str:
    jid = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[jid] = {
            "job_id": jid,
            "fund_id": fund_id,
            "state": "queued",
            "started_at": None,
            "finished_at": None,
            "stats": None,
            "log_tail": [],
            "error": None,
        }
    return jid


def _job_update(jid: str, **fields: Any) -> None:
    with _JOBS_LOCK:
        j = _JOBS.get(jid)
        if j is None:
            return
        j.update(fields)


def _job_log(jid: str, msg: str) -> None:
    with _JOBS_LOCK:
        j = _JOBS.get(jid)
        if j is None:
            return
        tail: List[str] = j["log_tail"]
        tail.append(msg)
        if len(tail) > _LOG_TAIL_MAX:
            del tail[: len(tail) - _LOG_TAIL_MAX]


def _rendered_pdf_filename(url: str) -> str:
    """HTML 通道 (Spec F) 渲染出的 PDF 文件名: 按 url 哈希, 不用固定文件名.

    固定文件名会导致同一 job 内出现多个不同 html url 时后一个覆盖前一个的
    渲染产物; 且不缓存 Path 对象本身 (只缓存"已渲染过"这个事实, 见调用点的
    rendered_urls set) -- 自动纠名 (rename_fund_id) 会把 pdf_dir 整个目录
    rename 到新 fund_id 下, 若缓存的是绝对 Path, rename 后就指向不存在的旧
    路径; 每次都从当前 pdf_dir 重新拼路径才不会踩这个坑。
    """
    import hashlib
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"_rendered_{h}.pdf"


def _upsert_fund_preserving_url_type(conn, store_mod, fund_id: str, fund_name: str,
                                     req: IngestRequest) -> None:
    """upsert_fund, 但保留已有 url_type (不被 upsert_fund 默认 'archive' 覆盖
    Coolabah 类 performance_report_html/csv)。

    start_ingest (请求处理同步代码, 让新行立刻对 GET /api/funds 可见, 否则
    表格状态列的角标找不到归属行) 和 _run_ingest_job (worker 线程, 补全
    confirmed_url/apir_code 等) 两处都要用, 抽出来避免各写一份 preserve 逻辑。
    """
    cu = req.confirmed_url or req.issuer_domain or ""
    existing_row = conn.execute(
        "SELECT url_type FROM funds WHERE fund_id=?", (fund_id,)
    ).fetchone()
    preserve_url_type = (existing_row[0] if existing_row else None) or "archive"
    store_mod.upsert_fund(
        conn,
        fund_id=fund_id,
        fund_name=fund_name,
        confirmed_url=cu,
        url_type=preserve_url_type,
        apir_code=req.apir_code,
        max_pdf_pages=req.max_pdf_pages,
    )


def _import_llm_ingest_store():
    """把仓库根加进 sys.path (若尚未在), 返回 llm_ingest.store 模块.

    llm_ingest 在仓库根, webapp/backend 进程默认 sys.path 不含它。start_ingest
    (请求处理同步代码) 与 _run_ingest_job (worker 线程) 都需要 store.slugify_fund_id
    /rename_fund_id, 抽出这一份 bootstrap 避免两处各写一遍。
    """
    import os
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
    )
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from llm_ingest import store as store_mod
    return store_mod


router = APIRouter(tags=["ingest"])


# ---------- 摄取核心 (在 worker 线程调用) ----------

def _trigger_recompute_if_needed(
    jid: str, fund_id: str, stats: Dict[str, int], wrote_any: bool,
) -> None:
    """L1 或 L2 只要写了新月度就触发既有 recompute."""
    if not (stats.get("monthly", 0) > 0 or wrote_any):
        return
    _job_log(jid, "recompute metrics ...")
    try:
        from app.database import SessionLocal
        from app.metrics_pipeline import compute_and_store_metrics
        sess = SessionLocal()
        try:
            compute_and_store_metrics(sess, fund_id)
            _job_log(jid, "recompute ok")
        except ValueError as e:
            _job_log(jid, f"recompute skip: {e}")
        finally:
            sess.close()
    except Exception as e:  # noqa: BLE001
        _job_log(jid, f"recompute failed (ingest 已成功): {e}")


def _run_ingest_job(jid: str, req: IngestRequest) -> None:
    """worker 线程主循环 (Spec B: L1=fundmonitors 主源, L2=PDF fallback).

    步骤:
      1. upsert_fund (需在 L1 UPDATE discovered_source_name 前建行).
      2. L1: fundmonitors.probe (fund_id+db_conn 走白名单短路; 否则 Tavily).
         L1 status=ok -> write_table_records + UPDATE discovered_source_name +
         触发 recompute + return, 跳过 L2 PDF 通路.
      3. L2 PDF fallback: 老 L1 discovery + 循环 + 提取 + 两闸 (仅 L1 未覆盖时).
      4. 成功后触发既有 recompute.
    """
    try:
        _job_update(jid, state="ingesting_l1_fundmonitors",
                    started_at=datetime.utcnow().isoformat())
        _job_log(jid, "job start")

        # 懒 import: llm_ingest 在仓库根 (_import_llm_ingest_store 顺带把仓库根
        # 加进 sys.path, 后面几个 llm_ingest 子模块 import 才不会失败)
        store_mod = _import_llm_ingest_store()
        from llm_ingest import cli as llm_cli
        from llm_ingest import discover as disc_mod
        from llm_ingest import extract as ex_mod
        from llm_ingest import fundmonitors as fm_mod
        from llm_ingest import html_to_pdf as html_to_pdf_mod
        from llm_ingest import parsers as parsers_mod
        from llm_ingest import pdf as pdf_mod
        from llm_ingest import verify

        # ---- upsert fund (提前, 用于 L1 UPDATE discovered_source_name) ----
        conn = store_mod.open_conn()
        store_mod.ensure_tables_if_missing(conn)
        # 幂等迁移: lifespan 没跑 (直调 job / 单测) 场景下自补一次
        try:
            from llm_ingest.migrations import spec_b_20260717 as _mig_b
            _mig_b.apply(conn)
        except Exception:  # noqa: BLE001
            pass
        _upsert_fund_preserving_url_type(conn, store_mod, req.fund_id, req.fund_name, req)
        _job_log(jid, f"upsert_fund: {req.fund_id}")

        # ---- L1: fundmonitors 主源 (Spec B 反转优先级) ----
        _job_log(jid, f"L1 fundmonitors: probing (engine={req.search_engine}) ...")
        l1_result: Dict[str, Any] = {"status": "skipped"}
        try:
            l1_result = fm_mod.probe(
                req.fund_name, fund_id=req.fund_id, db_conn=conn,
                engine=req.search_engine,
            )
        except Exception as e:  # noqa: BLE001
            l1_result = {"status": f"exception:{type(e).__name__}",
                         "page_fund_name": None, "records": [],
                         "url": None, "errors": [str(e)]}
        _job_log(jid, f"L1 fundmonitors: status={l1_result.get('status')}, "
                      f"records={len(l1_result.get('records', []))}, "
                      f"page_name={l1_result.get('page_fund_name')}")

        stats = {"monthly": 0, "pending": 0, "gap": 0, "download_fail": 0}

        if l1_result.get("status") == "ok":
            n_written = store_mod.write_table_records(
                conn, fund_id=req.fund_id,
                records=l1_result["records"], source_url=l1_result["url"],
            )
            _job_log(jid, f"L1 fundmonitors: {n_written} 月入库")
            # 记 discovered_source_name (供前端透明展示)
            conn.execute(
                "UPDATE funds SET discovered_source_name=? WHERE fund_id=?",
                (l1_result.get("page_fund_name"), req.fund_id),
            )
            conn.commit()

            # ---- 自动纠名: page_fund_name 已过 _name_matches 闸 (probe 内部),
            # 与用户输入名字面关联可控, 借此把 fund_id/fund_name 换成正式值 ----
            page_fund_name = l1_result.get("page_fund_name")
            if page_fund_name:
                new_id = store_mod.slugify_fund_id(page_fund_name)
                if new_id != req.fund_id:
                    if store_mod.rename_fund_id(conn, req.fund_id, new_id, page_fund_name):
                        old_id = req.fund_id
                        req = req.model_copy(update={"fund_id": new_id, "fund_name": page_fund_name})
                        _job_update(jid, fund_id=new_id)
                        from pathlib import Path as _Path
                        old_dir = _Path(llm_cli.PDF_ROOT) / old_id
                        new_dir = _Path(llm_cli.PDF_ROOT) / new_id
                        if old_dir.exists():
                            old_dir.rename(new_dir)
                        _job_log(jid, f"renamed: {old_id} -> {new_id}")
                    else:
                        _job_log(jid, f"rename_skipped: {new_id} 已被占用")

            stats["monthly"] = n_written
            _job_log(jid, "L1 覆盖成功, 跳过 L2 PDF 通路")
            conn.close()
            _job_update(jid, stats=stats)
            _trigger_recompute_if_needed(jid, req.fund_id, stats, True)
            _job_update(jid, state="succeeded",
                        finished_at=datetime.utcnow().isoformat())
            _job_log(jid, f"done: {stats}")
            return  # Spec B: L1 覆盖成功即 return, 无 L2 补差

        _job_log(jid, "L1 未覆盖, 走 L2 PDF 通路 ...")

        # ---- L2: 官网 discovery + 循环 (原 L1 通路降级为 L2) ----
        _job_update(jid, state="discovering_l2_pdf")
        links: List[tuple] = []
        # Spec D: 单文件多月 HTML/CSV 场景 (Coolabah Plotly 类)
        # 预抓一次, 缓存到 payload_cache; 每月复用
        payload_cache: Dict[str, str] = {}  # url -> text (CSV)
        from pathlib import Path
        pdf_dir = Path(llm_cli.PDF_ROOT) / req.fund_id
        # Spec F: 记录本 job 内已渲染过的 url (只渲染 1 次, 见 docs/superpowers/
        # plans/2026-07-20-html-rendered-pdf-channel.md) -- 只缓存"已渲染过"
        # 这个事实, 不缓存 Path 本身 (原因见 _rendered_pdf_filename 注释)。
        rendered_urls: set = set()
        if req.confirmed_url:
            _cu_low = req.confirmed_url.lower().split("?", 1)[0]
            # 分派: (1) 扩展名 .html/.htm/.csv 或 (2) funds.url_type 值为
            # performance_report_html / performance_report_csv (Coolabah 无扩
            # 展的 Plotly 页) 均走 single_file_multi_month
            _url_type_row = conn.execute(
                "SELECT url_type FROM funds WHERE fund_id=?", (req.fund_id,)
            ).fetchone()
            _url_type = (_url_type_row[0] if _url_type_row else "") or ""
            _is_single_file_html = (
                _cu_low.endswith((".html", ".htm", ".csv"))
                or _url_type in ("performance_report_html", "performance_report_csv")
            )
            if _is_single_file_html and req.inception_month:
                # 单文件多月: 从 inception 到当前 (下月-1) 枚举 ym
                _job_log(jid, f"single_file_multi_month: {req.confirmed_url}")
                from datetime import datetime as _dt
                _today = _dt.utcnow()
                # 目标最近月末 = 上个月 (确保数据已发布)
                _end_ym_dt = _today.replace(day=1)
                # 回退一月
                if _end_ym_dt.month == 1:
                    _end_ym_dt = _end_ym_dt.replace(year=_end_ym_dt.year - 1, month=12)
                else:
                    _end_ym_dt = _end_ym_dt.replace(month=_end_ym_dt.month - 1)
                _end_ym = f"{_end_ym_dt.year:04d}-{_end_ym_dt.month:02d}"
                # inception_month 本身没有"上月 NAV"可比, 收益率结构上算不出来
                # (不是搜不到, 是这个月不该被问) -- 枚举从 inception 次月起,
                # 不然会被下面 extract 失败误记成 confirmed_gap (2026-07-20 修)
                all_months = disc_mod._month_range(req.inception_month, _end_ym)
                months = all_months[1:] if len(all_months) > 1 else []
                links = [(m, req.confirmed_url) for m in months]
                _job_log(jid, f"single_file_multi_month: {len(links)} months to try")
                _cu_is_html_channel = (
                    _cu_low.endswith((".html", ".htm"))
                    or _url_type == "performance_report_html"
                )
                if _cu_is_html_channel:
                    # Spec F: 硬约束通道, 渲染一次 PDF 供全部月份复用 (不再对
                    # 原始 HTML 文本做预抓缓存 -- 渲染步骤自己会 goto 该 url,
                    # 提前多抓一份原始文本纯属浪费带宽)
                    _job_log(jid, f"pre-rendering HTML -> PDF: {req.confirmed_url}")
                    _rendered_path = pdf_dir / _rendered_pdf_filename(req.confirmed_url)
                    try:
                        html_to_pdf_mod.render_html_to_pdf(req.confirmed_url, _rendered_path)
                    except Exception as e:  # noqa: BLE001
                        raise ValueError(f"HTML 渲染失败 {req.confirmed_url}: {e}")
                    rendered_urls.add(req.confirmed_url)
                    _job_log(jid, f"pre-rendered PDF, {len(links)} months to try")
                else:
                    # 预抓 (CSV 通道)
                    _text = disc_mod._fetch(req.confirmed_url) or ""
                    if not _text:
                        raise ValueError(f"无法抓取 {req.confirmed_url}")
                    payload_cache[req.confirmed_url] = _text
                    _job_log(jid, f"pre-fetched {len(_text)} chars, {len(links)} months")
            else:
                _job_log(jid, f"parse_archive: {req.confirmed_url}")
                html = disc_mod._fetch(req.confirmed_url)
                if not html:
                    raise ValueError(f"无法抓取归档页 {req.confirmed_url}")
                parsed_links, has_more, hint, unp = disc_mod.parse_archive_page(html)
                links = [(str(ym), str(url)) for ym, url in parsed_links]
                _job_log(jid, f"parse_archive: {len(links)} links, unparseable={unp}, more_pages={has_more}")
        else:
            issuer_for_search = req.issuer or req.fund_name
            _job_log(jid, f"run_discovery: issuer={issuer_for_search}")
            rep = disc_mod.run_discovery(
                fund_name=req.fund_name,
                issuer=issuer_for_search,
                fund_id=req.fund_id,
                issuer_domain=req.issuer_domain,
                asx_code=req.asx_code,
                engine=req.search_engine,
            )
            links = rep.links
            # Spec G 4.5: 引擎降级必须可见 -- evidence_log 之外还要写 job 日志
            for _e in rep.evidence_log:
                _loc = _e.get("locate") or {}
                if _loc.get("engine_requested") and \
                        _loc.get("engine_used") != _loc.get("engine_requested"):
                    _job_log(
                        jid,
                        f"engine fallback: {_loc['engine_requested']} -> "
                        f"{_loc['engine_used']} ({_loc.get('fallback_reason', '')})",
                    )
            _job_log(jid, f"discovery: {len(links)} links, gaps={len(rep.gaps)}")

            # rep.gaps 是 discovery 阶段就确定"预期月份里压根没找到任何 PDF 链接"
            # 的月份 (expected - obtained 集合差) -- 与下面 per-link 循环里的
            # record_confirmed_gap (下载失败/抓取失败等, 只覆盖已有链接的月份)
            # 是互斥的两类缺口, 谁都不会覆盖这些月份, 之前从未被记进
            # confirmed_gaps, 违反 CLAUDE.md 零容忍规则 (2026-07 Stake 2025-09
            # 缺口静默漏记事故)。若之后 L2 触发自动纠名, rename_fund_id 会把
            # confirmed_gaps 整表迁到新 fund_id, 这里不用等纠名完成再记。
            if links:
                for _gap_ym in rep.gaps:
                    store_mod.record_confirmed_gap(
                        conn, fund_id=req.fund_id, missing_month=_gap_ym,
                        exhausted_levels="no_link_found",
                    )
                if rep.gaps:
                    _job_log(jid, f"discovery gaps 记入 confirmed_gaps: {len(rep.gaps)} 月")

        if not links:
            conn.close()
            raise ValueError("discovery 未产出任何 PDF 链接, 摄取无法进行")

        if req.limit:
            links = links[: req.limit]

        # ---- L2 数据源 ingest 循环 (PDF/HTML/CSV 3 通道) ----
        _job_update(jid, state="ingesting_l2_pdf")
        rename_attempted = False  # 只在本 job 内尝试一次自动纠名 (L2 通路)

        for i, (ym, url) in enumerate(links, 1):
            # 按 URL 后缀分派通道 (Spec D); 无后缀 URL 兜底看 funds.url_type
            low_url = url.lower()
            u_no_qs = low_url.split("?", 1)[0]
            if u_no_qs.endswith(".csv"):
                channel = "csv"
            elif u_no_qs.endswith((".html", ".htm")):
                channel = "html"
            elif locals().get("_url_type") == "performance_report_html":
                channel = "html"
            elif locals().get("_url_type") == "performance_report_csv":
                channel = "csv"
            else:
                # 默认 PDF (无后缀 / .pdf / file://.../.pdf 都当 PDF)
                channel = "pdf"

            pdf_path = pdf_dir / f"{ym}.pdf"  # 仅 PDF 通道用
            payload_text: Optional[str] = None  # CSV 通道装载 (HTML 通道走渲染 PDF, 见下)

            if channel == "pdf":
                # Spec C1: file:// 表示 run_discovery L2.6 本地缓存兜底
                if url.startswith("file://"):
                    from pathlib import Path as _Path
                    local_p = _Path(url[7:])
                    if not local_p.exists():
                        stats["download_fail"] += 1
                        store_mod.record_confirmed_gap(
                            conn, fund_id=req.fund_id, missing_month=ym,
                            exhausted_levels="local_cache_missing",
                        )
                        _job_log(jid, f"[{i}/{len(links)}] {ym} local cache MISSING {local_p}")
                        continue
                    pdf_path = local_p
                elif not pdf_path.exists():
                    ok = llm_cli._download_pdf(url, pdf_path)
                    if not ok:
                        stats["download_fail"] += 1
                        store_mod.record_confirmed_gap(
                            conn, fund_id=req.fund_id, missing_month=ym,
                            exhausted_levels="download_fail",
                        )
                        _job_log(jid, f"[{i}/{len(links)}] {ym} download FAIL")
                        continue
            elif channel == "html":
                # Spec F: HTML 硬约束通道 -- 渲染成 PDF (代码层, 非 LLM 判断),
                # 不再对原始 HTML 文本做字节窗口切片 (extract_html.py 旧法认错
                # 表格的根因, 见 PDD)。同一 url 只渲染一次, job 内缓存复用。
                # 路径每次从当前 pdf_dir 重新拼 (不缓存绝对 Path) -- 自动纠名
                # 可能把 pdf_dir 整个 rename 到新 fund_id, 缓存的旧 Path 会失效。
                pdf_path = pdf_dir / _rendered_pdf_filename(url)
                if url not in rendered_urls:
                    try:
                        html_to_pdf_mod.render_html_to_pdf(url, pdf_path)
                    except Exception as e:  # noqa: BLE001
                        stats["download_fail"] += 1
                        store_mod.record_confirmed_gap(
                            conn, fund_id=req.fund_id, missing_month=ym,
                            exhausted_levels="html_render_fail",
                        )
                        _job_log(jid, f"[{i}/{len(links)}] {ym} html_render FAIL: {e}")
                        continue
                    rendered_urls.add(url)
            else:
                # CSV 通道: 抓取文本到内存 (可能过大, 上限由 extract_from_source 内部截)
                if url in payload_cache:
                    payload_text = payload_cache[url]
                elif url.startswith("file://"):
                    from pathlib import Path as _Path
                    local_p = _Path(url[7:])
                    if not local_p.exists():
                        stats["download_fail"] += 1
                        store_mod.record_confirmed_gap(
                            conn, fund_id=req.fund_id, missing_month=ym,
                            exhausted_levels=f"local_{channel}_missing",
                        )
                        _job_log(jid, f"[{i}/{len(links)}] {ym} local {channel} MISSING {local_p}")
                        continue
                    payload_text = local_p.read_text(encoding="utf-8", errors="replace")
                    payload_cache[url] = payload_text
                else:
                    # 用 discover._fetch 与 discovery 层一致的 UA/超时
                    try:
                        from llm_ingest.discover import _fetch as _fetch_text
                        payload_text = _fetch_text(url) or ""
                    except Exception as e:  # noqa: BLE001
                        payload_text = ""
                        _job_log(jid, f"[{i}/{len(links)}] {ym} fetch {channel} ERR: {e}")
                    if not payload_text:
                        stats["download_fail"] += 1
                        store_mod.record_confirmed_gap(
                            conn, fund_id=req.fund_id, missing_month=ym,
                            exhausted_levels=f"{channel}_fetch_fail",
                        )
                        _job_log(jid, f"[{i}/{len(links)}] {ym} fetch {channel} EMPTY")
                        continue
                    payload_cache[url] = payload_text

            try:
                if channel in ("pdf", "html"):
                    # HTML 通道已在上面渲染成 PDF (Spec F), 走同一 PDF 提取
                    # 通道: max_pages=0 (全文) -- 页级裁剪会复现"字节窗口切片"
                    # 同一类 bug (裁剪后看不到附录里的真实月份数据), 不能沿用
                    # 常规 PDF 通道的 2 页默认值。
                    ex = ex_mod.extract_from_pdf(
                        pdf_path,
                        ym,
                        max_pages=(req.max_pdf_pages or 2) if channel == "pdf" else 0,
                        fund_name=req.fund_name, issuer=req.issuer or "",
                    )
                else:
                    ex = ex_mod.extract_from_source(
                        url, ym,
                        csv_text=payload_text,
                        fund_name=req.fund_name, issuer=req.issuer or "",
                    )
            except Exception as e:  # noqa: BLE001
                store_mod.record_confirmed_gap(
                    conn, fund_id=req.fund_id, missing_month=ym,
                    exhausted_levels=f"api_error:{type(e).__name__}",
                )
                _job_log(jid, f"[{i}/{len(links)}] {ym} extract ERR: {e}")
                continue

            # 反捏造两道闸: PDF/HTML(渲染后) 走 pdf_text, CSV 走 payload_text
            if channel in ("pdf", "html"):
                source_text = pdf_mod.full_text(pdf_path)
            else:
                source_text = payload_text or ""

            # ---- 自动纠名 (L2 通路, 只尝试一次): fund_name_text 独立走
            # check_fund_name_token (只需是 doc_text 子串, 不进闸1/闸2, 不影响
            # 上面数值提取), 再用身份闸做同页多基金混淆的兜底防线 ----
            # Spec G 10.4: 纠名判据由 fm_mod._name_matches (去停用词后交集非空)
            # 换成 verify.check_fund_identity。前者停用词表已排除 income/enhanced/
            # capital/australian, "Yarra Enhanced Income" 与 "Yarra Australian
            # Income" 双方都只剩 {yarra} -> 交集非空 -> 放行, 会把整支基金改名
            # 迁移到兄弟基金名下 (单份错数据升级为整支基金身份错乱)。
            # 不通过时不纠名也不阻断, 仅写 discovered_source_name 供前端人工核对。
            if not rename_attempted and ex.fund_name_text and not ex.not_found:
                name_ok = verify.check_fund_name_token(ex.fund_name_text, source_text)
                ident_ok = verify.check_fund_identity(ex.fund_name_text, req.fund_name)
                if not ident_ok.passed:
                    _job_log(jid, f"rename_skipped: {ident_ok.reason}")
                    conn.execute(
                        "UPDATE funds SET discovered_source_name=? WHERE fund_id=?",
                        (ex.fund_name_text, req.fund_id),
                    )
                    conn.commit()
                if name_ok.passed and ident_ok.passed:
                    conn.execute(
                        "UPDATE funds SET discovered_source_name=? WHERE fund_id=?",
                        (ex.fund_name_text, req.fund_id),
                    )
                    conn.commit()
                    new_id = store_mod.slugify_fund_id(ex.fund_name_text)
                    if new_id != req.fund_id:
                        if store_mod.rename_fund_id(conn, req.fund_id, new_id, ex.fund_name_text):
                            old_id = req.fund_id
                            req = req.model_copy(update={"fund_id": new_id, "fund_name": ex.fund_name_text})
                            _job_update(jid, fund_id=new_id)
                            old_dir, new_dir = pdf_dir, Path(llm_cli.PDF_ROOT) / new_id
                            if old_dir.exists():
                                old_dir.rename(new_dir)
                            pdf_dir = new_dir
                            _job_log(jid, f"renamed: {old_id} -> {new_id}")
                        else:
                            _job_log(jid, f"rename_skipped: {new_id} 已被占用")
                rename_attempted = True

            q = verify.check_quote_tokens(
                parsers_mod.collect_text_tokens(ex.raw),
                ex.source_quote,
                source_text,
            )
            history = store_mod.load_monthly_history(conn, req.fund_id)
            r = verify.check_rolling(ex.net_return, ex.ym, history, ex.rolling)
            # 闸 5 (Spec G 10.5): 逐份核对文档抬头基金名与目标基金是否同一支。
            # 不通过 -> write_extraction 判 pending_review 转人工, 既不静默入库
            # 也不静默丢弃 (CLAUDE.md: 宁可报错停下; 自动丢弃会造成静默缺失)。
            identity = verify.check_fund_identity(ex.fund_name_text, req.fund_name)
            if not identity.passed:
                _job_log(jid, f"[{i}/{len(links)}] {ym} identity FAIL: {identity.reason}")
            dec = store_mod.write_extraction(
                conn, fund_id=req.fund_id, ex=ex,
                quote_check=q, rolling_check=r, identity_check=identity,
                monthly_history=history,
            )
            stats[dec.action] = stats.get(dec.action, 0) + 1
            _job_log(jid, f"[{i}/{len(links)}] {ym} [{channel}] {dec.action} {dec.gate_summary}")

        conn.close()
        _job_update(jid, stats=stats)

        _trigger_recompute_if_needed(jid, req.fund_id, stats, False)

        _job_update(jid, state="succeeded",
                    finished_at=datetime.utcnow().isoformat())
        _job_log(jid, f"done: {stats}")
    except Exception as e:  # noqa: BLE001
        _job_update(jid, state="failed", error=str(e),
                    finished_at=datetime.utcnow().isoformat())
        _job_log(jid, f"FAIL: {e}")


# ---------- 路由 ----------

@router.post("/api/ingest/funds", response_model=IngestJobResponse,
             status_code=status.HTTP_202_ACCEPTED)
def start_ingest(req: IngestRequest):
    """起 LLM 摄取任务. 返回 job_id 供轮询. 前端弹窗提交后轮询直到成功/失败.

    唯一硬要求: fund_name 非空。fund_id 缺省 -> slugify(fund_name)。
    issuer / confirmed_url 都留空时用 fund_name 自身作为搜索关键词。
    """
    fund_name = (req.fund_name or "").strip()
    if not fund_name:
        raise HTTPException(status_code=400, detail="fund_name 必填")
    store_mod = _import_llm_ingest_store()
    fund_id = (req.fund_id or "").strip() or store_mod.slugify_fund_id(fund_name)
    # 回填 req 让下游 worker 一致 (pydantic v2 model_copy)
    req = req.model_copy(update={"fund_id": fund_id, "fund_name": fund_name})

    # 同步 upsert 一次: 让这一行立刻对 GET /api/funds 可见, 否则表格状态列
    # (轮询 /api/ingest/jobs/active 按 fund_id 关联) 在 worker 线程真正跑到
    # upsert_fund 之前的整段时间里找不到归属行, 新加的基金看起来"消失"了。
    conn = store_mod.open_conn()
    store_mod.ensure_tables_if_missing(conn)
    _upsert_fund_preserving_url_type(conn, store_mod, fund_id, fund_name, req)
    conn.close()

    jid = _job_new(fund_id)
    t = threading.Thread(target=_run_ingest_job, args=(jid, req), daemon=True,
                         name=f"ingest-{jid}")
    t.start()
    with _JOBS_LOCK:
        return IngestJobResponse(**_JOBS[jid])


@router.get("/api/ingest/jobs/active")
def list_active_jobs():
    """列当前非终态 job, 供前端表格级轮询把活跃摄取状态关联到基金行.

    必须注册在 `/api/ingest/jobs/{job_id}` 之前 -- 否则 Starlette 按注册顺序
    匹配, 会把 'active' 当成 job_id 传进那个动态路由, 永远 404。
    """
    terminal = {"succeeded", "failed"}
    with _JOBS_LOCK:
        return [
            {"job_id": j["job_id"], "fund_id": j["fund_id"], "state": j["state"]}
            for j in _JOBS.values() if j["state"] not in terminal
        ]


@router.get("/api/ingest/jobs/{job_id}", response_model=IngestJobResponse)
def get_job(job_id: str):
    with _JOBS_LOCK:
        j = _JOBS.get(job_id)
        if j is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} 不存在")
        return IngestJobResponse(**j)


@router.get("/api/pending", response_model=List[PendingReviewResponse])
def list_pending(fund_id: Optional[str] = None,
                 session: Session = Depends(get_db)):
    """列 pending_review 待人工审核记录 (default state='pending')."""
    q = session.query(PendingReview, Fund.fund_name).outerjoin(
        Fund, PendingReview.fund_id == Fund.fund_id
    ).filter(PendingReview.review_state == "pending")
    if fund_id:
        q = q.filter(PendingReview.fund_id == fund_id)
    q = q.order_by(PendingReview.created_at.desc())
    out: List[PendingReviewResponse] = []
    for pr, fname in q.all():
        out.append(PendingReviewResponse(
            id=pr.id, fund_id=pr.fund_id, fund_name=fname,
            date=pr.date, net_return=pr.net_return,
            source_quote=pr.source_quote, extract_method=pr.extract_method,
            gate_result=pr.gate_result, review_state=pr.review_state,
            review_reason=pr.review_reason,
            candidates_json=pr.candidates_json, created_at=pr.created_at,
        ))
    return out


@router.patch("/api/pending/{review_id}/approve")
def approve_pending(review_id: int):
    """人工审核通过. 走 llm_ingest.store.promote_pending (含 NAV 重算).

    返回 info["action"]:
      - 'approved': pending 值已入 monthly_returns, 触发指标重算
      - 'skipped_authoritative_covered': 该月已由权威源 (L3 fundmonitors 表 或 L1 LLM
        PDF 通路) 覆盖, pending 未采纳 (标 rejected), monthly_returns 不动, 不重算。
        额外附 existing_tag 告诉前端是被哪种权威源挡的。
    """
    from llm_ingest import store as store_mod
    conn = store_mod.open_conn()
    try:
        info = store_mod.promote_pending(conn, review_id)
    except KeyError:
        conn.close()
        raise HTTPException(status_code=404, detail=f"pending id={review_id} 不存在")
    conn.close()
    # 权威源已覆盖场景: 前端展示"未采纳"提示, 不重算
    if info.get("action") == "skipped_authoritative_covered":
        return {"ok": True, "fund_id": info["fund_id"], "date": info["date"],
                "action": "skipped_authoritative_covered",
                "existing_tag": info["existing_tag"],
                "message": f"该月已由权威源 ({info['existing_tag']}) 覆盖, pending 未采纳"}
    # 触发指标重算
    try:
        from app.database import SessionLocal
        from app.metrics_pipeline import compute_and_store_metrics
        sess = SessionLocal()
        try:
            compute_and_store_metrics(sess, info["fund_id"])
        except ValueError:
            pass
        finally:
            sess.close()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "fund_id": info["fund_id"], "date": info["date"],
            "action": "approved"}


@router.patch("/api/pending/{review_id}/reject")
def reject_pending(review_id: int, reason: str = ""):
    from llm_ingest import store as store_mod
    conn = store_mod.open_conn()
    store_mod.reject_pending(conn, review_id, reason=reason)
    conn.close()
    return {"ok": True}
