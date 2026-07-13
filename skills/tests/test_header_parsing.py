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


def test_short_fy_row_rejected_by_cell_count():
    """模拟 section 提取失败的场景：4-digit FY 短行泄漏到 Historical Performance 表内。
    短行（5 cell）应被 cell-count guard 拒绝，不贡献任何 record，不污染 YTD map。"""
    md = (
        "# Fund Profile\n"
        "\n"
        "## Historical Performance\n"
        "\n"
        "| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |\n"
        "|------|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|\n"
        "| 2023 | 0.30 | 0.40 | 0.50 | 0.60 | 0.70 | 0.80 | 0.90 | 1.00 | 1.10 | 1.20 | 1.30 | 1.40 | 8.50 |\n"
        # FY 短行（5 cell）：如果 cell-count guard 失效，会被误解析为
        # Jan=0.50, Feb=0.10, Mar=0.20, YTD=0.50，产生 3 条错误 record
        "| 2024 | 0.50% | 0.10% | 0.20% | 0.20% |\n"
        "| 2025 | 0.50 | 0.60 | 0.70 | 0.80 | 0.90 | 1.00 | 1.10 | 1.20 | 1.30 | 1.40 | 1.50 | 1.60 | 12.30 |\n"
    )
    records, ytd_map = parse_html_monthly_table(md)
    # 短行不应产生任何 record
    y2024 = [d for d, _ in records if d.startswith("2024")]
    assert len(y2024) == 0, "FY 短行不应被误解析为 2024 月度数据"
    # 正常行仍正常解析：2023 12 月 + 2025 12 月 = 24
    assert len(records) == 24
    # YTD map 不应被短行污染：ytd_map["2024"] 不应存在
    assert "2024" not in ytd_map, "FY 短行不应污染 YTD map"
    # 2023 和 2025 的 YTD 正常
    assert abs(ytd_map["2023"] - 0.0850) < 1e-9
    assert abs(ytd_map["2025"] - 0.1230) < 1e-9
