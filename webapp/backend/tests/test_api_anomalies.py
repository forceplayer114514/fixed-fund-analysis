"""异常审计与人工纠错 API 测试。"""
import pytest
from app.models import MonthlyReturn, RbaCashRate


def _seed_with_outlier(client, db_session):
    """注册基金 + 12 个月正常数据(0.005) + 1 个极端值(0.5)，recompute 触发异常检测。"""
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    for m in range(1, 13):
        db_session.add(MonthlyReturn(fund_id="f1", date=f"2025-{m:02d}-28",
                                     net_return=0.005, nav=1.0))
        db_session.add(RbaCashRate(date_period=f"2025-{m:02d}", rate=0.0435))
    db_session.add(MonthlyReturn(fund_id="f1", date="2026-01-31", net_return=0.5, nav=1.0))
    db_session.add(RbaCashRate(date_period="2026-01", rate=0.0435))
    db_session.commit()
    client.post("/api/funds/f1/recompute")  # 触发异常检测写入 anomalies


@pytest.mark.unit
def test_list_anomalies(client, db_session):
    _seed_with_outlier(client, db_session)
    resp = client.get("/api/anomalies")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    assert body[0]["fund_name"] == "Fund One"
    assert body[0]["value"] == pytest.approx(0.5)
    assert body[0]["type"] == "return_outlier"


@pytest.mark.unit
def test_list_anomalies_includes_rba_missing(client, db_session):
    """RBA 缺失月：recompute 后 GET /api/anomalies 出现 type=rba_missing，
    monthly_return_id=None（纠错无意义）、数值字段 None（修正4 验证点）。"""
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    db_session.add(MonthlyReturn(fund_id="f1", date="2026-01-31", net_return=0.01, nav=1.0))
    # 故意不写 RBA 利率
    db_session.commit()
    client.post("/api/funds/f1/recompute")

    resp = client.get("/api/anomalies")
    assert resp.status_code == 200
    rba = [a for a in resp.json() if a["type"] == "rba_missing"]
    assert len(rba) == 1
    assert rba[0]["date"] == "2026-01-31"
    assert rba[0]["reason"] is not None
    assert rba[0]["monthly_return_id"] is None  # 禁用纠错
    assert rba[0]["value"] is None  # 数值字段为 None


@pytest.mark.unit
def test_patch_monthly_return_corrects_and_recomputes(client, db_session):
    _seed_with_outlier(client, db_session)
    mr = db_session.query(MonthlyReturn).filter_by(
        fund_id="f1", date="2026-01-31").first()
    # 人工纠错：把 0.5 改回 0.005
    resp = client.patch(f"/api/monthly-returns/{mr.id}",
                        json={"net_return": 0.005})
    assert resp.status_code == 200
    # 改后异常应消失（重算后 0.005 不再是异常）
    anomalies = client.get("/api/anomalies").json()
    assert len(anomalies) == 0
    # NAV 已重算
    db_session.expire_all()
    mr2 = db_session.get(MonthlyReturn, mr.id)
    assert mr2.net_return == pytest.approx(0.005)


@pytest.mark.unit
def test_patch_nonexistent_returns_404(client):
    resp = client.patch("/api/monthly-returns/99999", json={"net_return": 0.01})
    assert resp.status_code == 404
