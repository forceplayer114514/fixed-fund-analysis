"""RBA 定时调度：APScheduler 每日抓取 RBA 利率并入库。"""
from __future__ import annotations

from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.rba import fetch_current_rba_rate, fetch_historical_rba_rates, upsert_rba_rates
from app.database import SessionLocal


def run_rba_update(session_factory=None) -> dict:
    """执行一次 RBA 抓取 + 入库。

    Args:
        session_factory: 可选的会话工厂（测试注入）；默认用 SessionLocal。
    Returns:
        {"current_rate": float, "upserted": int}
    """
    factory = session_factory or SessionLocal
    current = fetch_current_rba_rate()
    historical = fetch_historical_rba_rates()
    session = factory()
    try:
        count = upsert_rba_rates(session, historical)
        return {"current_rate": current, "upserted": count}
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
