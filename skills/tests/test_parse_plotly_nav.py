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


def test_realistic_html_with_layout_names_extracts_fund():
    """真实 pandoc Plotly HTML：2 trace + layout 块含 annotation/axis name 字段。

    旧逻辑（count-mismatch 校验）会因 layout 的 "name" 字段无对应 "text" 数组而
    raise；新 trace-scoped 提取按对象作用域配对 name+text，layout name 不参与。
    本测试锁定该修复。
    """
    import re as _re

    html = """
    <div class="plotly-graph-div"></div>
    <script type="text/javascript">
    window.PLOTLYENV=window.PLOTLYENV || {};
    var data = [
      {"name":"Coolabah FRHY Fund (Assisted)","x":["2022-11-30","2022-12-31"],"text":["Coolabah FRHY Fund (Assisted)<br />2022-11-30: $100.00","Coolabah FRHY Fund (Assisted)<br />2022-12-31: $100.52"],"type":"scatter"},
      {"name":"AusBond Credit FRN Index","x":["2022-11-30","2022-12-31"],"text":["AusBond Credit FRN Index<br />2022-11-30: $100.00","AusBond Credit FRN Index<br />2022-12-31: $100.20"],"type":"scatter"}
    ];
    var layout = {
      "xaxis":{"name":"xaxis1","title":"Date"},
      "yaxis":{"name":"yaxis1","title":"NAV"},
      "annotations":[{"name":"annot1","text":"some note"}],
      "legend":{"name":"legend1"}
    };
    var config = {"displayModeBar":false};
    </script>
    """
    # 证明旧 count-mismatch 逻辑在此 fixture 上会 raise：name 字段数(6) >
    # text 数组数(2)，因 layout 的 xaxis/yaxis/annot/legend name 无对应 text 数组。
    name_fields = _re.findall(r'"name":\s*"([^"]+)"', html)
    text_arrays = _re.findall(r'"text":\s*\[([^\]]+)\]', html, _re.DOTALL)
    assert len(name_fields) > len(text_arrays), "fixture 须含 layout name 触发旧 count-mismatch"

    # 新逻辑：trace-scoped，正确提取 fund 序列（benchmark 丢弃，layout 不参与）
    series = parse_plotly_nav_series(html, "Assisted")
    assert len(series) == 2
    assert series[0] == ("2022-11-30", 100.00)
    assert series[-1] == ("2022-12-31", 100.52)
    # benchmark 末值 100.20 未混入
    assert all(nav >= 100.0 for _, nav in series)
    assert 100.20 not in [nav for _, nav in series]
