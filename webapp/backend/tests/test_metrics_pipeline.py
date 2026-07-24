"""指标编排管道端到端测试。"""
import pytest

from app.models import Fund, FundMetric, Anomaly, RbaCashRate
from app.crud import create_fund, upsert_monthly_return
from app.metrics_pipeline import compute_and_store_metrics, _find_month_gaps, recompute_all_funds


@pytest.mark.unit
def test_compute_and_store_metrics_short_history(db_session):
    """不足36个月的基金：写入 fund_metrics，is_short_history_warning=1。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    for i, r in enumerate([0.005, 0.006, 0.004, 0.007, 0.005, 0.006]):
        upsert_monthly_return(db_session, "f1", f"2025-{i+1:02d}-28", r)

    for i in range(1, 7):
        db_session.add(RbaCashRate(date_period=f"2025-{i:02d}", rate=0.0435))
    db_session.commit()

    metrics = compute_and_store_metrics(db_session, "f1")
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

    for m in range(1, 13):
        db_session.add(RbaCashRate(date_period=f"2025-{m:02d}", rate=0.0435))
    db_session.add(RbaCashRate(date_period="2026-01", rate=0.0435))
    db_session.commit()

    compute_and_store_metrics(db_session, "f1")

    anomalies = db_session.query(Anomaly).filter_by(fund_id="f1").all()
    assert len(anomalies) == 1
    assert anomalies[0].date == "2026-01-31"
    assert anomalies[0].value == pytest.approx(0.5)


@pytest.mark.unit
def test_compute_and_store_metrics_uses_db_rba(db_session):
    """数据库中的 RBA 利率被正确用于超额收益计算。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "f1", "2026-01-31", 0.01)
    db_session.add(RbaCashRate(date_period="2026-01", rate=0.0435))
    db_session.commit()

    metrics = compute_and_store_metrics(db_session, "f1")
    excess = 0.01 - 0.0435 / 12.0
    expected_ann = (1.0 + excess) ** 12 - 1
    assert metrics["orig_annualized_excess_return"] == pytest.approx(expected_ann, rel=1e-6)


@pytest.mark.unit
def test_compute_and_store_metrics_rba_missing_excluded(db_session):
    """RBA 缺失月：剔除于超额序列 + 写 rba_missing 异常 + 继续计算（PDD 1.7 / 决策1）。

    基金自身月度缺口仍零容忍（见 test_compute_and_store_metrics_gap_detection）。
    """
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "f1", "2026-01-31", 0.01)
    # 不写入 RBA 利率
    db_session.commit()

    metrics = compute_and_store_metrics(db_session, "f1")
    # 计算继续：基金 1 月，超额有效月数=0（RBA 缺失）
    assert metrics["history_months"] == 1
    assert metrics["excess_sample_months"] == 0
    # rba_missing 异常落库
    anomalies = db_session.query(Anomaly).filter_by(fund_id="f1").all()
    assert len(anomalies) == 1
    assert anomalies[0].type == "rba_missing"
    assert anomalies[0].reason is not None
    assert anomalies[0].date == "2026-01-31"
    assert anomalies[0].value is None  # 数值字段为 None


@pytest.mark.unit
def test_compute_and_store_metrics_idempotent(db_session):
    """重复调用不产生重复记录（upsert）。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "f1", "2026-01-31", 0.01)
    db_session.add(RbaCashRate(date_period="2026-01", rate=0.0435))
    db_session.commit()

    compute_and_store_metrics(db_session, "f1")
    compute_and_store_metrics(db_session, "f1")

    assert db_session.query(FundMetric).filter_by(fund_id="f1").count() == 1


@pytest.mark.unit
def test_find_month_gaps_unit():
    """_find_month_gaps 正确识别缺失月份。"""
    # 连续序列无缺口
    assert _find_month_gaps(["2025-01-31", "2025-02-28", "2025-03-31"]) == []
    # 缺 2025-03
    assert _find_month_gaps(["2025-01-31", "2025-02-28", "2025-04-30"]) == ["2025-03"]
    # 跨年缺口：2025-12 -> 2026-02，缺 2026-01
    assert _find_month_gaps(["2025-12-31", "2026-02-28"]) == ["2026-01"]
    # 单点 / 空列表无缺口
    assert _find_month_gaps(["2025-01-31"]) == []
    assert _find_month_gaps([]) == []


@pytest.mark.unit
def test_compute_and_store_metrics_gap_detection(db_session):
    """月度数据存在缺口时，管道必须报错并列出缺失月份（CLAUDE.md 数据缺口零容忍）。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    # 2025-01、2025-02、2025-04 —— 跳过 2025-03
    upsert_monthly_return(db_session, "f1", "2025-01-31", 0.01)
    upsert_monthly_return(db_session, "f1", "2025-02-28", 0.02)
    upsert_monthly_return(db_session, "f1", "2025-04-30", 0.03)

    with pytest.raises(ValueError) as excinfo:
        compute_and_store_metrics(db_session, "f1")
    msg = str(excinfo.value)
    assert "f1" in msg
    assert "2025-03" in msg
    assert "缺口" in msg
    # 报错后不应写入任何指标记录
    assert db_session.get(FundMetric, "f1") is None


@pytest.mark.unit
def test_recompute_all_funds_refreshes_stale_cache_after_rba_change(db_session):
    """RBA 利率源变更后（如本次会话的官方源重写），旧 fund_metrics 缓存里的
    orig_annualized_excess_return/sharpe_ratio 是按旧 rf_rates 算的，不会
    自动失效——full 路径的缓存新鲜度校验只比对 date_period（最新月份），不比对
    rf_rates 是否变过。recompute_all_funds 补上这层级联重算。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    for m in range(1, 7):
        upsert_monthly_return(db_session, "f1", f"2025-{m:02d}-28", 0.01)
        db_session.add(RbaCashRate(date_period=f"2025-{m:02d}", rate=0.03))
    db_session.commit()

    compute_and_store_metrics(db_session, "f1")
    stale = db_session.get(FundMetric, "f1")
    stale_excess = stale.orig_annualized_excess_return

    # RBA 源"重写"：同一批月份改用明显不同的利率（模拟本次会话真实发生的场景）
    for m in range(1, 7):
        rate = db_session.get(RbaCashRate, f"2025-{m:02d}")
        rate.rate = 0.06
    db_session.commit()

    result = recompute_all_funds(db_session)
    assert result["recomputed"] == ["f1"]
    assert result["failed"] == []

    db_session.expire_all()
    refreshed = db_session.get(FundMetric, "f1")
    assert refreshed.orig_annualized_excess_return != pytest.approx(stale_excess)


@pytest.mark.unit
def test_recompute_all_funds_skips_gap_fund_without_failing_others(db_session):
    """一支基金有月份缺口时跳过记入 failed，不拖垮其它基金的重算。"""
    create_fund(db_session, fund_id="good", fund_name="Good Fund",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    for m in range(1, 4):
        upsert_monthly_return(db_session, "good", f"2025-{m:02d}-28", 0.01)
        db_session.add(RbaCashRate(date_period=f"2025-{m:02d}", rate=0.03))

    create_fund(db_session, fund_id="gap", fund_name="Gap Fund",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "gap", "2025-01-31", 0.01)
    upsert_monthly_return(db_session, "gap", "2025-03-28", 0.01)  # 缺 2025-02
    db_session.commit()

    result = recompute_all_funds(db_session)
    assert result["recomputed"] == ["good"]
    assert len(result["failed"]) == 1
    assert result["failed"][0]["fund_id"] == "gap"
