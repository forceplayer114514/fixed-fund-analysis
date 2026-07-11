"""period 切片纯函数测试。"""
import pytest
from app.period import get_common_months, slice_by_period


@pytest.mark.unit
def test_slice_full():
    dates = ["2026-01-31", "2026-02-28", "2026-03-31"]
    rets = [0.01, 0.02, 0.03]
    d, r = slice_by_period(dates, rets, "full")
    assert d == dates and r == rets


@pytest.mark.unit
def test_slice_3y_takes_last_36():
    # 48 个月，3y 切片取最后 36 个 -> 从 index 12 (2024-01) 起
    dates = [f"2023-{m:02d}-28" for m in range(1, 13)] + [f"2024-{m:02d}-28" for m in range(1, 13)] \
            + [f"2025-{m:02d}-28" for m in range(1, 13)] + [f"2026-{m:02d}-28" for m in range(1, 13)]
    rets = [0.001 * i for i in range(48)]
    d, r = slice_by_period(dates, rets, "3y")
    assert len(d) == 36
    assert d[0] == "2024-01-28"  # 48 个月的最后 36 个


@pytest.mark.unit
def test_slice_1y_takes_last_12():
    # 24 个月，1y 切片取最后 12 个 -> 从 index 12 (2026-01) 起
    dates = [f"2025-{m:02d}-28" for m in range(1, 13)] + [f"2026-{m:02d}-28" for m in range(1, 13)]
    rets = [0.001 * i for i in range(24)]
    d, r = slice_by_period(dates, rets, "1y")
    assert len(d) == 12
    assert d[0] == "2026-01-28"  # 24 个月的最后 12 个


@pytest.mark.unit
def test_slice_common():
    dates = ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"]
    rets = [0.01, 0.02, 0.03, 0.04]
    common = ["2026-02", "2026-03"]
    d, r = slice_by_period(dates, rets, "common", common_months=common)
    assert d == ["2026-02-28", "2026-03-31"]
    assert r == [0.02, 0.03]


@pytest.mark.unit
def test_get_common_months_intersection():
    a = ["2026-01-31", "2026-02-28", "2026-03-31"]
    b = ["2026-02-28", "2026-03-31", "2026-04-30"]
    common = get_common_months([a, b])
    assert common == ["2026-02", "2026-03"]


@pytest.mark.unit
def test_slice_invalid_period_raises():
    with pytest.raises(ValueError):
        slice_by_period(["2026-01-31"], [0.01], "5y")
