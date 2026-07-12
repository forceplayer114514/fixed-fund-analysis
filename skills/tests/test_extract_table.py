"""lib/extract.py HTML 表格源解析函数单元测试。

测 parse_html_monthly_table + gate_check_table，不触网。fixture 用真实
fundmonitors markdown 结构片段（含 Historical Performance 表 + FY 表，
验证 FY 表被排除、N/R 被跳过、负号被捕获）。
"""
from __future__ import annotations

import pytest

from lib.extract import gate_check_table, parse_html_monthly_table


# 真实 fundmonitors Full Fund Profile markdown 结构片段（精简，保留关键特征）
FIXTURE_MD = """\
# Smarter Money Long Short Credit Fund (LSCF)

Some header content here.

Historical Performance  (all figures shown here are net of fees unless otherwise stated)

| Year | Jan % | Feb % | Mar % | Apr % | May % | Jun % | Jul % | Aug % | Sep % | Oct % | Nov % | Dec % | YTD % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **2026** | 0.83 | 0.07 | -0.19 | 0.99 | 0.56 | 0.61 | N/R | N/R | N/R | N/R | N/R | N/R | 2.90 |
| **2025** | 0.61 | 0.62 | -0.15 | -0.62 | 1.66 | 0.66 | 1.02 | 0.64 | 0.69 | 0.37 | 0.14 | 0.56 | 6.36 |
| **2017** | N/R | N/R | N/R | N/R | N/R | N/R | N/R | N/R | 0.34 | 0.73 | 0.39 | 0.52 | 2.00 |

Historical Financial Year Performance  (all figures shown here are are percentage per month net of fees unless otherwise stated)

| Year | Jul % | Aug % | Sep % | Oct % | Nov % | Dec % | Jan % | Feb % | Mar % | Apr % | May % | Jun % | FYTD % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **2025/2026** | 1.02 | 0.64 | 0.69 | 0.37 | 0.14 | 0.56 | 0.83 | 0.07 | -0.19 | 0.99 | 0.56 | 0.61 | 6.47 |
"""


# --- parse_html_monthly_table ---

@pytest.mark.unit
def test_parse_record_count():
    """2026(6)+2025(12)+2017(4)=22 条记录，FY 表不重复计数。"""
    records, _ = parse_html_monthly_table(FIXTURE_MD)
    assert len(records) == 22


@pytest.mark.unit
def test_parse_start_end():
    """起点 2017-09-30（首份有数据月），终点 2026-06-30。"""
    records, _ = parse_html_monthly_table(FIXTURE_MD)
    assert records[0][0] == "2017-09-30"
    assert records[-1][0] == "2026-06-30"


@pytest.mark.unit
def test_parse_skips_nr_pre_inception_and_future():
    """N/R 跳过：2017 Jan-Aug 不出现，2026 Jul-Dec 不出现。"""
    records, _ = parse_html_monthly_table(FIXTURE_MD)
    dates = {d for d, _ in records}
    # 2017 仅有 Sep-Dec
    assert "2017-09-30" in dates
    assert "2017-12-31" in dates
    assert "2017-08-31" not in dates  # N/R
    # 2026 仅有 Jan-Jun
    assert "2026-06-30" in dates
    assert "2026-07-31" not in dates  # N/R
    assert "2026-12-31" not in dates  # N/R


@pytest.mark.unit
def test_parse_negative_signs_captured():
    """负号必须捕获：2026 Mar=-0.19%, 2025 Mar=-0.15%, 2025 Apr=-0.62%。"""
    records, _ = parse_html_monthly_table(FIXTURE_MD)
    by_date = {d: r for d, r in records}
    assert by_date["2026-03-31"] == pytest.approx(-0.0019)
    assert by_date["2025-03-31"] == pytest.approx(-0.0015)
    assert by_date["2025-04-30"] == pytest.approx(-0.0062)
    # 正数也正确
    assert by_date["2026-01-31"] == pytest.approx(0.0083)


@pytest.mark.unit
def test_parse_ytd_map():
    """YTD 列提取为小数 dict，供 gate_check_table 复利交叉验证。"""
    _, ytd_map = parse_html_monthly_table(FIXTURE_MD)
    assert ytd_map == {"2026": 0.029, "2025": 0.0636, "2017": 0.02}


