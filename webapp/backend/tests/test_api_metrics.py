"""metrics 对比与时序 API 测试。"""
import pytest
from app.models import MonthlyReturn, RbaCashRate


def _seed_fund_with_data(client, db_session, fund_id, name, returns_by_month):
    """辅助：注册基金 + 写入月度数据 + RBA。"""
    client.post("/api/funds", json={"fund_id": fund_id, "fund_name": name,
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    for ym, r in returns_by_month:
        db_session.add(MonthlyReturn(fund_id=fund_id, date=ym, net_return=r, nav=1.0))
        month_key = ym[:7]
        if db_session.get(RbaCashRate, month_key) is None:
            db_session.add(RbaCashRate(date_period=month_key, rate=0.0435))
    db_session.commit()


@pytest.mark.unit
def test_compare_full_reads_cached_metrics(client, db_session):
    _seed_fund_with_data(client, db_session, "f1", "Fund One",
                         [("2026-01-31", 0.01), ("2026-02-28", 0.02)])
    client.post("/api/funds/f1/recompute")  # 预计算 fund_metrics
    resp = client.get("/api/metrics/compare", params={"fund_ids": "f1", "period": "full"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["funds"][0]["fund_id"] == "f1"
    assert body["funds"][0]["history_months"] == 2


@pytest.mark.unit
def test_compare_3y_recomputes_on_slice(client, db_session):
    # 48 个月数据，3y 切片应只用最后 36 个
    data = [(f"2022-{m:02d}-28", 0.005) for m in range(1, 13)]
    data += [(f"2023-{m:02d}-28", 0.005) for m in range(1, 13)]
    data += [(f"2024-{m:02d}-28", 0.005) for m in range(1, 13)]
    data += [(f"2025-{m:02d}-28", 0.005) for m in range(1, 13)]
    _seed_fund_with_data(client, db_session, "f1", "Fund One", data)
    resp = client.get("/api/metrics/compare", params={"fund_ids": "f1", "period": "3y"})
    assert resp.status_code == 200
    assert resp.json()["funds"][0]["history_months"] == 36


@pytest.mark.unit
def test_compare_common_aligns_multiple_funds(client, db_session):
    _seed_fund_with_data(client, db_session, "f1", "Fund One",
                         [("2026-01-31", 0.01), ("2026-02-28", 0.02), ("2026-03-31", 0.03)])
    _seed_fund_with_data(client, db_session, "f2", "Fund Two",
                         [("2026-02-28", 0.02), ("2026-03-31", 0.03), ("2026-04-30", 0.04)])
    resp = client.get("/api/metrics/compare", params={"fund_ids": "f1,f2", "period": "common"})
    assert resp.status_code == 200
    funds = resp.json()["funds"]
    # 两基金共同区间为 2026-02、2026-03，各 2 个月
    assert all(f["history_months"] == 2 for f in funds)


@pytest.mark.unit
def test_compare_unknown_fund_returns_404(client):
    resp = client.get("/api/metrics/compare", params={"fund_ids": "ghost", "period": "full"})
    assert resp.status_code == 404
