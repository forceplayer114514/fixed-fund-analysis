"""异常审计与人工纠错路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Anomaly, Fund, MonthlyReturn
from app.crud import upsert_monthly_return
from app.metrics_pipeline import compute_and_store_metrics
from app.schemas import AnomalyResponse, MonthlyReturnPatch, sanitize_for_json

router = APIRouter(prefix="/api", tags=["anomalies"])


@router.get("/anomalies", response_model=list[AnomalyResponse])
def list_anomalies(session: Session = Depends(get_db)):
    """汇总所有基金的异常检测结果，附基金名 + monthly_return_id 供前端审计展示。

    monthly_return_id 按 (fund_id, date) 关联查 MonthlyReturn 主键，供人工纠错
    PATCH /api/monthly-returns/{row_id} 定位。不能用 anomalies.id（独立自增，
    会改写无关基金数据）。
    """
    rows = session.query(Anomaly).order_by(Anomaly.fund_id, Anomaly.date).all()
    out = []
    for a in rows:
        fund = session.get(Fund, a.fund_id)
        # return_outlier：按 (fund_id, date) 精确定位对应的 monthly_returns 行主键供纠错
        # rba_missing：基金该月收益本身存在（缺的是 RBA），纠错无意义，monthly_return_id=None
        mr_id = None
        if a.type != "rba_missing":
            mr = (session.query(MonthlyReturn)
                  .filter_by(fund_id=a.fund_id, date=a.date).first())
            mr_id = mr.id if mr else None
        out.append(AnomalyResponse(
            id=a.id, fund_id=a.fund_id, date=a.date,
            type=a.type, reason=a.reason,
            value=a.value, z_score=a.z_score, threshold_sigma=a.threshold_sigma,
            mean=a.mean, stdev=a.stdev,
            fund_name=fund.fund_name if fund else None,
            monthly_return_id=mr_id,
        ))
    return out


@router.patch("/monthly-returns/{row_id}")
def patch_monthly_return(row_id: int, payload: MonthlyReturnPatch,
                         session: Session = Depends(get_db)):
    """人工纠错：用户主动修改某月净值，随后重算 NAV 与该基金 5 维指标+异常。

    CLAUDE.md 第五条：人工纠错（用户主动）允许，自动纠错禁止。
    """
    mr = session.get(MonthlyReturn, row_id)
    if mr is None:
        raise HTTPException(status_code=404, detail=f"monthly_return id={row_id} 不存在")
    # 人工纠错净值（upsert 内部会重算 NAV）
    upsert_monthly_return(
        session, mr.fund_id, mr.date, payload.net_return,
        commentary_truth=payload.commentary_truth,
    )
    # 重算该基金指标（异常检测也会随之重算并覆盖）
    try:
        metrics = compute_and_store_metrics(session, mr.fund_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return sanitize_for_json(metrics)
