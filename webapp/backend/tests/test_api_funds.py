"""FastAPI 骨架 + 基金 CRUD API 测试。"""
import pytest

from app.models import MonthlyReturn, RbaCashRate


@pytest.mark.unit
def test_health_endpoint(client):
    """GET /health 返回 200。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.unit
def test_create_fund_via_api(client):
    payload = {
        "fund_id": "f1", "fund_name": "Fund One",
        "apir_code": "ETL5010AU", "confirmed_url": "http://x",
        "fetch_method": "pdf", "url_type": "pdf",
    }
    resp = client.post("/api/funds", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["fund_id"] == "f1"
    assert body["apir_code"] == "ETL5010AU"
    assert body["has_metrics"] is False


@pytest.mark.unit
def test_create_fund_duplicate_name_returns_409(client):
    payload = {"fund_id": "f1", "fund_name": "Dup", "confirmed_url": "http://x",
               "fetch_method": "pdf", "url_type": "pdf"}
    client.post("/api/funds", json=payload)
    payload2 = {"fund_id": "f2", "fund_name": "Dup", "confirmed_url": "http://y",
                "fetch_method": "pdf", "url_type": "pdf"}
    resp = client.post("/api/funds", json=payload2)
    assert resp.status_code == 409


@pytest.mark.unit
def test_create_fund_invalid_apir_returns_422(client):
    payload = {"fund_id": "f1", "fund_name": "Bad", "apir_code": "INVALID",
               "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"}
    resp = client.post("/api/funds", json=payload)
    assert resp.status_code == 422


@pytest.mark.unit
def test_create_fund_without_apir_ok(client):
    """Stake 等无 APIR 基金：apir_code 可空。"""
    payload = {"fund_id": "stake", "fund_name": "Stake", "apir_code": None,
               "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"}
    resp = client.post("/api/funds", json=payload)
    assert resp.status_code == 201
    assert resp.json()["apir_code"] is None


@pytest.mark.unit
def test_list_funds_with_cutoff(client, db_session):
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    for m, r in [(1, 0.01), (2, 0.02), (3, 0.03)]:
        db_session.add(MonthlyReturn(fund_id="f1", date=f"2026-{m:02d}-28",
                                     net_return=r, nav=1.0))
    db_session.add(RbaCashRate(date_period="2026-01", rate=0.0435))
    db_session.commit()
    resp = client.get("/api/funds")
    assert resp.status_code == 200
    funds = resp.json()
    assert len(funds) == 1
    assert funds[0]["data_cutoff_month"] == "2026-03"


@pytest.mark.unit
def test_delete_fund(client):
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    resp = client.delete("/api/funds/f1")
    assert resp.status_code == 204
    assert client.get("/api/funds").json() == []
    # 再删不存在 -> 404
    assert client.delete("/api/funds/f1").status_code == 404


@pytest.mark.unit
def test_recompute_fund_metrics(client, db_session):
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    for m, r in [(1, 0.01), (2, 0.02)]:
        db_session.add(MonthlyReturn(fund_id="f1", date=f"2026-{m:02d}-28",
                                     net_return=r, nav=1.0))
    db_session.add(RbaCashRate(date_period="2026-01", rate=0.0435))
    db_session.add(RbaCashRate(date_period="2026-02", rate=0.0435))
    db_session.commit()
    resp = client.post("/api/funds/f1/recompute")
    assert resp.status_code == 200
    body = resp.json()
    assert body["history_months"] == 2
    assert body["is_short_history_warning"] == 1
    # GET /api/funds 现在 has_metrics=True
    assert client.get("/api/funds").json()[0]["has_metrics"] is True


@pytest.mark.unit
def test_recompute_no_data_returns_400(client):
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    resp = client.post("/api/funds/f1/recompute")
    assert resp.status_code == 400


@pytest.mark.unit
def test_list_funds_returns_gap_count(client, db_session):
    """GET /api/funds 透出 gap_count（confirmed_gaps 表行数，数据完整性标记）。"""
    from app.models import ConfirmedGap
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    client.post("/api/funds", json={"fund_id": "f2", "fund_name": "Gap Fund",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    db_session.add(ConfirmedGap(fund_id="f2", missing_month="2026-02"))
    db_session.add(ConfirmedGap(fund_id="f2", missing_month="2026-05"))
    db_session.commit()
    funds = {f["fund_id"]: f for f in client.get("/api/funds").json()}
    assert funds["f1"]["gap_count"] == 0
    assert funds["f2"]["gap_count"] == 2
