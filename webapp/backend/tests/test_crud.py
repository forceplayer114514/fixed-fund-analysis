"""CRUD 与 NAV 重计算测试。"""
import time

import pytest
from sqlalchemy import inspect

from app.models import Fund, MonthlyReturn, RbaCashRate, FundMetric
from app.crud import (
    create_fund, get_fund, get_all_funds, delete_fund,
    upsert_monthly_return, get_returns, recompute_nav, resolve_rf_rates,
    upsert_metrics,
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
    """按月份查 RBA 利率：缺失月返回 None + missing_dates（scoped 例外 PDD 1.7 / 决策1）。"""
    db_session.add(RbaCashRate(date_period="2026-03", rate=0.0435))
    db_session.add(RbaCashRate(date_period="2026-04", rate=0.0410))
    db_session.commit()

    # 缺失月份：返回 None（不抛错），missing_dates 记录缺失的基金日期
    dates = ["2026-03-31", "2026-04-30", "2026-05-31"]  # 5月缺失
    rates, missing = resolve_rf_rates(db_session, dates)
    assert rates[0] == pytest.approx(0.0435)
    assert rates[1] == pytest.approx(0.0410)
    assert rates[2] is None
    assert missing == ["2026-05-31"]

    # 完整月份正常返回，missing 为空
    rates2, missing2 = resolve_rf_rates(db_session, ["2026-03-31", "2026-04-30"])
    assert rates2[0] == pytest.approx(0.0435)
    assert rates2[1] == pytest.approx(0.0410)
    assert missing2 == []


def _minimal_metrics(**overrides) -> dict:
    """构造一份最小合法的 FundMetric 指标 dict（用于 upsert_metrics 测试）。"""
    base = dict(
        date_period="2026-01", history_months=1, excess_sample_months=1,
        is_short_history_warning=1,
        unsmoothing_coefficient_phi=0.0, is_geltner_applied=0,
        orig_annualized_return=0.0, un_annualized_return=0.0,
        orig_annualized_excess_return=0.0, un_annualized_excess_return=0.0,
        orig_max_drawdown=0.0, un_max_drawdown=0.0,
        orig_recovery_months=None, un_recovery_months=None,
        orig_dd_recovered=1, un_dd_recovered=1,
        orig_information_ratio=None, un_information_ratio=None,
        orig_excess_win_rate=0.5, un_excess_win_rate=0.5,
        orig_max_underperform_months=1, un_max_underperform_months=1,
        orig_annualized_volatility=0.01, un_annualized_volatility=0.01,
        ljung_box_q=0.0, is_q_significant=0,
    )
    base.update(overrides)
    return base


@pytest.mark.unit
def test_upsert_metrics_refreshes_updated_at(db_session):
    """upsert_metrics 更新已有记录时，updated_at 应被 onupdate 刷新（Fix 2）。

    FundMetric.updated_at 配置了 onupdate=text("(datetime('now'))")，当
    upsert_metrics 走 setattr 更新分支时，SQLAlchemy 会把 updated_at 加入
    UPDATE 的 SET 子句，从而刷新时间戳。
    """
    # 结构性断言：列上确实配置了 onupdate
    col = inspect(FundMetric).columns["updated_at"]
    assert col.onupdate is not None, "FundMetric.updated_at 未配置 onupdate"

    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    # 第一次：插入
    upsert_metrics(db_session, "f1", _minimal_metrics())
    t1 = db_session.get(FundMetric, "f1").updated_at

    # SQLite datetime('now') 精度为秒，sleep 1.1s 确保时间戳发生变化
    time.sleep(1.1)

    # 第二次：更新（走 setattr 分支，触发 onupdate）
    upsert_metrics(db_session, "f1", _minimal_metrics(orig_information_ratio=1.5))
    t2 = db_session.get(FundMetric, "f1").updated_at

    assert t2 != t1, f"updated_at 在 UPDATE 后未刷新: t1={t1!r} t2={t2!r}"


@pytest.mark.unit
def test_rba_cash_rate_updated_at_has_onupdate():
    """RbaCashRate.updated_at 同样配置了 onupdate（Fix 2）。"""
    col = inspect(RbaCashRate).columns["updated_at"]
    assert col.onupdate is not None, "RbaCashRate.updated_at 未配置 onupdate"
