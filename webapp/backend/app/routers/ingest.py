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


router = APIRouter(tags=["ingest"])


# ---------- 摄取核心 (在 worker 线程调用) ----------

def _run_ingest_job(jid: str, req: IngestRequest) -> None:
    """worker 线程主循环. 内部懒 import llm_ingest, 避免影响 uvicorn 启动.

    步骤:
      1. discover: 若给了 confirmed_url 直接当归档页, 否则 run_discovery.
      2. upsert_fund.
      3. 循环 links: 下载 -> 提取 -> 两闸 -> 写库 (monthly / pending / gap).
      4. 成功后触发既有 recompute 逻辑 (调用 metrics_pipeline.compute_and_store_metrics).
    """
    try:
        _job_update(jid, state="discovering", started_at=datetime.utcnow().isoformat())
        _job_log(jid, "job start")

        # 懒 import: llm_ingest 在 sys.path 里 (顶层包)
        from llm_ingest import cli as llm_cli
        from llm_ingest import discover as disc_mod
        from llm_ingest import extract as ex_mod
        from llm_ingest import pdf as pdf_mod
        from llm_ingest import store as store_mod
        from llm_ingest import verify

        # ---- discovery ----
        links: List[tuple] = []
        if req.confirmed_url:
            # 用户填了 URL: 当归档页, 直接 parse_archive_page
            _job_log(jid, f"parse_archive: {req.confirmed_url}")
            html = disc_mod._fetch(req.confirmed_url)
            if not html:
                raise ValueError(f"无法抓取归档页 {req.confirmed_url}")
            parsed_links, has_more, hint, unp = disc_mod.parse_archive_page(html)
            links = [(str(ym), str(url)) for ym, url in parsed_links]
            _job_log(jid, f"parse_archive: {len(links)} links, unparseable={unp}, more_pages={has_more}")
        else:
            if not req.issuer:
                raise ValueError("留空 confirmed_url 时必须给 issuer, 供 Gemini 联网搜索归档页定位")
            _job_log(jid, f"run_discovery: issuer={req.issuer}")
            rep = disc_mod.run_discovery(
                fund_name=req.fund_name,
                issuer=req.issuer,
                fund_id=req.fund_id,
                issuer_domain=req.issuer_domain,
                asx_code=req.asx_code,
            )
            links = rep.links
            _job_log(jid, f"discovery: {len(links)} links, gaps={len(rep.gaps)}")

        if not links:
            raise ValueError("discovery 未产出任何 PDF 链接, 摄取无法进行")

        if req.limit:
            links = links[: req.limit]

        # ---- upsert fund ----
        conn = store_mod.open_conn()
        store_mod.ensure_tables_if_missing(conn)
        # confirmed_url 落库: 优先 req.confirmed_url, 否则用 issuer_domain 或第一个 link
        cu = req.confirmed_url or req.issuer_domain or (links[0][1] if links else "")
        store_mod.upsert_fund(
            conn,
            fund_id=req.fund_id,
            fund_name=req.fund_name,
            confirmed_url=cu,
            apir_code=req.apir_code,
            max_pdf_pages=req.max_pdf_pages,
        )
        _job_log(jid, f"upsert_fund: {req.fund_id}")

        # ---- ingest 循环 ----
        _job_update(jid, state="ingesting")
        stats = {"monthly": 0, "pending": 0, "gap": 0, "download_fail": 0}
        from pathlib import Path
        pdf_dir = Path(llm_cli.PDF_ROOT) / req.fund_id

        for i, (ym, url) in enumerate(links, 1):
            pdf_path = pdf_dir / f"{ym}.pdf"
            if not pdf_path.exists():
                ok = llm_cli._download_pdf(url, pdf_path)
                if not ok:
                    stats["download_fail"] += 1
                    store_mod.record_confirmed_gap(
                        conn, fund_id=req.fund_id, missing_month=ym,
                        exhausted_levels="download_fail",
                    )
                    _job_log(jid, f"[{i}/{len(links)}] {ym} download FAIL")
                    continue
            try:
                ex = ex_mod.extract_from_pdf(
                    pdf_path, ym, max_pages=req.max_pdf_pages or 2,
                )
            except Exception as e:  # noqa: BLE001
                store_mod.record_confirmed_gap(
                    conn, fund_id=req.fund_id, missing_month=ym,
                    exhausted_levels=f"api_error:{type(e).__name__}",
                )
                _job_log(jid, f"[{i}/{len(links)}] {ym} extract ERR: {e}")
                continue

            pdf_text = pdf_mod.full_text(pdf_path)
            q = verify.check_quote(ex.source_quote, pdf_text, ex.net_return)
            history = store_mod.load_monthly_history(conn, req.fund_id)
            r = verify.check_rolling(ex.net_return, ex.ym, history, ex.rolling)
            dec = store_mod.write_extraction(
                conn, fund_id=req.fund_id, ex=ex,
                quote_check=q, rolling_check=r,
                monthly_history=history,
            )
            stats[dec.action] = stats.get(dec.action, 0) + 1
            _job_log(jid, f"[{i}/{len(links)}] {ym} {dec.action} {dec.gate_summary}")

        conn.close()
        _job_update(jid, stats=stats)

        # ---- 自动触发既有 recompute (若有足够月度数据) ----
        if stats["monthly"] > 0:
            _job_log(jid, "recompute metrics ...")
            try:
                from app.database import SessionLocal
                from app.metrics_pipeline import compute_and_store_metrics
                sess = SessionLocal()
                try:
                    compute_and_store_metrics(sess, req.fund_id)
                    _job_log(jid, "recompute ok")
                except ValueError as e:
                    # 月数不足等场景: 摄取仍成功, 只是指标计算跳过
                    _job_log(jid, f"recompute skip: {e}")
                finally:
                    sess.close()
            except Exception as e:  # noqa: BLE001
                _job_log(jid, f"recompute failed (ingest 已成功): {e}")

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
    """起 LLM 摄取任务. 返回 job_id 供轮询. 前端弹窗提交后轮询直到成功/失败."""
    # 前置校验: fund_id 非空 (schemas 已强制)
    if not req.confirmed_url and not req.issuer:
        raise HTTPException(status_code=400,
                            detail="必须至少给 confirmed_url (归档页) 或 issuer (联网搜索)")
    jid = _job_new(req.fund_id)
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
    """人工审核通过. 走 llm_ingest.store.promote_pending (含 NAV 重算)."""
    from llm_ingest import store as store_mod
    conn = store_mod.open_conn()
    try:
        info = store_mod.promote_pending(conn, review_id)
    except KeyError:
        conn.close()
        raise HTTPException(status_code=404, detail=f"pending id={review_id} 不存在")
    conn.close()
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
    return {"ok": True, "fund_id": info["fund_id"], "date": info["date"]}


@router.patch("/api/pending/{review_id}/reject")
def reject_pending(review_id: int, reason: str = ""):
    from llm_ingest import store as store_mod
    conn = store_mod.open_conn()
    store_mod.reject_pending(conn, review_id, reason=reason)
    conn.close()
    return {"ok": True}
