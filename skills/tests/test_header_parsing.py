"""表头文字定位回归：列序打乱不影响解析（非位置索引）。"""
from __future__ import annotations

from pathlib import Path

from lib.extract import parse_html_monthly_table

FIX = Path(__file__).parent / "fixtures"


def test_shuffled_columns_parsed_by_header_not_position():
    md = (FIX / "columns_shuffled.md").read_text(encoding="utf-8")
    records, ytd_map = parse_html_monthly_table(md)
    # 2024 全 12 月 + 2025 前 3 月
    assert len(records) == 15
    # 2024-01 = 0.40%
    jan = [r for d, r in records if d.startswith("2024-01")]
    assert len(jan) == 1
    assert abs(jan[0] - 0.0040) < 1e-9
    # 负号捕获：2024-07 = -0.10%
    jul = [r for d, r in records if d.startswith("2024-07")]
    assert abs(jul[0] - (-0.0010)) < 1e-9
    # YTD map 正确（YTD 列在 Jan 前，靠表头定位仍取对）
    assert abs(ytd_map["2024"] - 0.0150) < 1e-9
    assert abs(ytd_map["2025"] - 0.0090) < 1e-9


def test_negative_sign_captured():
    md = (FIX / "columns_shuffled.md").read_text(encoding="utf-8")
    records, _ = parse_html_monthly_table(md)
    negs = [r for _, r in records if r < 0]
    assert negs == [-0.0010]


def test_nr_skipped_not_gap():
    md = (FIX / "columns_shuffled.md").read_text(encoding="utf-8")
    records, _ = parse_html_monthly_table(md)
    # 2025-04 起 N/R 跳过，2025 只 3 月
    y2025 = [d for d, _ in records if d.startswith("2025")]
    assert len(y2025) == 3
