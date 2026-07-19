"""指标计算编排管道：从数据库读取月度收益 -> 计算5维指标 + 检测异常 -> 写回数据库。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.crud import get_returns, resolve_rf_rates, replace_anomalies, upsert_metrics
from app.calculations import compute_all_metrics
from app.anomaly import detect_anomalies
from app.models import Fund


def _find_month_gaps(dates: list[str]) -> list[str]:
    """返回日期序列中缺失的月份列表（YYYY-MM）。

    dates 为月末日期列表（YYYY-MM-DD，升序）。返回首尾月份之间所有未出现的
    YYYY-MM。少于两个点时不存在缺口，返回空列表。
    """
    if len(dates) < 2:
        return []
    months = [d[:7] for d in dates]
    start = date.fromisoformat(dates[0][:10]).replace(day=1)
    end = date.fromisoformat(dates[-1][:10]).replace(day=1)
    expected: list[str] = []
    cur = start
    while cur <= end:
        expected.append(cur.strftime("%Y-%m"))
        # 推进到下个月第一天（用 32 天跨越当前月末，再归零日）
        cur = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
    actual_set = set(months)
    return [m for m in expected if m not in actual_set]


def compute_and_store_metrics(
    session: Session,
    fund_id: str,
) -> dict:
    """计算并持久化某基金的5维指标与异常。

    Args:
        session: 数据库会话。
        fund_id: 基金ID。

    Returns:
        计算出的指标 dict（已写入 fund_metrics 表）。

    Raises:
        ValueError: 月度数据有缺口，或 RBA 利率缺失月份（零容忍）。
    """
    time_series = get_returns(session, fund_id)
    if not time_series:
        raise ValueError(f"基金 {fund_id} 无月度收益数据，无法计算指标")

    returns = [dp["net_return"] for dp in time_series]
    dates = [dp["date"] for dp in time_series]

    # 数据缺口零容忍（CLAUDE.md 第一条）：基金月度序列必须连续，否则拒绝计算
    gaps = _find_month_gaps(dates)
    if gaps:
        raise ValueError(
            f"基金 {fund_id} 月度数据存在缺口，缺失月份: {gaps}。"
            f"拒绝计算（数据缺口零容忍）。"
        )

    # RBA 基准缺失：scoped 例外（PDD 1.7 / 决策1）--不抛错，剔除该月于超额序列 + 写异常 + 继续
    rf_rates, missing_rba_dates = resolve_rf_rates(session, dates)

    # 计算5维指标（rf_rates 含 None 的月份在 compute_all_metrics 内部剔除于超额序列）
    metrics = compute_all_metrics(returns, rf_rates, fund_name=fund_id)

    # 记录数据截止月份（最近月份），FundMetric 必需字段
    metrics["date_period"] = dates[-1][:7]

    # 检测异常并写入：MAD 离群点（type=return_outlier）+ RBA 缺失（type=rba_missing）
    anomalies = detect_anomalies(time_series, threshold_sigma=3.0)
    anomalies.extend([
        {"date": d, "type": "rba_missing",
         "reason": "RBA 现金利率缺失，该月已从超额序列剔除"}
        for d in missing_rba_dates
    ])
    replace_anomalies(session, fund_id, anomalies)

    # 写入指标
    upsert_metrics(session, fund_id, metrics)

    return metrics


def recompute_all_funds(session: Session) -> dict:
    """级联重算全部基金的 fund_metrics 缓存。

    full 路径的缓存新鲜度校验（routers/metrics.py::compare）只比对 date_period
    是否等于最新月度收益月份，不检测 rf_rates（RBA 利率）本身是否变过。RBA 数据
    源更新（人工 /api/rba/refresh 或每日调度 run_rba_update）会改写
    rba_cash_rates，但不会让已缓存的 fund_metrics 失效——旧缓存继续被 full 路径
    读出，直到某个偶然触发即时重算的路径（如锚定态 start_month 切片）暴露出数值
    不一致。这里在 RBA 更新之后统一重算一遍所有基金，消除这个缺口。

    单基金失败（数据缺口等）跳过记入 failed，不拖垮其它基金。
    """
    fund_ids = [f.fund_id for f in session.query(Fund.fund_id).all()]
    recomputed: list[str] = []
    failed: list[dict] = []
    for fid in fund_ids:
        try:
            compute_and_store_metrics(session, fid)
            recomputed.append(fid)
        except ValueError as e:
            failed.append({"fund_id": fid, "reason": str(e)})
    return {"recomputed": recomputed, "failed": failed}
