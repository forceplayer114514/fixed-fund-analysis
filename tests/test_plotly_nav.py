"""plotly_nav.parse_plotly_nav_series 单测.

本模块此前是零调用零测试的死代码。2026-08-01 复活它当"这一页自己就是月报"的
判别器 (见 docs/superpowers/plans/2026-08-01-self-report-page-routing.md), 补齐
测试后才允许接进发现层。

重点覆盖两条不许放松的硬保护 (2026-07-18 Coolabah 错源 173 月事故的教训):
  - trace 名含 benchmark/index/ausbond 一律丢弃
  - 命中 trace 数 != 1 一律 raise (0 条和 >1 条都不许猜)
"""
import sys

sys.path.insert(0, "/Users/chong/Desktop/fixed_fund_analysis")

import pytest

from llm_ingest.plotly_nav import parse_plotly_nav_series


def _trace(name: str, points) -> str:
    """造一条 Plotly trace 的 JSON 字面量 (hovertext 格式照抄 Coolabah 真实页)."""
    texts = ", ".join(f'"{name}<br />{d}: ${v}"' for d, v in points)
    return f'{{"name": "{name}", "type": "scatter", "text": [{texts}]}}'


def _html(*traces: str) -> str:
    return (
        "<html><body><div class='js-plotly-plot'></div><script>"
        f"var data = [{', '.join(traces)}];"
        "</script></body></html>"
    )


FUND_POINTS = [("2025-01-31", "100.00"), ("2025-02-28", "100.85"),
               ("2025-03-31", "101.40")]
BENCH_POINTS = [("2025-01-31", "100.00"), ("2025-02-28", "100.10"),
                ("2025-03-31", "100.22")]


class TestBenchmarkExclusion:
    def test_fund_trace_returned_benchmark_dropped(self):
        html = _html(_trace("Coolabah Short Term Income Fund", FUND_POINTS),
                     _trace("AusBond Bank Bill Index", BENCH_POINTS))
        got = parse_plotly_nav_series(html, "Coolabah Short Term Income Fund")
        assert got == [("2025-01-31", 100.0), ("2025-02-28", 100.85),
                       ("2025-03-31", 101.40)]

    def test_benchmark_alone_raises_zero_match(self):
        """基准单独存在时必须零命中 raise, 不许把基准当基金返回。"""
        html = _html(_trace("AusBond Composite Index", BENCH_POINTS))
        with pytest.raises(ValueError, match="零匹配"):
            parse_plotly_nav_series(html, "AusBond Composite Index")


class TestTokenSubsetMatching:
    """2026-08-01 Coolabah 回归: trace 名不带发行商前缀、却多带 ETF 后缀,
    子串匹配必然失败 (实测拿基金全名去匹配 raise 零匹配)。"""

    TRACE_NAME = "Global Floating-Rate High Yield Complex ETF"
    FUND_NAME = "Coolabah Global Floating-Rate High Yield Complex"

    def test_trace_name_is_subset_of_fund_name_after_type_words(self):
        html = _html(_trace(self.TRACE_NAME, FUND_POINTS))
        got = parse_plotly_nav_series(html, self.FUND_NAME)
        assert len(got) == 3
        assert got[0] == ("2025-01-31", 100.0)

    def test_sibling_share_class_with_extra_token_is_not_matched(self):
        """兄弟基金多一个不在基金名里的 token -> 不是子集 -> 零命中 raise。"""
        html = _html(_trace("Global Floating-Rate High Yield Fund AI", FUND_POINTS))
        with pytest.raises(ValueError, match="零匹配"):
            parse_plotly_nav_series(html, self.FUND_NAME)

    def test_substring_match_still_works(self):
        """回归: 原有子串语义不能因为新增 token 路径而失效。"""
        html = _html(_trace("Coolabah Active Composite Bond Fund", FUND_POINTS))
        got = parse_plotly_nav_series(html, "Active Composite Bond")
        assert len(got) == 3


class TestAmbiguityRefusal:
    def test_two_matching_traces_raise(self):
        """同页挂多个份额类别时拒绝判定, 绝不挑一个 (2026-07-18 事故教训)。"""
        html = _html(_trace("Global Floating-Rate High Yield Complex ETF", FUND_POINTS),
                     _trace("Global Floating-Rate High Yield Complex", BENCH_POINTS))
        with pytest.raises(ValueError, match="多 trace 匹配"):
            parse_plotly_nav_series(
                html, "Coolabah Global Floating-Rate High Yield Complex")

    def test_error_lists_candidate_trace_names(self):
        """2026-08-01 实测: Coolabah 同一策略两个份额类别各占一页, trace 名分别是
        "...Fund (Assisted)" / "...Fund (Institutional)"。登记的基金名没写清是哪
        一类时判定必然拒绝 -- 报错必须带上页内候选曲线名, 否则用户不知道该把
        名字改成什么, 只看到一句"未产出任何链接"。"""
        html = _html(_trace("Floating-Rate High Yield Fund (Assisted)", FUND_POINTS),
                     _trace("AusBond Credit FRN Index", BENCH_POINTS))
        with pytest.raises(ValueError) as ei:
            parse_plotly_nav_series(html, "Coolabah Floating-Rate High Yield Fund")
        msg = str(ei.value)
        assert "零匹配" in msg
        assert "Assisted" in msg, "候选曲线名必须出现在报错里"
        assert "AusBond" not in msg, "基准不该混进候选名单"

    def test_no_trace_at_all_raises(self):
        with pytest.raises(ValueError, match="零匹配"):
            parse_plotly_nav_series("<html><body>nothing</body></html>", "Some Fund")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
