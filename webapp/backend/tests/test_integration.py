"""端到端集成：注册->数据->重算->对比->时序->纠错->删除同步。"""
import pytest
from app.models import MonthlyReturn, RbaCashRate


@pytest.mark.unit
def test_full_workflow(client, db_session):
    # 1. 注册两只基金
    for fid, name in [("f1", "Fund Alpha"), ("f2", "Fund Beta")]:
        client.post("/api/funds", json={"fund_id": fid, "fund_name": name,
                     "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})

    # 2. 模拟 skill 写入月度数据（12 个月）+ RBA 利率
    for fid in ["f1", "f2"]:
        for m in range(1, 13):
            db_session.add(MonthlyReturn(fund_id=fid, date=f"2025-{m:02d}-28",
                                         net_return=0.005 + (0.001 if fid == "f1" else 0),
                                         nav=1.0))
    for m in range(1, 13):
        db_session.add(RbaCashRate(date_period=f"2025-{m:02d}", rate=0.0435))
    db_session.commit()

    # 3. 重算指标
    for fid in ["f1", "f2"]:
        r = client.post(f"/api/funds/{fid}/recompute")
        assert r.status_code == 200

    # 4. 对比（full）
    resp = client.get("/api/metrics/compare", params={"fund_ids": "f1,f2", "period": "full"})
    assert resp.status_code == 200
    assert len(resp.json()["funds"]) == 2

    # 5. 时序
    resp = client.get("/api/metrics/time-series", params={"fund_ids": "f1,f2", "period": "full"})
    assert resp.status_code == 200
    assert len(resp.json()["series"]) == 2

    # 6. 异常列表（12 个月正常数据应无异常）
    anomalies = client.get("/api/anomalies").json()
    assert len(anomalies) == 0

    # 7. 删除一只基金，确认列表更新（模拟"网页删除"同步）
    assert client.delete("/api/funds/f2").status_code == 204
    funds = client.get("/api/funds").json()
    assert len(funds) == 1
    assert funds[0]["fund_id"] == "f1"


@pytest.mark.unit
def test_health_and_openapi_schema(client):
    """健康检查与 OpenAPI schema 可用（前端代码生成的依据）。"""
    assert client.get("/health").status_code == 200
    assert client.get("/openapi.json").status_code == 200
