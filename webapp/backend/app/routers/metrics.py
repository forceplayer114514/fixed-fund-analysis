"""metrics 对比与时序路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.crud import get_returns, get_fund, resolve_rf_rates
from app.models import FundMetric, MonthlyReturn
from app.calculations import (
    compute_all_metrics,
    calculate_autocorrelation,
    unsmooth_returns,
    should_apply_geltner,
    _build_nav_series,
)
from app.metrics_pipeline import _find_month_gaps, compute_and_store_metrics
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
    # RBA 缺失：scoped 例外（PDD 1.7 / 决策1），剔除该月于超额序列 + 继续。
    # 切片路径不写 rba_missing 异常（R1）：切片可见的 RBA 缺失月必为 full 历史子集，
    # full 路径的 compute_and_store_metrics 已写库覆盖一切切片场景。
    rf, _missing = resolve_rf_rates(session, d_slice)
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
    excluded = []
    for fid in ids:
        try:
            if period == "full":
                m = session.get(FundMetric, fid)
                # 缓存新鲜度校验：date_period 必须等于最新 monthly_return 月份，
                # 否则 skills 摄取新月份后缓存过期，回退即时重算并写回（自愈）。
                latest_mr = (session.query(MonthlyReturn)
                             .filter_by(fund_id=fid)
                             .order_by(MonthlyReturn.date.desc()).first())
                latest_month = latest_mr.date[:7] if latest_mr else None
                if (m is not None and latest_month is not None
                        and m.date_period == latest_month):
                    m_dict = {c.name: getattr(m, c.name) for c in m.__table__.columns}
                else:
                    # 缓存过期/不存在：重算并写回 fund_metrics（自愈缓存）
                    m_dict = compute_and_store_metrics(session, fid)
            else:
                m_dict = _recompute_for_slice(session, fid, period, common_months)
        except ValueError as e:
            # 缺口/RBA 缺失：跳过该基金不拖垮整批（robustness），数据完整性铁律不变
            excluded.append({"fund_id": fid, "reason": str(e)})
            continue
        m_dict["fund_id"] = fid
        fund = get_fund(session, fid)
        m_dict["fund_name"] = fund.fund_name if fund else None
        # 布尔字段统一转 bool（FundMetric 是 Integer 0/1，time-series 返回 bool）
        for bk in ("is_short_history_warning", "is_geltner_applied", "is_q_significant",
                   "orig_dd_recovered", "un_dd_recovered"):
            if m_dict.get(bk) is not None:
                m_dict[bk] = bool(m_dict[bk])
        results.append(m_dict)
    return {"period": period, "funds": sanitize_for_json(results), "excluded": excluded}


@router.get("/time-series")
def time_series(fund_ids: str = Query(...),
                period: str = Query("full"),
                session: Session = Depends(get_db)):
    """对齐累计 NAV 时序（原始 + 去平滑）。

    去平滑 NAV 仅当切片后序列通过 Geltner 三重防火墙才返回，否则为 null。
    """
    if period not in VALID_PERIODS:
        raise HTTPException(status_code=422, detail=f"period 须为 {VALID_PERIODS}")
    ids = [s.strip() for s in fund_ids.split(",") if s.strip()]
    if not ids:
        raise HTTPException(status_code=422, detail="fund_ids 不能为空")

    per_fund = {}
    for fid in ids:
        fund = get_fund(session, fid)
        if fund is None:
            raise HTTPException(status_code=404, detail=f"基金 {fid} 不存在")
        ts = get_returns(session, fid)
        per_fund[fid] = {
            "name": fund.fund_name,
            "dates": [d["date"] for d in ts],
            "returns": [d["net_return"] for d in ts],
        }

    common_months = None
    if period == "common":
        common_months = get_common_months([v["dates"] for v in per_fund.values()])

    sliced = {}
    excluded = []
    for fid, info in per_fund.items():
        d, r = slice_by_period(info["dates"], info["returns"], period, common_months)
        # 数据缺口零容忍（CLAUDE.md 第一条）：切片后序列有缺口则跳过该基金
        gaps = _find_month_gaps(d)
        if gaps:
            excluded.append({"fund_id": fid, "reason": f"时序切片后月份存在缺口: {gaps}"})
            continue
        sliced[fid] = {"name": info["name"], "dates": d, "returns": r}

    all_months = sorted({d[:7] for info in sliced.values() for d in info["dates"]})

    # 全局 RBA 月利率，对齐 all_months，缺失月为 None（PDD 1.7 scoped 例外）。
    # 前端热力图/滚动超额/超额序列共用。all_months 为 YYYY-MM，resolve_rf_rates 的 d[:7] 兼容。
    rba_rates, _rba_missing = resolve_rf_rates(session, all_months)

    series = []
    for fid, info in sliced.items():
        r_slice = info["returns"]
        orig_nav = _build_nav_series(r_slice)[1:]  # 去掉起点 1.0
        unsm_nav = None
        unsm_returns = None
        is_geltner = False
        if len(r_slice) >= 2:
            phi, q = calculate_autocorrelation(r_slice, fund_name=fid)
            is_geltner = should_apply_geltner(len(r_slice), phi, q)
            if is_geltner:
                unsm_returns = unsmooth_returns(r_slice, phi, fund_name=fid)
                unsm_nav = _build_nav_series(unsm_returns)[1:]
        series.append({
            "fund_id": fid,
            "fund_name": info["name"],
            "dates": info["dates"],
            "returns": r_slice,
            "unsm_returns": unsm_returns,
            "orig_nav": orig_nav,
            "unsm_nav": unsm_nav,
            "is_geltner_applied": is_geltner,
        })
    return {"period": period, "months": all_months, "rba": sanitize_for_json(rba_rates),
            "series": sanitize_for_json(series), "excluded": excluded}
