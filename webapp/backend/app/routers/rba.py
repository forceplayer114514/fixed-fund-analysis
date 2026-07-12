"""RBA 手动刷新路由。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import RbaCashRate
from app.rba import fetch_current_rba_rate, fetch_historical_rba_rates, upsert_rba_rates

router = APIRouter(prefix="/api/rba", tags=["rba"])


@router.post("/refresh")
def refresh_rba(session: Session = Depends(get_db)):
    """手动触发一次 RBA 利率抓取与入库。"""
    try:
        current = fetch_current_rba_rate()
        historical = fetch_historical_rba_rates()
        count = upsert_rba_rates(session, historical)
        # 当前利率以当月 key 落库（RBA 首页比 DBnomics 及时）
        current_month = datetime.now().strftime("%Y-%m")
        existing = session.get(RbaCashRate, current_month)
        if existing:
            existing.rate = current
        else:
            session.add(RbaCashRate(date_period=current_month, rate=current))
        session.commit()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RBA 抓取失败: {e}")
    return {"current_rate": current, "upserted": count}
