"""指标编排管道端到端测试。"""
import pytest

from app.models import Fund, FundMetric, Anomaly, RbaCashRate
from app.crud import create_fund, upsert_monthly_return
from app.metrics_pipeline import compute_and_store_metrics


@pytest.mark.unit
def test_compute_and_store_metrics_short_history(db_session):
    """不足36个月的基金：写入 fund_metrics，is_short_history_warning=1。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    for i, r in enumerate([0.005, 0.006, 0.004, 0.007, 0.005, 0.006]):
        upsert_monthly_return(db_session, "f1", f"2025-{i+1:02d}-28", r)

    db_session.add(RbaCashRate(date_period="2025-01", rate=0.0435))
    db_session.commit()

    metrics = compute_and_store_metrics(db_session, "f1", fallback_rba_rate=0.0435)
    assert metrics["history_months"] == 6
    assert metrics["is_short_history_warning"] == 1

    # 验证已写入数据库
    stored = db_session.get(FundMetric, "f1")
    assert stored is not None
    assert stored.is_short_history_warning == 1
    assert stored.orig_annualized_volatility == pytest.approx(metrics["orig_annualized_volatility"])


@pytest.mark.unit
def test_compute_and_store_metrics_anomalies_persisted(db_session):
    """异常值检测后写入 anomalies 表。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    # 12个正常值 + 1个极端值
    for m in range(1, 13):
        upsert_monthly_return(db_session, "f1", f"2025-{m:02d}-28", 0.005)
    upsert_monthly_return(db_session, "f1", "2026-01-31", 0.5)

    compute_and_store_metrics(db_session, "f1", fallback_rba_rate=0.0435)

    anomalies = db_session.query(Anomaly).filter_by(fund_id="f1").all()
    assert len(anomalies) == 1
    assert anomalies[0].date == "2026-01-31"
    assert anomalies[0].value == pytest.approx(0.5)


@pytest.mark.unit
def test_compute_and_store_metrics_uses_db_rba_over_fallback(db_session):
    """数据库中有 RBA 利率时优先使用，而非 fallback。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "f1", "2026-01-31", 0.01)
    db_session.add(RbaCashRate(date_period="2026-01", rate=0.0435))
    db_session.commit()

    metrics = compute_and_store_metrics(db_session, "f1", fallback_rba_rate=0.0999)
    # 应使用 DB 中的 0.0435 而非 fallback 0.0999
    excess = 0.01 - 0.0435 / 12.0
    expected_ann = (1.0 + excess) ** 12 - 1
    assert metrics["orig_annualized_excess_return"] == pytest.approx(expected_ann, rel=1e-6)


@pytest.mark.unit
def test_compute_and_store_metrics_idempotent(db_session):
    """重复调用不产生重复记录（upsert）。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "f1", "2026-01-31", 0.01)

    compute_and_store_metrics(db_session, "f1", fallback_rba_rate=0.0435)
    compute_and_store_metrics(db_session, "f1", fallback_rba_rate=0.0435)

    assert db_session.query(FundMetric).filter_by(fund_id="f1").count() == 1
