"""数据库 CRUD 操作与 NAV 重计算。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Fund, MonthlyReturn, RbaCashRate, Anomaly, FundMetric

# 须与 llm_ingest/cli.py::PDF_ROOT 保持一致 (同一份 PDF 缓存目录)。
# DB 层 cascade (ORM relationship + confirmed_gaps/pending_review 的 FK
# pragma, 见 database.py) 覆盖不到文件系统 -- 删 fund 不清这个目录的话,
# 同 fund_id 重新添加时摄取流程会把残留旧 PDF 当缓存复用 (跳过重新下载),
# 相当于新基金喂进了旧数据 (2026-07 发现)。
PDF_ROOT = Path(__file__).resolve().parents[3] / "data" / "pdf_cache"


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
    session.delete(fund)  # 级联删除子表 (DB 层, 见 PDF_ROOT 注释)
    session.commit()
    pdf_dir = PDF_ROOT / fund_id
    if pdf_dir.exists():
        shutil.rmtree(pdf_dir, ignore_errors=True)
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


def resolve_rf_rates(session: Session, dates: list[str]) -> tuple[list[float | None], list[str]]:
    """按月份从 rba_cash_rates 表查年化利率。

    Returns:
        (rates, missing_dates): rates 与 dates 等长，RBA 缺失位为 None；
        missing_dates 为 RBA 缺失的基金日期列表（供 pipeline 写 rba_missing 异常）。

    Scoped 例外（PDD 1.7 / 决策1）：RBA 基准缺失不抛错，交由 pipeline 剔除该月于
    超额序列 + 写异常审计 + 继续计算。基金自身月度缺口仍由 _find_month_gaps 零容忍。
    RBA 缺失会扭曲超额收益类指标，故剔除而非填充（禁插值/回填，CLAUDE.md 第一条）。
    """
    rates: list[float | None] = []
    missing: list[str] = []
    for d in dates:
        month_key = d[:7]  # YYYY-MM
        rba = session.get(RbaCashRate, month_key)
        if rba is None:
            missing.append(d)
            rates.append(None)
        else:
            rates.append(rba.rate)
    return rates, missing


def replace_anomalies(session: Session, fund_id: str, anomalies: list[dict]) -> None:
    """清空并重写某基金的异常记录（return_outlier + rba_missing 两类）。

    每条 dict 需含 date, type；return_outlier 另含 value/z_score/threshold_sigma/
    mean/stdev；rba_missing 另含 reason，数值字段缺省写 None（Anomaly 表这些列已 nullable）。
    忽略 detect_anomalies 返回的 commentary_truth（属 monthly_returns 表，不存于此）。
    """
    session.query(Anomaly).filter_by(fund_id=fund_id).delete()
    for a in anomalies:
        session.add(Anomaly(
            fund_id=fund_id,
            date=a["date"],
            type=a.get("type", "return_outlier"),
            reason=a.get("reason"),
            value=a.get("value"),
            z_score=a.get("z_score"),
            threshold_sigma=a.get("threshold_sigma"),
            mean=a.get("mean"),
            stdev=a.get("stdev"),
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
