"""基金 CRUD + 手动重算路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crud import create_fund, get_all_funds, get_fund, delete_fund
from app.database import get_db
from app.models import Fund, FundMetric, MonthlyReturn, ConfirmedGap, PendingReview
from app.metrics_pipeline import compute_and_store_metrics
from app.schemas import FundCreate, FundResponse, sanitize_for_json

router = APIRouter(prefix="/api/funds", tags=["funds"])


def _to_response(fund: Fund, session: Session, gap_count: int | None = None,
                 pending_count: int | None = None) -> FundResponse:
    """ORM Fund -> FundResponse，附带数据截止年月与是否有指标。

    data_cutoff_month 直接取最新 monthly_return 月份，不用 fund_metrics 缓存值
    （缓存可能在 skills 摄取新月份后过期）。
    gap_count / pending_count 由调用方批量查询传入避免 N+1；None 时单基金即时查。
    """
    metric = session.get(FundMetric, fund.fund_id)
    latest = (session.query(MonthlyReturn)
              .filter_by(fund_id=fund.fund_id)
              .order_by(MonthlyReturn.date.desc()).first())
    cutoff = latest.date[:7] if latest else None
    if gap_count is None:
        gap_count = session.query(ConfirmedGap).filter_by(fund_id=fund.fund_id).count()
    if pending_count is None:
        pending_count = (session.query(PendingReview)
                         .filter_by(fund_id=fund.fund_id, review_state="pending")
                         .count())
    return FundResponse(
        fund_id=fund.fund_id, fund_name=fund.fund_name, apir_code=fund.apir_code,
        confirmed_url=fund.confirmed_url, fetch_method=fund.fetch_method,
        url_type=fund.url_type, max_pdf_pages=fund.max_pdf_pages,
        data_cutoff_month=cutoff, has_metrics=metric is not None,
        gap_count=gap_count, pending_count=pending_count,
        discovered_source_name=fund.discovered_source_name,
    )


@router.get("", response_model=list[FundResponse])
def list_funds(session: Session = Depends(get_db)):
    funds = get_all_funds(session)
    # 一次 group_by 查全量 gap 计数，避免逐基金 N+1
    gap_rows = (session.query(ConfirmedGap.fund_id, func.count(ConfirmedGap.id))
                .group_by(ConfirmedGap.fund_id).all())
    gap_map = {fid: cnt for fid, cnt in gap_rows}
    pend_rows = (session.query(PendingReview.fund_id, func.count(PendingReview.id))
                 .filter(PendingReview.review_state == "pending")
                 .group_by(PendingReview.fund_id).all())
    pend_map = {fid: cnt for fid, cnt in pend_rows}
    return [_to_response(f, session,
                         gap_count=gap_map.get(f.fund_id, 0),
                         pending_count=pend_map.get(f.fund_id, 0))
            for f in funds]


@router.post("", response_model=FundResponse, status_code=status.HTTP_201_CREATED)
def add_fund(payload: FundCreate, session: Session = Depends(get_db)):
    try:
        fund = create_fund(
            session, fund_id=payload.fund_id, fund_name=payload.fund_name,
            apir_code=payload.apir_code, confirmed_url=payload.confirmed_url,
            fetch_method=payload.fetch_method, url_type=payload.url_type,
            max_pdf_pages=payload.max_pdf_pages,
        )
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"基金名 '{payload.fund_name}' 已存在")
    return _to_response(fund, session)


@router.delete("/{fund_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_fund(fund_id: str, session: Session = Depends(get_db)):
    if not delete_fund(session, fund_id):
        raise HTTPException(status_code=404, detail=f"基金 {fund_id} 不存在")


@router.post("/{fund_id}/recompute")
def recompute(fund_id: str, session: Session = Depends(get_db)):
    """手动触发该基金 5 维指标重算（含异常检测）。月度数据缺口时返回 400。"""
    if get_fund(session, fund_id) is None:
        raise HTTPException(status_code=404, detail=f"基金 {fund_id} 不存在")
    try:
        metrics = compute_and_store_metrics(session, fund_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return sanitize_for_json(metrics)
