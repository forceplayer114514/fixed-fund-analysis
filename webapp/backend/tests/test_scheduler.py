"""RBA 调度与手动刷新测试。网络用 monkeypatch mock。"""
import pytest
from sqlalchemy.orm import sessionmaker

from app.scheduler import run_rba_update, start_scheduler, shutdown_scheduler
from app.models import RbaCashRate


@pytest.mark.unit
def test_run_rba_update_fetches_and_upserts(db_session, monkeypatch):
    """run_rba_update 调用 fetch + upsert，返回结果。"""
    monkeypatch.setattr("app.scheduler.fetch_current_rba_rate", lambda: 0.0435)
    monkeypatch.setattr("app.scheduler.fetch_historical_rba_rates",
                        lambda: {"2026-01": 0.0435, "2026-02": 0.0410})

    SessionFactory = sessionmaker(bind=db_session.bind, autocommit=False, autoflush=False)
    result = run_rba_update(SessionFactory)
    assert result["current_rate"] == pytest.approx(0.0435)
    assert result["upserted"] >= 2
    # 验证入库（同一 StaticPool 单连接，db_session 可读到 committed 数据）
    db_session.expire_all()
    assert db_session.get(RbaCashRate, "2026-01").rate == pytest.approx(0.0435)
    assert db_session.get(RbaCashRate, "2026-02").rate == pytest.approx(0.0410)


@pytest.mark.unit
def test_start_scheduler_returns_running_scheduler():
    """start_scheduler 返回一个可关闭的调度器（含 RBA 任务）。"""
    sched = start_scheduler(session_factory=lambda: None)
    try:
        assert sched is not None
        jobs = sched.get_jobs()
        assert len(jobs) >= 1
        assert jobs[0].id == "rba_daily_update"
    finally:
        shutdown_scheduler(sched)


@pytest.mark.unit
def test_rba_refresh_api(client, db_session, monkeypatch):
    """POST /api/rba/refresh 手动触发 RBA 更新。"""
    monkeypatch.setattr("app.rba.fetch_current_rba_rate", lambda: 0.0435)
    monkeypatch.setattr("app.rba.fetch_historical_rba_rates",
                        lambda: {"2026-03": 0.0435})
    resp = client.post("/api/rba/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_rate"] == pytest.approx(0.0435)
    assert body["upserted"] >= 1
