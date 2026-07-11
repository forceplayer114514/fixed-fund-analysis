"""CRUD 与 NAV 重计算测试。"""
import pytest

from app.models import Fund, MonthlyReturn, RbaCashRate
from app.crud import (
    create_fund, get_fund, get_all_funds, delete_fund,
    upsert_monthly_return, get_returns, recompute_nav, resolve_rf_rates,
)


@pytest.mark.unit
def test_create_and_get_fund(db_session):
    fund = create_fund(db_session, fund_id="f1", fund_name="Fund One",
                       confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    assert fund.fund_id == "f1"
    assert get_fund(db_session, "f1").fund_name == "Fund One"
    assert get_fund(db_session, "nonexistent") is None


@pytest.mark.unit
def test_get_all_funds(db_session):
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    create_fund(db_session, fund_id="f2", fund_name="Fund Two",
                confirmed_url="http://y", fetch_method="pdf", url_type="pdf")
    assert len(get_all_funds(db_session)) == 2


@pytest.mark.unit
def test_delete_fund_cascades(db_session):
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "f1", "2026-05-31", 0.01)
    assert delete_fund(db_session, "f1") is True
    assert get_fund(db_session, "f1") is None
    assert len(get_returns(db_session, "f1")) == 0
    assert delete_fund(db_session, "nonexistent") is False


@pytest.mark.unit
def test_upsert_monthly_return_recompute_nav(db_session):
    """upsert 后 NAV 自动重算：3个月收益 [0.01, 0.02, 0.03] -> NAV [1.01, 1.0302, 1.061306]。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "f1", "2026-03-31", 0.01)
    upsert_monthly_return(db_session, "f1", "2026-04-30", 0.02)
    upsert_monthly_return(db_session, "f1", "2026-05-31", 0.03)

    returns = get_returns(db_session, "f1")
    assert len(returns) == 3
    assert returns[0]["date"] == "2026-03-31"
    # NAV 在数据库中应已重算
    rows = db_session.query(MonthlyReturn).order_by(MonthlyReturn.date).all()
    assert rows[0].nav == pytest.approx(1.01)
    assert rows[1].nav == pytest.approx(1.01 * 1.02)
    assert rows[2].nav == pytest.approx(1.01 * 1.02 * 1.03)


@pytest.mark.unit
def test_recompute_nav_after_mid_insertion(db_session):
    """中途插入历史月份后重算 NAV 应正确级联。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "f1", "2026-05-31", 0.03)
    upsert_monthly_return(db_session, "f1", "2026-03-31", 0.01)
    upsert_monthly_return(db_session, "f1", "2026-04-30", 0.02)

    rows = db_session.query(MonthlyReturn).order_by(MonthlyReturn.date).all()
    assert rows[0].nav == pytest.approx(1.01)
    assert rows[1].nav == pytest.approx(1.01 * 1.02)
    assert rows[2].nav == pytest.approx(1.01 * 1.02 * 1.03)


@pytest.mark.unit
def test_resolve_rf_rates(db_session):
    """按月份查 RBA 利率，缺失用 fallback。"""
    db_session.add(RbaCashRate(date_period="2026-03", rate=0.0435))
    db_session.add(RbaCashRate(date_period="2026-04", rate=0.0410))
    db_session.commit()

    dates = ["2026-03-31", "2026-04-30", "2026-05-31"]  # 5月缺失
    rates = resolve_rf_rates(db_session, dates, fallback_rate=0.0425)
    assert rates[0] == pytest.approx(0.0435)
    assert rates[1] == pytest.approx(0.0410)
    assert rates[2] == pytest.approx(0.0425)  # fallback
