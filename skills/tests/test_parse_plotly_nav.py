"""parse_plotly_nav_series tests: name-filter extraction, benchmark drop, zero/multi raise."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.extract import parse_plotly_nav_series

FIX = Path(__file__).parent / "fixtures"


def test_assisted_extracts_correct_trace_drops_benchmark():
    html = (FIX / "frhy_assisted.html").read_text(encoding="utf-8")
    series = parse_plotly_nav_series(html, "Assisted")
    assert len(series) == 5
    assert series[0] == ("2022-11-30", 100.00)
    assert series[-1] == ("2023-03-31", 102.08)
    # benchmark not included
    navs = [nav for _, nav in series]
    assert all(nav >= 100.0 for nav in navs)
    assert 100.80 not in navs  # last benchmark value


def test_institutional_extracts_correct_trace():
    html = (FIX / "frhy_institutional.html").read_text(encoding="utf-8")
    series = parse_plotly_nav_series(html, "Institutional")
    assert len(series) == 5
    assert series[0] == ("2022-11-30", 100.00)
    assert series[-1] == ("2023-03-31", 103.92)


def test_zero_match_raises():
    html = (FIX / "frhy_assisted.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="零匹配"):
        parse_plotly_nav_series(html, "NonexistentClass")


def test_multi_match_raises():
    # build html with two traces both matching "Coolabah"
    html = """
    {"name":"Coolabah Fund A","text":["Coolabah Fund A<br />2022-11-30: $100.00"]},
    {"name":"Coolabah Fund B","text":["Coolabah Fund B<br />2022-12-31: $101.00"]}
    """
    with pytest.raises(ValueError, match="多 trace 匹配"):
        parse_plotly_nav_series(html, "Coolabah")


def test_ascending_order():
    html = (FIX / "frhy_assisted.html").read_text(encoding="utf-8")
    series = parse_plotly_nav_series(html, "Assisted")
    dates = [d for d, _ in series]
    assert dates == sorted(dates)
