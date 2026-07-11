"""RBA 定时调度（Task 6 实现）。占位：让 main.py lifespan 可 import。"""
from __future__ import annotations

from typing import Optional


def start_scheduler(session_factory=None) -> Optional[object]:
    """Task 6 实现：启动 APScheduler 每日抓取 RBA。占位返回 None。"""
    return None


def shutdown_scheduler(scheduler) -> None:
    """Task 6 实现：优雅停止调度器。占位空操作。"""
    if scheduler is not None:
        pass
