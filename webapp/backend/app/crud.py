"""数据库 CRUD 操作与 NAV 重计算。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import Fund, MonthlyReturn, RbaCashRate, Anomaly, FundMetric


def create_fund(session: Session, **kwargs) -> Fund:
    fund = Fund(**kwargs)
    session.add(fund)
    session.commit()
    session.refresh(fund)
    return fund


def get_fund(session: Session, fund_id: str) -> Optional[Fund]:
    return session.get(Fund, fund_id)


def get_all_funds(session: Session) -> list[Fund]:
    return session.query(Fund).order_by(Fund.fund_name).all()


def delete_fund(session: Session, fund_id: str) -> bool:
    fund = session.get(Fund, fund_id)
    if fund is None:
        return False
    session.delete(fund)  # 级联删除子表
    session.commit()
    return True


def upsert_monthly_return(session: Session, fund_id: str, date: str,
                          net_return: float, commentary_truth: Optional[float] = None) -> MonthlyReturn:
    """插入或更新某月收益，随后重算该基金全部 NAV。"""
    existing = session.query(MonthlyReturn).filter_by(fund_id=fund_id, date=date).first()
    if existing:
        existing.net_return = net_return
        if commentary_truth is not None:
            existing.commentary_truth = commentary_truth
        row = existing
    else:
        row = MonthlyReturn(fund_id=fund_id, date=date, net_return=net_return,
                            nav=1.0, commentary_truth=commentary_truth)
        session.add(row)
    session.commit()
    recompute_nav(session, fund_id)
    session.refresh(row)
    return row


def get_returns(session: Session, fund_id: str) -> list[dict]:
    """按日期升序返回该基金的月度收益（date, net_return, commentary_truth）。"""
    rows = session.query(MonthlyReturn).filter_by(fund_id=fund_id).order_by(MonthlyReturn.date).all()
    return [{"date": r.date, "net_return": r.net_return, "commentary_truth": r.commentary_truth}
            for r in rows]


def recompute_nav(session: Session, fund_id: str) -> None:
    """重新计算该基金全部累计 NAV（以 1.0 为起点复利）。

    在插入/更新任意月度收益后调用，确保 NAV 序列始终连续正确。
    """
    rows = session.query(MonthlyReturn).filter_by(fund_id=fund_id).order_by(MonthlyReturn.date).all()
    nav = 1.0
    for r in rows:
        nav = nav * (1.0 + r.net_return)
        r.nav = nav
    session.commit()


def resolve_rf_rates(session: Session, dates: list[str], fallback_rate: float) -> list[float]:
    """按月份从 rba_cash_rates 表查年化利率，缺失月份用 fallback。"""
    rates = []
    for d in dates:
        month_key = d[:7]  # YYYY-MM
        rba = session.get(RbaCashRate, month_key)
        rates.append(rba.rate if rba else fallback_rate)
    return rates


def replace_anomalies(session: Session, fund_id: str, anomalies: list[dict]) -> None:
    """清空并重写某基金的异常记录。

    显式映射字段（忽略 detect_anomalies 返回的 commentary_truth，
    因为 Anomaly 表不存储此字段--它属于 monthly_returns 表）。
    """
    session.query(Anomaly).filter_by(fund_id=fund_id).delete()
    for a in anomalies:
        session.add(Anomaly(
            fund_id=fund_id,
            date=a["date"],
            value=a["value"],
            z_score=a["z_score"],
            threshold_sigma=a["threshold_sigma"],
            mean=a["mean"],
            stdev=a["stdev"],
        ))
    session.commit()


def upsert_metrics(session: Session, fund_id: str, metrics: dict) -> None:
    """插入或更新某基金的5维指标记录。"""
    existing = session.get(FundMetric, fund_id)
    if existing:
        for key, val in metrics.items():
            setattr(existing, key, val)
    else:
        session.add(FundMetric(fund_id=fund_id, **metrics))
    session.commit()
