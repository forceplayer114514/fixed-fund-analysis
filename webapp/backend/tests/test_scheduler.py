"""RBA 调度与手动刷新测试。网络用 monkeypatch mock。"""
import pytest
from sqlalchemy.orm import sessionmaker

from app.scheduler import run_rba_update, start_scheduler, shutdown_scheduler
from app.models import RbaCashRate


@pytest.mark.unit
def test_run_rba_update_fetches_and_upserts(db_session, monkeypatch):
    """run_rba_update 调用 fetch + 展开 + upsert，返回结果。"""
    monkeypatch.setattr(
        "app.scheduler.fetch_rba_rate_history",
        lambda: [("2026-01-15", 0.0435), ("2026-02-04", 0.0410)],
    )

    SessionFactory = sessionmaker(bind=db_session.bind, autocommit=False, autoflush=False)
    result = run_rba_update(SessionFactory)
    assert result["current_rate"] == pytest.approx(0.0410)
    assert result["upserted"] >= 2
    # 验证入库（同一 StaticPool 单连接，db_session 可读到 committed 数据）
    db_session.expire_all()
    assert db_session.get(RbaCashRate, "2026-01").rate == pytest.approx(0.0435)
    # 02 月 4 号生效（28 天：1-3 号旧利率 3 天 + 4-28 号新利率 25 天），按天加权
    feb_expected = (3 * 0.0435 + 25 * 0.0410) / 28
    assert db_session.get(RbaCashRate, "2026-02").rate == pytest.approx(feb_expected)


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
    # patch 目标必须是 routers.rba 模块命名空间（from import 已绑定引用），
    # patch app.rba 不影响 routers.rba 的引用，会导致真实网络请求
    monkeypatch.setattr(
        "app.routers.rba.fetch_rba_rate_history",
        lambda: [("2026-03-04", 0.0435)],
    )
    resp = client.post("/api/rba/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_rate"] == pytest.approx(0.0435)
    assert body["upserted"] >= 1


@pytest.mark.unit
def test_rba_history_api_groups_periods(client, db_session):
    """GET /api/rba/history 按连续相同利率合并区间返回。"""
    db_session.add(RbaCashRate(date_period="2026-01", rate=0.045))
    db_session.add(RbaCashRate(date_period="2026-02", rate=0.045))
    db_session.add(RbaCashRate(date_period="2026-03", rate=0.041))
    db_session.commit()
    resp = client.get("/api/rba/history")
    assert resp.status_code == 200
    assert resp.json() == [
        {"start_month": "2026-01", "end_month": "2026-02", "rate": pytest.approx(0.045)},
        {"start_month": "2026-03", "end_month": "2026-03", "rate": pytest.approx(0.041)},
    ]
