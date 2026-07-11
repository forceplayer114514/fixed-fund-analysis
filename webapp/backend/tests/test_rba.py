"""RBA 利率抓取测试。网络请求用 unittest.mock.patch mock，不发起真实请求。"""
import pytest
from unittest.mock import patch, MagicMock

from app.rba import fetch_current_rba_rate, fetch_historical_rba_rates, upsert_rba_rates
from app.models import RbaCashRate


@pytest.mark.unit
def test_fetch_current_rba_rate_parses_html():
    """从 RBA 首页 HTML 解析现金利率。"""
    fake_html = '''
    <html><body><article>
      <span>Cash rate target</span>
      <p class="statistic-value">4.35%</p>
    </article></body></html>
    '''
    mock_resp = MagicMock()
    mock_resp.text = fake_html
    mock_resp.raise_for_status = MagicMock()
    with patch("app.rba.requests.get", return_value=mock_resp):
        rate = fetch_current_rba_rate()
    assert rate == pytest.approx(0.0435)


@pytest.mark.unit
def test_fetch_historical_rba_rates_parses_dbnomics():
    """从 DBnomics API 响应解析历史利率。"""
    fake_json = {
        "series": {"docs": [{
            "period": ["2026-03", "2026-04", "2026-05"],
            "value": ["4.35", "4.10", "4.10"]
        }]}
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_json
    mock_resp.raise_for_status = MagicMock()
    with patch("app.rba.requests.get", return_value=mock_resp):
        rates = fetch_historical_rba_rates()
    assert rates["2026-03"] == pytest.approx(0.0435)
    assert rates["2026-04"] == pytest.approx(0.0410)
    assert rates["2026-05"] == pytest.approx(0.0410)


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
