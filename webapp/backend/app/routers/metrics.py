"""metrics 对比与时序路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.crud import get_returns, get_fund, resolve_rf_rates
from app.models import FundMetric
from app.calculations import compute_all_metrics
from app.metrics_pipeline import _find_month_gaps
from app.period import get_common_months, slice_by_period, VALID_PERIODS
from app.schemas import sanitize_for_json

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _recompute_for_slice(session: Session, fund_id: str, period: str,
                         common_months=None) -> dict:
    """从 monthly_returns 切片后即时重算 5 维指标。

    含月份缺口零容忍检查（CLAUDE.md 第一条）：切片后序列若有缺口则拒绝计算。
    """
    ts = get_returns(session, fund_id)
    if not ts:
        raise ValueError(f"基金 {fund_id} 无月度收益数据")
    dates = [d["date"] for d in ts]
    returns = [d["net_return"] for d in ts]
    d_slice, r_slice = slice_by_period(dates, returns, period, common_months)
    if not d_slice:
        raise ValueError(f"基金 {fund_id} 在 period={period} 下无数据")
    # 数据缺口零容忍
    gaps = _find_month_gaps(d_slice)
    if gaps:
        raise ValueError(f"基金 {fund_id} 切片后月份存在缺口: {gaps}")
    rf = resolve_rf_rates(session, d_slice, fallback_rate=0.0435)
    metrics = compute_all_metrics(r_slice, rf, fund_name=fund_id)
    metrics["fund_id"] = fund_id
    metrics["date_period"] = d_slice[-1][:7]
    return metrics


@router.get("/compare")
def compare(fund_ids: str = Query(...),
            period: str = Query("full"),
            session: Session = Depends(get_db)):
    """5 维指标对比。full 读 fund_metrics 缓存；3y/1y/common 切片即时重算。"""
    if period not in VALID_PERIODS:
        raise HTTPException(status_code=422, detail=f"period 须为 {VALID_PERIODS}")
    ids = [s.strip() for s in fund_ids.split(",") if s.strip()]
    if not ids:
        raise HTTPException(status_code=422, detail="fund_ids 不能为空")

    for fid in ids:
        if get_fund(session, fid) is None:
            raise HTTPException(status_code=404, detail=f"基金 {fid} 不存在")

    common_months = None
    if period == "common":
        all_dates = [[d["date"] for d in get_returns(session, fid)] for fid in ids]
        common_months = get_common_months(all_dates)

    results = []
    for fid in ids:
        if period == "full":
            m = session.get(FundMetric, fid)
            if m is None:
                # 无预计算指标：即时全量重算
                m_dict = _recompute_for_slice(session, fid, "full")
            else:
                m_dict = {c.name: getattr(m, c.name) for c in m.__table__.columns}
        else:
            m_dict = _recompute_for_slice(session, fid, period, common_months)
        results.append(m_dict)
    return {"period": period, "funds": sanitize_for_json(results)}
