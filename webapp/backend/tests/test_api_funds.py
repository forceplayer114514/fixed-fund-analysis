"""FastAPI 骨架测试。"""
import pytest


@pytest.mark.unit
def test_health_endpoint(client):
    """GET /health 返回 200。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
