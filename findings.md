# Findings & Decisions (阶段 2)

## 阶段 1 已实现接口契约（复用，不重写）
- **models.py**: Fund, MonthlyReturn, Anomaly, RbaCashRate, FundMetric, AiReport（6表）。fund_name UNIQUE, apir_code nullable+UNIQUE, 子表 ondelete=CASCADE。
- **calculations.py**: `compute_all_metrics(returns, rf_rates, fund_name) -> dict`（18键，与 FundMetric 字段一致，不含 fund_id/updated_at）。含 calculate_autocorrelation, unsmooth_returns, should_apply_geltner, _build_nav_series, LJUNG_BOX_CRITICAL_VALUE=3.841。
- **anomaly.py**: `detect_anomalies(time_series, threshold_sigma=3.0) -> list[dict]`（MAD）。
- **crud.py**: create_fund, get_fund, get_all_funds, delete_fund, upsert_monthly_return(含 recompute_nav), get_returns, recompute_nav, resolve_rf_rates, replace_anomalies, upsert_metrics。
- **rba.py**: fetch_current_rba_rate() -> float, fetch_historical_rba_rates() -> dict[str,float], upsert_rba_rates(session, rates) -> int。
- **metrics_pipeline.py**: `compute_and_store_metrics(session, fund_id, fallback_rba_rate=None) -> dict`（含 _find_month_gaps 缺口检查，缺口抛 ValueError）。
- **database.py**: engine, SessionLocal, init_db(), get_db(), Base。
- **config.py**: settings (DATABASE_URL, RBA_BASE_URL, RBA_HISTORY_API)。

## 环境约束
- Python 3.9.6，Optional[X]，python3/pip3
- 阶段 1 共 43 测试通过（在 main）

## 关键计算细节
- 超额收益：逐月扣减 RBA 复利年化 (spec 4.1)：r_e,t = r_t - RBA_t/12，AnnExcess = (∏(1+r_e))^(12/n) - 1
- Geltner 三重防火墙：n>=36, Q>3.841, 0<=phi<=0.85
- Omega：分母0返回 inf
- compute_all_metrics 返回键名与 FundMetric 字段一致

## Task 2 发现（影响后续 Task）
- **Starlette 同步端点在 threadpool 执行**：内存 SQLite 默认每线程独立 DB -> 'no such table'。修复：conftest 用 `StaticPool + check_same_thread=False`。
- **Omega=inf 不 JSON 合规**：Omega 比率无跑输月时为 inf，FastAPI JSONResponse 拒绝 inf（Out of range float）。修复：`schemas.sanitize_for_json` 递归转 inf/NaN -> None。**compare/time-series 端点也须复用此函数**。

## 已知问题（下迭代）
- **PATCH 人工纠错事务非原子**：`PATCH /api/monthly-returns/{id}` 中 upsert_monthly_return 内部先 commit（净值+NAV 持久化），再调 compute_and_store_metrics。若后者抛 ValueError（极端净值致 peak<1e-4 或去平滑分母过低），返回 400 但净值已改、指标仍是旧值。触发条件边缘，下迭代改为同事务或先验证后 commit。

## Resources
- spec: docs/superpowers/specs/2026-07-10-fund-web-ui-design.md
- 阶段2计划: docs/superpowers/plans/2026-07-11-fastapi-api-layer.md
- 阶段1计划: docs/superpowers/plans/2026-07-10-backend-foundation.md
- 阶段1代码: webapp/backend/app/

## Visual/Browser Findings
- 无