@pytest.mark.unit
def test_parse_excludes_fy_table():
    """FY 表（Historical Financial Year Performance）不被解析：无 2025/2026 这种行。"""
    records, ytd_map = parse_html_monthly_table(FIXTURE_MD)
    # FY 表年份带斜杠（2025/2026），不匹配 \d{4}，且其整张表在 FY marker 之后被排除
    assert "2025/2026" not in ytd_map
    # FY 表的 7 月值 1.02 不会作为 2025-07 重复出现（2025-07 应为 1.02 是巧合一致，
    # 但关键是 FY 表的 2025/2026 行不产生额外记录）。22 条已验证不含 FY 行。
    assert len(records) == 22


@pytest.mark.unit
def test_parse_empty_markdown():
    """空 markdown -> ([], {})，绝不猜测。"""
    assert parse_html_monthly_table("") == ([], {})


@pytest.mark.unit
def test_parse_no_historical_performance_marker():
    """无 Historical Performance 标记 -> ([], {})。"""
    md = "# Some fund\n| Year | Jan % |\n| --- | --- |\n| 2025 | 0.61 |"
    assert parse_html_monthly_table(md) == ([], {})


# --- gate_check_table ---

# 2025 全年 12 月真实值（net of fees，小数），compound ≈ 0.0636
RET_2025 = [
    ("2025-01-31", 0.0061), ("2025-02-28", 0.0062), ("2025-03-31", -0.0015),
    ("2025-04-30", -0.0062), ("2025-05-31", 0.0166), ("2025-06-30", 0.0066),
    ("2025-07-31", 0.0102), ("2025-08-31", 0.0064), ("2025-09-30", 0.0069),
    ("2025-10-31", 0.0037), ("2025-11-30", 0.0014), ("2025-12-31", 0.0056),
]


@pytest.mark.unit
def test_gate_pass_real_2025_data():
    """真实 2025 数据：YTD 复利通过，无缺口，无连续相同，字段类型正常 -> pass。"""
    ytd_map = {"2025": 0.0636}
    pass_ok, errors = gate_check_table(RET_2025, ytd_map)
    assert pass_ok is True
    assert errors == []


@pytest.mark.unit
def test_gate_ytd_compounding_fail():
    """篡改一个月值，YTD 复利误差 > 0.5% -> fail。"""
    tampered = list(RET_2025)
    tampered[0] = ("2025-01-31", 0.05)  # 0.61% -> 5%
    ytd_map = {"2025": 0.0636}
    pass_ok, errors = gate_check_table(tampered, ytd_map)
    assert pass_ok is False
    assert any("YTD 复利验证失败" in e for e in errors)


@pytest.mark.unit
def test_gate_gap_detected():
    """删中间一个月 -> 缺口，fail。"""
    gapped = RET_2025[:5] + RET_2025[6:]  # 删 2025-06
    ytd_map = {"2025": 0.0636}
    pass_ok, errors = gate_check_table(gapped, ytd_map)
    assert pass_ok is False
    assert any("缺口" in e for e in errors)
    assert "2025-06" in str(errors)


@pytest.mark.unit
def test_gate_anti_fabrication():
    """连续 3 个相同非零值 -> ANTI-FABRICATION，fail。"""
    records = [
        ("2025-01-31", 0.0050), ("2025-02-28", 0.0050), ("2025-03-31", 0.0050),
        ("2025-04-30", 0.0061),
    ]
    pass_ok, errors = gate_check_table(records, {})
    assert pass_ok is False
    assert any("ANTI-FABRICATION" in e for e in errors)


@pytest.mark.unit
def test_gate_field_type_violation():
    """|r| >= 0.5（50%）-> 字段异常，fail。"""
    records = list(RET_2025)
    records[6] = ("2025-07-31", 0.6)  # 60%
    ytd_map = {"2025": 0.0636}
    pass_ok, errors = gate_check_table(records, ytd_map)
    assert pass_ok is False
    assert any("字段异常" in e for e in errors)


@pytest.mark.unit
def test_gate_empty_records():
    """空 records -> fail，'无数据'。"""
    pass_ok, errors = gate_check_table([], {})
    assert pass_ok is False
    assert errors == ["无数据"]


@pytest.mark.unit
def test_gate_ytd_partial_year_inception():
    """首年部分月（inception，如 2017 Sep-Dec 4 月）YTD 复利也能验证通过。"""
    records_2017 = [
        ("2017-09-30", 0.0034), ("2017-10-31", 0.0073),
        ("2017-11-30", 0.0039), ("2017-12-31", 0.0052),
    ]
    ytd_map = {"2017": 0.02}
    pass_ok, errors = gate_check_table(records_2017, ytd_map)
    assert pass_ok is True  # compound ≈ 0.0199，误差 < 0.5%
