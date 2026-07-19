"""RBA 定时调度：APScheduler 每日抓取 RBA 利率并入库。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.rba import expand_to_monthly, fetch_rba_rate_history, upsert_rba_rates
from app.database import SessionLocal
from app.metrics_pipeline import recompute_all_funds


def run_rba_update(session_factory=None) -> dict:
    """执行一次 RBA 抓取 + 入库：抓官方 Cash Rate Target 全历史表，展开成逐月字典
    覆盖写入（同一张表既给当前利率也给历史序列，见 app/rba.py 模块 docstring）。

    RBA 利率变了，所有基金的 fund_metrics 缓存（依赖 rf_rates 算超额收益/IR）
    随之级联重算，否则 full 路径会继续读旧利率算出的缓存值（真实发生过：本次
    会话重写 RBA 源后，未重算的缓存与锚定态即时重算的数值对不上）。

    Args:
        session_factory: 可选的会话工厂（测试注入）；默认用 SessionLocal。
    Returns:
        {"current_rate": float, "upserted": int, "recomputed": [...], "failed": [...]}
    """
    factory = session_factory or SessionLocal
    history = fetch_rba_rate_history()
    current_month = datetime.now().strftime("%Y-%m")
    monthly = expand_to_monthly(history, through_month=current_month)
    session = factory()
    try:
        count = upsert_rba_rates(session, monthly)
        recompute_result = recompute_all_funds(session)
        current_rate = history[-1][1] if history else None
        return {"current_rate": current_rate, "upserted": count, **recompute_result}
    finally:
        # 默认工厂创建的 session 需关闭；测试注入的 session 由测试管理
        if session_factory is None:
            session.close()


def start_scheduler(session_factory=None) -> Optional[BackgroundScheduler]:
    """启动每日 RBA 调度。返回调度器实例；SCHEDULER_ENABLED=False 时返回 None。"""
    if not settings.SCHEDULER_ENABLED:
        return None
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(
        run_rba_update,
        CronTrigger(hour=settings.RBA_CRON_HOUR, minute=0),
        args=[session_factory],
        id="rba_daily_update",
        replace_existing=True,
    )
    sched.start()
    return sched


def shutdown_scheduler(scheduler) -> None:
    """优雅停止调度器。"""
    if scheduler is not None:
        scheduler.shutdown(wait=False)
