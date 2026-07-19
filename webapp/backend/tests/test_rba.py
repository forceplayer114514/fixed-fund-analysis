"""RBA 利率抓取/展开/分组测试。网络请求用 unittest.mock.patch mock，不发起真实请求。"""
import pytest
from unittest.mock import patch, MagicMock

from app.rba import (
    expand_to_monthly,
    fetch_rba_rate_history,
    group_rate_periods,
    upsert_rba_rates,
)
from app.models import RbaCashRate


_FAKE_TABLE_HTML = """
<html><body><table>
<tr><th>Effective Date</th><th>Change% points</th><th>Cash rate target%</th><th>Related Documents</th></tr>
<tr><td>18 Mar 2026</td><td>+0.25</td><td>4.10</td><td>Statement</td></tr>
<tr><td>4 Feb 2026</td><td>+0.25</td><td>3.85</td><td>Statement</td></tr>
<tr><td>4 Apr 1990</td><td>-1.00 to -1.50</td><td>15.00 to 15.50</td><td>Statement</td></tr>
<tr><td>Legend:</td></tr>
<tr><td>Cash rate decreased</td></tr>
</table></body></html>
"""


@pytest.mark.unit
def test_fetch_rba_rate_history_parses_table_ascending_and_skips_junk_rows():
    """解析官方 Cash Rate Target 表：跳过表头/Legend/区间值行，按日期升序返回。"""
    mock_resp = MagicMock()
    mock_resp.text = _FAKE_TABLE_HTML
    mock_resp.raise_for_status = MagicMock()
    with patch("app.rba.requests.get", return_value=mock_resp):
        history = fetch_rba_rate_history()
    assert history == [
        ("2026-02-04", pytest.approx(0.0385)),
        ("2026-03-18", pytest.approx(0.0410)),
    ]


@pytest.mark.unit
class TestExpandToMonthly:
    def test_fills_months_with_latest_effective_rate(self):
        """无变动的月份两种口径恒等；03 月 18 号中途变动，按天加权而非整月套用月末值。"""
        history = [("2026-01-15", 0.0435), ("2026-03-18", 0.0410)]
        monthly = expand_to_monthly(history, through_month="2026-04")
        # 03 月 31 天：1-17 号(17天)旧利率 0.0435，18-31 号(14天)新利率 0.0410
        march_expected = (17 * 0.0435 + 14 * 0.0410) / 31
        assert monthly == {
            "2026-01": pytest.approx(0.0435),
            "2026-02": pytest.approx(0.0435),
            "2026-03": pytest.approx(march_expected),
            "2026-04": pytest.approx(0.0410),
        }

    def test_mid_month_change_day_weighted_not_month_end_flat(self):
        """决议当月中途生效：按实际生效天数加权平均，不是简单取月末口径这一个值。"""
        # 4 月 10 号生效，30 天中前 9 天旧利率、后 21 天新利率
        history = [("2026-04-01", 0.0400), ("2026-04-10", 0.0450)]
        monthly = expand_to_monthly(history, through_month="2026-04")
        expected = (9 * 0.0400 + 21 * 0.0450) / 30
        assert monthly["2026-04"] == pytest.approx(expected)
        # 旧口径(整月套用月末值)会给出 0.0450，跟按天加权的结果应有明显差异
        assert monthly["2026-04"] != pytest.approx(0.0450)

    def test_empty_history_returns_empty_dict(self):
        assert expand_to_monthly([], through_month="2026-04") == {}

    def test_defaults_through_month_to_now(self, monkeypatch):
        import app.rba as rba_mod

        class _FixedDatetime:
            @classmethod
            def now(cls):
                import datetime as _dt
                return _dt.datetime(2026, 5, 1)

        monkeypatch.setattr(rba_mod, "datetime", _FixedDatetime)
        monthly = expand_to_monthly([("2026-01-01", 0.04)])
        assert "2026-05" in monthly
        assert "2026-06" not in monthly


@pytest.mark.unit
class TestGroupRatePeriods:
    def test_merges_consecutive_same_rate_months(self):
        rates = {"2026-01": 0.045, "2026-02": 0.045, "2026-03": 0.041}
        assert group_rate_periods(rates) == [
            {"start_month": "2026-01", "end_month": "2026-02", "rate": 0.045},
            {"start_month": "2026-03", "end_month": "2026-03", "rate": 0.041},
        ]

    def test_does_not_merge_across_a_gap_even_if_same_rate(self):
        """2026-01/2026-03 同利率但中间月份缺失(非连续), 不该被合并成一个区间."""
        rates = {"2026-01": 0.045, "2026-03": 0.045}
        assert group_rate_periods(rates) == [
            {"start_month": "2026-01", "end_month": "2026-01", "rate": 0.045},
            {"start_month": "2026-03", "end_month": "2026-03", "rate": 0.045},
        ]

    def test_empty_input(self):
        assert group_rate_periods({}) == []


@pytest.mark.unit
def test_upsert_rba_rates(db_session):
    """写入利率表，重复 date_period 覆盖而非报错。"""
    rates = {"2026-03": 0.0435, "2026-04": 0.0410}
    count = upsert_rba_rates(db_session, rates)
    assert count == 2
    assert db_session.get(RbaCashRate, "2026-03").rate == pytest.approx(0.0435)

    # 覆盖更新
    count = upsert_rba_rates(db_session, {"2026-03": 0.0400})
    assert db_session.get(RbaCashRate, "2026-03").rate == pytest.approx(0.0400)
    assert db_session.query(RbaCashRate).count() == 2  # 未新增
