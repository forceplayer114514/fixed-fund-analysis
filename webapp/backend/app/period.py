"""period 切片纯函数：full/3y/1y/common。无 IO。"""
from __future__ import annotations

from typing import Optional

VALID_PERIODS = {"full", "3y", "1y", "common"}


def get_common_months(dates_lists: list[list[str]]) -> list[str]:
    """多基金月末日期列表 -> 共同月份（YYYY-MM）交集，升序。"""
    if not dates_lists:
        return []
    sets = [{d[:7] for d in dl} for dl in dates_lists]
    common = sets[0]
    for s in sets[1:]:
        common = common & s
    return sorted(common)


def slice_by_period(dates: list[str], returns: list[float], period: str,
                    common_months: Optional[list[str]] = None) -> tuple[list[str], list[float]]:
    """按 period 切片 (dates, returns)。

    full: 全部；3y: 最后 36 个；1y: 最后 12 个；common: 仅保留 common_months 内月份。
    """
    if period not in VALID_PERIODS:
        raise ValueError(f"未知 period: {period}，支持 {VALID_PERIODS}")
    if period == "full":
        return list(dates), list(returns)
    if period == "3y":
        n = min(36, len(dates))
        return dates[-n:], returns[-n:]
    if period == "1y":
        n = min(12, len(dates))
        return dates[-n:], returns[-n:]
    # common
    if common_months is None:
        common_months = []
    keep = set(common_months)
    out_d, out_r = [], []
    for d, r in zip(dates, returns):
        if d[:7] in keep:
            out_d.append(d)
            out_r.append(r)
    return out_d, out_r
