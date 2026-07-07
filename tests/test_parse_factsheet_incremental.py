import pytest
from scripts.parse_factsheet import extract_month_prefix, check_gaps

def merge_and_recalculate_nav(cache_series: list[dict], new_series: list[dict]) -> list[dict]:
    # 合并
    merged_map = {dp["date"]: dp for dp in cache_series}
    for dp in new_series:
        merged_map[dp["date"]] = dp

    # 排序
    sorted_series = [merged_map[d] for d in sorted(merged_map.keys())]

    # 重算 NAV
    current_nav = 1.0
    for idx, dp in enumerate(sorted_series):
        if idx == 0:
            dp["nav"] = 1.0
            dp["net_return"] = 0.0
        else:
            current_nav = current_nav * (1.0 + dp["net_return"])
            dp["nav"] = current_nav
    return sorted_series

@pytest.mark.unit
def test_merge_and_recalculate_nav():
    cache = [
        {"date": "2020-01-31", "net_return": 0.0, "nav": 1.0},
        {"date": "2020-02-29", "net_return": 0.01, "nav": 1.01}
    ]
    new_data = [
        {"date": "2020-03-31", "net_return": 0.02, "nav": 1.0} # 新解析的临时 NAV 往往为 1.0
    ]
    result = merge_and_recalculate_nav(cache, new_data)
    assert len(result) == 3
    assert result[2]["nav"] == pytest.approx(1.01 * 1.02)

@pytest.mark.unit
def test_extract_month_prefix():
    # Bentham format
    assert extract_month_prefix("20170131-GIF-Monthly-Report.pdf") == "2017-01"
    # Short month name format
    assert extract_month_prefix("GIF-Monthly-Report-202502.pdf") == "2025-02"
    # MXT format
    assert extract_month_prefix("_2605 - MXT Monthly Report.pdf") == "2026-05"
    assert extract_month_prefix("2605 - MXT Monthly Report.pdf") is None

@pytest.mark.unit
def test_check_gaps():
    series_ok = [
        {"date": "2020-01-31", "net_return": 0.0, "nav": 1.0},
        {"date": "2020-02-29", "net_return": 0.01, "nav": 1.01},
        {"date": "2020-03-31", "net_return": 0.02, "nav": 1.0302}
    ]
    check_gaps(series_ok, "test_fund")

    series_gap = [
        {"date": "2020-01-31", "net_return": 0.0, "nav": 1.0},
        {"date": "2020-03-31", "net_return": 0.02, "nav": 1.02}
    ]
    with pytest.raises(ValueError, match="GAP DETECTED"):
        check_gaps(series_gap, "test_fund")
