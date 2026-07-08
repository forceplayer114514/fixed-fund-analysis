import pytest
import re
import datetime

# Helper functions to test (copied from parse_factsheet.py)
def extract_yymm(filename):
    match = re.search(r'_(\d{2})(\d{2})', filename)
    if match:
        return int(match.group(1)) * 100 + int(match.group(2))
    return 0

def get_last_day_of_month(year, month):
    if month == 12:
        return datetime.date(year, 12, 31)
    return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)

def parse_blocks_to_returns(p2_blocks, report_year, report_month_num):
    raw_data_points = []
    for b in p2_blocks:
        bbox, block_text = b
        lines = [line.strip() for line in block_text.split('\n') if line.strip()]
        if not lines:
            continue

        if re.match(r'^\d{4}$', lines[0]):
            block_year = int(lines[0])
            if 2017 <= block_year <= 2026:
                y0 = bbox[1]
                if 110 <= y0 <= 283:
                    values = lines[1:-1]
                    if block_year == 2017:
                        if len(values) == 3:
                            months = [10, 11, 12]
                        else:
                            months = list(range(13 - len(values), 13))
                    elif block_year == report_year:
                        months = list(range(1, len(values) + 1))
                    else:
                        months = list(range(1, 13))

                    for idx, val in enumerate(values):
                        if idx >= len(months):
                            break
                        m = months[idx]
                        try:
                            net_return = float(val) / 100.0
                            m_date = get_last_day_of_month(block_year, m)
                            raw_data_points.append({
                                "date": m_date.strftime("%Y-%m-%d"),
                                "net_return": net_return
                            })
                        except ValueError:
                            continue
    return raw_data_points

@pytest.mark.unit
def test_extract_yymm_mxt():
    assert extract_yymm("6a34d33147df3603f998feea_2605 - MXT Monthly Report.pdf") == 2605
    assert extract_yymm("69c5c97a01ad2f5e190c6018_2602 - MXT Monthly Report.pdf") == 2602
    assert extract_yymm("no_digits_here.pdf") == 0

@pytest.mark.unit
def test_parse_blocks_mxt_happy_path():
    # Simulate page 2 blocks with coordinates and texts
    # Format of b: (bbox_tuple, text)
    # bbox_tuple: (x0, y0, x1, y1)
    p2_blocks = [
        # Net Return blocks (y0 in [110, 283])
        ((41.7, 140.4, 558.8, 150.5), "2026\n0.66\n0.60\n0.65\n0.73\n0.69\n3.33"),
        ((41.8, 155.2, 558.8, 165.3), "2025\n0.64\n0.60\n0.66\n0.65\n0.70\n0.63\n0.65\n0.66\n0.61\n0.64\n0.63\n0.66\n7.76"),
        ((42.4, 271.8, 558.1, 281.9), "2017\n0.46\n0.35\n0.41\n1.23"),

        # Distribution blocks (y0 in [305, 465]) - should be filtered out by y0 check
        ((41.3, 320.9, 558.0, 331.0), "2026\n1.36\n1.17\n1.33\n1.37\n1.48\n6.71"),
        ((42.1, 455.0, 557.9, 465.1), "2017\n-\n-\n2.19\n2.19")
    ]

    returns = parse_blocks_to_returns(p2_blocks, 2026, 5)

    # Check total length: 5 (from 2026) + 12 (from 2025) + 3 (from 2017) = 20 points
    assert len(returns) == 20

    # Sort chronologically
    returns.sort(key=lambda x: x["date"])

    # Check 2017 points
    assert returns[0]["date"] == "2017-10-31"
    assert returns[0]["net_return"] == pytest.approx(0.0046)
    assert returns[1]["date"] == "2017-11-30"
    assert returns[1]["net_return"] == pytest.approx(0.0035)
    assert returns[2]["date"] == "2017-12-31"
    assert returns[2]["net_return"] == pytest.approx(0.0041)

    # Check 2025 points
    assert returns[3]["date"] == "2025-01-31"
    assert returns[3]["net_return"] == pytest.approx(0.0064)

    # Check 2026 points
    assert returns[-1]["date"] == "2026-05-31"
    assert returns[-1]["net_return"] == pytest.approx(0.0069)
