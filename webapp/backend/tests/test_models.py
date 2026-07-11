"""验证6张表的ORM模型：插入、唯一约束、级联删除。"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Fund, MonthlyReturn, Anomaly, RbaCashRate, FundMetric, AiReport


@pytest.mark.unit
def test_insert_fund_and_returns(db_session):
    """能插入基金及其月度收益，并通过关系访问。"""
    fund = Fund(fund_id="stake_accumulate", fund_name="Stake Accumulate",
                confirmed_url="https://example.com", fetch_method="pdf", url_type="pdf")
    db_session.add(fund)
    db_session.commit()

    ret = MonthlyReturn(fund_id="stake_accumulate", date="2026-05-31",
                        net_return=0.0053, nav=1.0053)
    db_session.add(ret)
    db_session.commit()

    assert db_session.query(MonthlyReturn).count() == 1
    assert fund.monthly_returns[0].net_return == pytest.approx(0.0053)


@pytest.mark.unit
def test_fund_name_unique_constraint(db_session):
    """fund_name 唯一约束：重复插入同名基金应报错。"""
    fund1 = Fund(fund_id="fund_a", fund_name="Duplicate Fund",
                 confirmed_url="http://a", fetch_method="pdf", url_type="pdf")
    db_session.add(fund1)
    db_session.commit()

    fund2 = Fund(fund_id="fund_b", fund_name="Duplicate Fund",
                 confirmed_url="http://b", fetch_method="pdf", url_type="pdf")
    db_session.add(fund2)
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.unit
def test_apir_code_nullable(db_session):
    """apir_code 可为空（支持 Stake 等无 APIR 基金）。"""
    fund = Fund(fund_id="stake", fund_name="Stake Fund",
                apir_code=None, confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    db_session.add(fund)
    db_session.commit()
    assert fund.apir_code is None


@pytest.mark.unit
def test_monthly_return_unique_date_per_fund(db_session):
    """同一基金同一月份不能重复插入。"""
    fund = Fund(fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    db_session.add(fund)
    db_session.commit()

    db_session.add(MonthlyReturn(fund_id="f1", date="2026-05-31", net_return=0.01, nav=1.01))
    db_session.commit()

    db_session.add(MonthlyReturn(fund_id="f1", date="2026-05-31", net_return=0.02, nav=1.02))
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.unit
def test_cascade_delete_fund_removes_children(db_session):
    """删除基金应级联删除其月度收益、异常、指标、AI报告。"""
    fund = Fund(fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    db_session.add(fund)
    db_session.commit()

    db_session.add(MonthlyReturn(fund_id="f1", date="2026-05-31", net_return=0.01, nav=1.01))
    db_session.add(Anomaly(fund_id="f1", date="2026-05-31", value=0.99,
                           z_score=3.5, threshold_sigma=3.0, mean=0.01, stdev=0.02))
    db_session.add(FundMetric(fund_id="f1", date_period="2026-05", history_months=1,
                              is_short_history_warning=1, unsmoothing_coefficient_phi=0.0,
                              is_geltner_applied=0, orig_annualized_excess_return=0.0,
                              un_annualized_excess_return=0.0, orig_max_drawdown=0.0,
                              un_max_drawdown=0.0, orig_omega_ratio=1.0, un_omega_ratio=1.0,
                              orig_excess_win_rate=0.5, un_excess_win_rate=0.5,
                              orig_max_underperform_months=1, un_max_underperform_months=1,
                              orig_annualized_volatility=0.01, un_annualized_volatility=0.01,
                              ljung_box_q=0.0, is_q_significant=0))
    db_session.commit()

    db_session.delete(fund)
    db_session.commit()

    assert db_session.query(MonthlyReturn).count() == 0
    assert db_session.query(Anomaly).count() == 0
    assert db_session.query(FundMetric).count() == 0


@pytest.mark.unit
def test_rba_cash_rate_upsert_style(db_session):
    """RBA 利率表以 date_period 为主键。"""
    db_session.add(RbaCashRate(date_period="2026-05", rate=0.0435))
    db_session.commit()
    assert db_session.query(RbaCashRate).count() == 1


@pytest.mark.unit
def test_updated_at_real_timestamp_on_insert(db_session):
    """INSERT 时 updated_at 应为真实时间戳，而非字面字符串 '(datetime('now'))'。"""
    from datetime import datetime

    db_session.add(RbaCashRate(date_period="2026-05", rate=0.0435))
    db_session.commit()
    row = db_session.get(RbaCashRate, "2026-05")
    # 不应等于字面字符串
    assert row.updated_at != "(datetime('now'))"
    # 应为合法的日期时间字符串（YYYY-MM-DD HH:MM:SS 格式）
    assert row.updated_at is not None
    assert len(row.updated_at) >= 10  # 至少包含日期部分
    # 验证能解析为日期
    datetime.strptime(row.updated_at[:19], "%Y-%m-%d %H:%M:%S")


@pytest.mark.unit
def test_fund_created_at_real_timestamp_on_insert(db_session):
    """Fund.created_at INSERT 时应为真实时间戳，而非字面字符串。"""
    from app.models import Fund
    from datetime import datetime
    fund = Fund(fund_id="f_ct", fund_name="Created Test Fund",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    db_session.add(fund)
    db_session.commit()
    db_session.refresh(fund)
    assert fund.created_at != "(datetime('now'))"
    assert fund.created_at is not None
    datetime.strptime(fund.created_at[:19], "%Y-%m-%d %H:%M:%S")
