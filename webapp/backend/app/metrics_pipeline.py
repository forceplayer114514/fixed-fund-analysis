"""指标计算编排管道：从数据库读取月度收益 -> 计算5维指标 + 检测异常 -> 写回数据库。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.crud import get_returns, resolve_rf_rates, replace_anomalies, upsert_metrics
from app.calculations import compute_all_metrics
from app.anomaly import detect_anomalies


def compute_and_store_metrics(
    session: Session,
    fund_id: str,
    fallback_rba_rate: Optional[float] = None,
) -> dict:
    """计算并持久化某基金的5维指标与异常。

    Args:
        session: 数据库会话。
        fund_id: 基金ID。
        fallback_rba_rate: 数据库中缺失 RBA 利率时的回退值（通常为当前抓取的利率）。

    Returns:
        计算出的指标 dict（已写入 fund_metrics 表）。
    """
    time_series = get_returns(session, fund_id)
    if not time_series:
        raise ValueError(f"基金 {fund_id} 无月度收益数据，无法计算指标")

    returns = [dp["net_return"] for dp in time_series]
    dates = [dp["date"] for dp in time_series]

    if fallback_rba_rate is None:
        fallback_rba_rate = 0.0435  # 安全回退，正常流程应传入抓取值

    rf_rates = resolve_rf_rates(session, dates, fallback_rate=fallback_rba_rate)

    # 计算5维指标
    metrics = compute_all_metrics(returns, rf_rates, fund_name=fund_id)

    # 记录数据截止月份（最近月份），FundMetric 必需字段
    metrics["date_period"] = dates[-1][:7]

    # 检测异常并写入
    anomalies = detect_anomalies(time_series, threshold_sigma=3.0)
    replace_anomalies(session, fund_id, anomalies)

    # 写入指标
    upsert_metrics(session, fund_id, metrics)

    return metrics
