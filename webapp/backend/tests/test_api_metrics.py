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
    # Phase 1 新字段存在
    assert "orig_information_ratio" in body["funds"][0]
    assert "orig_annualized_return" in body["funds"][0]
    assert "excess_sample_months" in body["funds"][0]
    assert "orig_recovery_months" in body["funds"][0]
    # Omega 已移除
    assert "orig_omega_ratio" not in body["funds"][0]
    assert "un_omega_ratio" not in body["funds"][0]


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


@pytest.mark.unit
def test_time_series_returns_aligned_nav(client, db_session):
    _seed_fund_with_data(client, db_session, "f1", "Fund One",
                         [("2026-01-31", 0.01), ("2026-02-28", 0.02), ("2026-03-31", 0.03)])
    resp = client.get("/api/metrics/time-series", params={"fund_ids": "f1", "period": "full"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["months"] == ["2026-01", "2026-02", "2026-03"]
    s = body["series"][0]
    assert s["fund_id"] == "f1"
    # 原始 NAV: 1.01, 1.0302, 1.061206
    assert s["orig_nav"] == pytest.approx([1.01, 1.01 * 1.02, 1.01 * 1.02 * 1.03], rel=1e-5)
    # 3 个月 < 36，不应去平滑
    assert s["is_geltner_applied"] is False
    assert s["unsm_nav"] is None
    # Phase 2 新字段：逐月 returns + 全局 rba（对齐 months）
    assert s["returns"] == pytest.approx([0.01, 0.02, 0.03])
    assert s["unsm_returns"] is None
    assert "rba" in body
    assert len(body["rba"]) == 3
    assert body["rba"][0] == pytest.approx(0.0435)  # _seed_fund_with_data 写入 0.0435


@pytest.mark.unit
def test_time_series_common_aligns_two_funds(client, db_session):
    _seed_fund_with_data(client, db_session, "f1", "Fund One",
                         [("2026-01-31", 0.01), ("2026-02-28", 0.02), ("2026-03-31", 0.03)])
    _seed_fund_with_data(client, db_session, "f2", "Fund Two",
                         [("2026-02-28", 0.02), ("2026-03-31", 0.03), ("2026-04-30", 0.04)])
    resp = client.get("/api/metrics/time-series", params={"fund_ids": "f1,f2", "period": "common"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["months"] == ["2026-02", "2026-03"]
    assert len(body["series"]) == 2
    # f1 在共同区间的 orig_nav: 从 2026-02 起重新基数为 1.0 -> [1.02, 1.02*1.03]
    assert body["series"][0]["orig_nav"] == pytest.approx([1.02, 1.02 * 1.03], rel=1e-5)


@pytest.mark.unit
def test_time_series_rba_null_for_missing_month(client, db_session):
    """RBA 缺失月：time-series 的 rba 数组对应位为 null（不抛错，PDD 1.7 scoped）。"""
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    db_session.add(MonthlyReturn(fund_id="f1", date="2026-01-31", net_return=0.01, nav=1.0))
    db_session.add(MonthlyReturn(fund_id="f1", date="2026-02-28", net_return=0.02, nav=1.0))
    # 只写 2026-01 的 RBA，2026-02 缺失
    db_session.add(RbaCashRate(date_period="2026-01", rate=0.0435))
    db_session.commit()
    resp = client.get("/api/metrics/time-series", params={"fund_ids": "f1", "period": "full"})
    assert resp.status_code == 200
    assert resp.json()["rba"] == [pytest.approx(0.0435), None]


@pytest.mark.unit
def test_time_series_geltner_nav_when_applied(client, db_session):
    """足够长且自相关显著的序列，去平滑 NAV 应返回（非 null）。"""
    # 平滑收敛序列 phi≈0.7，触发 Geltner（n=60, Q≈30>3.841, 0<=phi<=0.85）
    data = []
    val = 0.0
    for i in range(60):
        year = 2021 + i // 12
        month = (i % 12) + 1
        ym = f"{year}-{month:02d}-28"
        val = val + (0.01 - val) * 0.3  # 平滑收敛到 0.01
        data.append((ym, val))
    _seed_fund_with_data(client, db_session, "f1", "SmoothFund", data)
    resp = client.get("/api/metrics/time-series", params={"fund_ids": "f1", "period": "full"})
    assert resp.status_code == 200
    s = resp.json()["series"][0]
    assert s["is_geltner_applied"] is True
    assert s["unsm_nav"] is not None
    assert len(s["unsm_nav"]) == len(s["orig_nav"])


def _seed_fund_with_gap(client, db_session, fund_id, name):
    """注册基金 + 写入有缺口的月度数据（缺 2026-02）。"""
    client.post("/api/funds", json={"fund_id": fund_id, "fund_name": name,
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    for ym, r in [("2026-01-31", 0.01), ("2026-03-31", 0.03)]:
        db_session.add(MonthlyReturn(fund_id=fund_id, date=ym, net_return=r, nav=1.0))
        if db_session.get(RbaCashRate, ym[:7]) is None:
            db_session.add(RbaCashRate(date_period=ym[:7], rate=0.0435))
    db_session.commit()


@pytest.mark.unit
def test_compare_excludes_fund_with_gap(client, db_session):
    """缺口基金不拖垮整批：compare 跳过缺口基金进 excluded，其余正常返回（robustness）。"""
    _seed_fund_with_data(client, db_session, "f1", "Fund One",
                         [("2026-01-31", 0.01), ("2026-02-28", 0.02), ("2026-03-31", 0.03)])
    _seed_fund_with_gap(client, db_session, "f2", "Gap Fund")
    resp = client.get("/api/metrics/compare", params={"fund_ids": "f1,f2", "period": "full"})
    assert resp.status_code == 200
    body = resp.json()
    fund_ids = [f["fund_id"] for f in body["funds"]]
    assert "f1" in fund_ids
    assert "f2" not in fund_ids
    excl = {e["fund_id"] for e in body["excluded"]}
    assert "f2" in excl


@pytest.mark.unit
def test_time_series_excludes_fund_with_gap(client, db_session):
    """缺口基金时序也降级：跳过进 excluded，不 422。"""
    _seed_fund_with_data(client, db_session, "f1", "Fund One",
                         [("2026-01-31", 0.01), ("2026-02-28", 0.02), ("2026-03-31", 0.03)])
    _seed_fund_with_gap(client, db_session, "f2", "Gap Fund")
    resp = client.get("/api/metrics/time-series", params={"fund_ids": "f1,f2", "period": "full"})
    assert resp.status_code == 200
    body = resp.json()
    series_ids = [s["fund_id"] for s in body["series"]]
    assert "f1" in series_ids
    assert "f2" not in series_ids
    excl = {e["fund_id"] for e in body["excluded"]}
    assert "f2" in excl
