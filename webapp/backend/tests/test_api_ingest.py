"""GET /api/ingest/jobs/active 单测.

直接操作 app.routers.ingest._JOBS (进程内内存字典) 构造场景, 不需要真的起
摄取线程 -- 该端点只是把 _JOBS 里非终态的 job 过滤+投影, 与 DB 无关。
"""
import pytest

from app.routers import ingest as ing


@pytest.fixture(autouse=True)
def _clean_jobs():
    """每个测试前后清空 _JOBS, 避免用例间串状态 (模块级字典, 不随 client fixture 重置)."""
    ing._JOBS.clear()
    yield
    ing._JOBS.clear()


@pytest.mark.unit
def test_active_jobs_lists_non_terminal(client):
    jid = ing._job_new("fund_a")
    ing._job_update(jid, state="ingesting_l2_pdf")

    resp = client.get("/api/ingest/jobs/active")
    assert resp.status_code == 200
    body = resp.json()
    assert body == [{"job_id": jid, "fund_id": "fund_a", "state": "ingesting_l2_pdf"}]


@pytest.mark.unit
def test_active_jobs_filters_out_terminal(client):
    jid_done = ing._job_new("fund_b")
    ing._job_update(jid_done, state="succeeded")
    jid_failed = ing._job_new("fund_c")
    ing._job_update(jid_failed, state="failed")
    jid_active = ing._job_new("fund_d")
    ing._job_update(jid_active, state="queued")

    resp = client.get("/api/ingest/jobs/active")
    body = resp.json()
    assert [j["fund_id"] for j in body] == ["fund_d"]


@pytest.mark.unit
def test_active_jobs_empty_list_when_none_active(client):
    resp = client.get("/api/ingest/jobs/active")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.unit
def test_active_jobs_route_not_shadowed_by_job_id_route(client):
    """/active 必须命中 list_active_jobs, 不能被 /{job_id} 动态路由截获."""
    resp = client.get("/api/ingest/jobs/active")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
