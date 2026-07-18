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
import re
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


def _slugify_fund_id(name: str) -> str:
    """基金名 -> fund_id slug. 小写, 非字母数字 -> 下划线, 去首尾, 折叠连续下划线。

    例: 'Bentham Global Income Fund' -> 'bentham_global_income_fund'
    """
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    s = re.sub(r"_+", "_", s)
    return s or "fund"


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

        # 懒 import: llm_ingest 在仓库根
        import os
        _repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
        )
        if _repo_root not in sys.path:
            sys.path.insert(0, _repo_root)
        from llm_ingest import cli as llm_cli
        from llm_ingest import discover as disc_mod
        from llm_ingest import extract as ex_mod
        from llm_ingest import fundmonitors as fm_mod
        from llm_ingest import parsers as parsers_mod
        from llm_ingest import pdf as pdf_mod
        from llm_ingest import store as store_mod
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
        cu = req.confirmed_url or req.issuer_domain or ""
        # 保留已有 url_type (Coolabah performance_report_html 等)
        _existing_row = conn.execute(
            "SELECT url_type FROM funds WHERE fund_id=?", (req.fund_id,)
        ).fetchone()
        _preserve_url_type = (_existing_row[0] if _existing_row else None) or "archive"
        store_mod.upsert_fund(
            conn,
            fund_id=req.fund_id,
            fund_name=req.fund_name,
            confirmed_url=cu,
            url_type=_preserve_url_type,
            apir_code=req.apir_code,
            max_pdf_pages=req.max_pdf_pages,
        )
        _job_log(jid, f"upsert_fund: {req.fund_id}")

        # ---- L1: fundmonitors 主源 (Spec B 反转优先级) ----
        _job_log(jid, "L1 fundmonitors: probing ...")
        l1_result: Dict[str, Any] = {"status": "skipped"}
        try:
            l1_result = fm_mod.probe(req.fund_name, fund_id=req.fund_id, db_conn=conn)
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
        payload_cache: Dict[str, str] = {}  # url -> text (HTML/CSV)
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
                months = disc_mod._month_range(req.inception_month, _end_ym)
                links = [(m, req.confirmed_url) for m in months]
                _job_log(jid, f"single_file_multi_month: {len(links)} months to try")
                # 预抓
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
            )
            links = rep.links
            _job_log(jid, f"discovery: {len(links)} links, gaps={len(rep.gaps)}")

        if not links:
            conn.close()
            raise ValueError("discovery 未产出任何 PDF 链接, 摄取无法进行")

        if req.limit:
            links = links[: req.limit]

        # ---- L2 数据源 ingest 循环 (PDF/HTML/CSV 3 通道) ----
        _job_update(jid, state="ingesting_l2_pdf")
        from pathlib import Path
        pdf_dir = Path(llm_cli.PDF_ROOT) / req.fund_id

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
            payload_text: Optional[str] = None  # HTML/CSV 通道装载

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
            else:
                # HTML/CSV 通道: 抓取文本到内存 (可能过大, 上限由 extract_from_source 内部截)
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
                if channel == "pdf":
                    ex = ex_mod.extract_from_pdf(
                        pdf_path, ym, max_pages=req.max_pdf_pages or 2,
                        fund_name=req.fund_name, issuer=req.issuer or "",
                    )
                else:
                    ex = ex_mod.extract_from_source(
                        url, ym,
                        html_text=payload_text if channel == "html" else None,
                        csv_text=payload_text if channel == "csv" else None,
                        fund_name=req.fund_name, issuer=req.issuer or "",
                    )
            except Exception as e:  # noqa: BLE001
                store_mod.record_confirmed_gap(
                    conn, fund_id=req.fund_id, missing_month=ym,
                    exhausted_levels=f"api_error:{type(e).__name__}",
                )
                _job_log(jid, f"[{i}/{len(links)}] {ym} extract ERR: {e}")
                continue

            # 反捏造两道闸: PDF 走 pdf_text, HTML/CSV 走 payload_text
            if channel == "pdf":
                source_text = pdf_mod.full_text(pdf_path)
            else:
                source_text = payload_text or ""
            q = verify.check_quote_tokens(
                parsers_mod.collect_text_tokens(ex.raw),
                ex.source_quote,
                source_text,
            )
            history = store_mod.load_monthly_history(conn, req.fund_id)
            r = verify.check_rolling(ex.net_return, ex.ym, history, ex.rolling)
            dec = store_mod.write_extraction(
                conn, fund_id=req.fund_id, ex=ex,
                quote_check=q, rolling_check=r,
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
    fund_id = (req.fund_id or "").strip() or _slugify_fund_id(fund_name)
    # 回填 req 让下游 worker 一致 (pydantic v2 model_copy)
    req = req.model_copy(update={"fund_id": fund_id, "fund_name": fund_name})
    jid = _job_new(fund_id)
    t = threading.Thread(target=_run_ingest_job, args=(jid, req), daemon=True,
                         name=f"ingest-{jid}")
    t.start()
    with _JOBS_LOCK:
        return IngestJobResponse(**_JOBS[jid])


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
