"""RBA 手动刷新 + 历史利率查询路由。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import RbaCashRate
from app.rba import expand_to_monthly, fetch_rba_rate_history, group_rate_periods, upsert_rba_rates
from app.metrics_pipeline import recompute_all_funds

router = APIRouter(prefix="/api/rba", tags=["rba"])


@router.post("/refresh")
def refresh_rba(session: Session = Depends(get_db)):
    """手动触发一次 RBA 利率抓取与入库（抓官方 Cash Rate Target 全历史表）。

    利率变了就级联重算全部基金 fund_metrics 缓存，避免 full 路径继续读旧利率
    算出的缓存值（recompute_all_funds，见 scheduler.py::run_rba_update 同款注释）。
    """
    try:
        history = fetch_rba_rate_history()
        current_month = datetime.now().strftime("%Y-%m")
        monthly = expand_to_monthly(history, through_month=current_month)
        count = upsert_rba_rates(session, monthly)
        recompute_result = recompute_all_funds(session)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RBA 抓取失败: {e}")
    current_rate = history[-1][1] if history else None
    return {"current_rate": current_rate, "upserted": count, **recompute_result}


@router.get("/history")
def rba_history(session: Session = Depends(get_db)) -> list[dict]:
    """按连续相同利率合并成区间返回（如 2026-01~2026-06 4.50%），不逐月列。"""
    rows = session.query(RbaCashRate).order_by(RbaCashRate.date_period).all()
    rates = {r.date_period: r.rate for r in rows}
    return group_rate_periods(rates)
