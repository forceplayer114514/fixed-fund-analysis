# Progress Log (阶段 2)

## Session: 2026-07-11

### 当前状态
- 阶段 1（后端地基）已完成并合并 main，43 测试通过
- 阶段 2 计划已批准（7 任务，无 LLM）
- 分支 feature/api-layer
- 即将开始 Task 1

### Task 1: FastAPI 骨架 + 配置 + schemas + TestClient
- **Status:** complete
- **Started:** 2026-07-11
- Actions taken:
  - 创建 main.py (create_app 工厂 + lifespan + CORS + /health)
  - 创建 schemas.py (FundCreate/FundResponse/MonthlyReturnPatch/AnomalyResponse, APIR 正则校验)
  - 扩展 config.py (CORS_ORIGINS/SCHEDULER_ENABLED/RBA_CRON_HOUR)
  - 扩展 conftest.py (client fixture, 不触发 lifespan 避免建文件 DB)
  - 创建占位 routers (funds/metrics/anomalies/rba) + scheduler.py 占位
  - 更新 requirements.txt (fastapi/uvicorn/apscheduler/httpx)
- Files: app/main.py, schemas.py, scheduler.py, config.py, routers/{__init__,funds,metrics,anomalies,rba}.py, tests/conftest.py, tests/test_api_funds.py, requirements.txt
- Test: 44 passed (43 阶段1 + 1 health)

### Task 2: 基金 CRUD API + 手动重算
- **Status:** complete
- Actions taken:
  - 实现 routers/funds.py: GET/POST/DELETE /api/funds + POST /{id}/recompute
  - 修复 conftest 内存 DB 跨线程（StaticPool + check_same_thread=False）
  - 加 schemas.sanitize_for_json（inf/NaN -> None，Omega=inf 时 JSON 合规）
- Files: app/routers/funds.py, app/schemas.py, tests/conftest.py, tests/test_api_funds.py
- Test: 52 passed

### Task 3: period 切片 + 指标对比 API
- **Status:** complete
- Actions taken:
  - 实现 period.py (get_common_months, slice_by_period: full/3y/1y/common)
  - 实现 routers/metrics.py compare 端点（full 读缓存，3y/1y/common 切片重算，含 _find_month_gaps 缺口检查 + sanitize_for_json）
  - 修正计划中 test_slice_3y/1y 的 off-by 断言（48 月最后 36 个从 2024-01 起）
  - 修正 _seed_fund_with_data 的 RBA UNIQUE 冲突（写前检查已存在）
- Files: app/period.py, app/routers/metrics.py, tests/test_period.py, tests/test_api_metrics.py
- Test: 62 passed

### Task 4: 时序 API
- **Status:** complete
- Actions taken:
  - 追加 routers/metrics.py time-series 端点（对齐 NAV + 去平滑 NAV，切片后判定 Geltner）
  - 修正测试 Geltner 触发数据：用指数衰减序列（phi≈0.70, Q≈30.7）真正触发，原 AR(1) phi=0.18 不触发
- Files: app/routers/metrics.py, tests/test_api_metrics.py
- Test: 65 passed

### Task 5: 异常审计 + 人工纠错 API
- **Status:** complete
- Actions taken:
  - 实现 routers/anomalies.py: GET /api/anomalies + PATCH /api/monthly-returns/{id}
  - PATCH 触发 upsert_monthly_return + compute_and_store_metrics（重算 NAV + 指标 + 异常）
- Files: app/routers/anomalies.py, tests/test_api_anomalies.py
- Test: 68 passed

### Task 6: RBA 定时调度 + 手动刷新
- **Status:** complete
- Actions taken:
  - 实现 scheduler.py (run_rba_update + start_scheduler + shutdown_scheduler, APScheduler CronTrigger)
  - 实现 routers/rba.py POST /api/rba/refresh
- Files: app/scheduler.py, app/routers/rba.py, tests/test_scheduler.py
- Test: 71 passed（26.95s，APScheduler 启动稍慢）

### Task 7: 端到端集成测试 + README + pytest.ini
- **Status:** complete
- Actions taken:
  - 创建 pytest.ini（注册 unit 标记，消除 72 个警告）
  - 写 test_integration.py（端到端：注册->数据->重算->对比->时序->异常->删除同步 + health/openapi）
  - 创建 README.md（启动说明、环境变量、10 端点）
  - 修复 test_full_workflow RBA UNIQUE 冲突（循环外统一写 RBA）
- Files: pytest.ini, tests/test_integration.py, README.md
- Test: 73 passed, 1 warning

## 阶段 2 全部 7 任务完成
- 总测试: 73 passed（阶段1的43 + 阶段2新增30）
- 10 个 API 端点 + RBA APScheduler 调度
- 下一步: 整支分支 review + 合并 main

### 最终 Review 修复（reviewer 发现 5 Important，修 4 留 1）
- **Status:** complete
- 修复 #1: test_rba_refresh_api patch 目标改 app.routers.rba（原 patch app.rba 不影响 from import 绑定，发真实网络请求 26.95s；现 1.74s）
- 修复 #2: time-series 加 _find_month_gaps 缺口检查（CLAUDE.md 第一条 缺口零容忍）
- 修复 #3: create_app lifespan 改闭包捕获 enable_scheduler（原 settings 恢复后 lifespan 仍启动调度器，with TestClient 会污染仓库 DB）
- 修复 #4: time-series series 加 dates 字段（非 common 多基金对齐，spec 5.1.5）
- 已知问题 #5: PATCH 人工纠错事务非原子（upsert commit 后 recompute 可能失败，净值已改指标未更新）- 下迭代修复
- Test: 73 passed, 1 warning（urllib3 环境警告）

## Test Results
| Test | Status |
|------|--------|

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | 阶段 2 Task 1 起点 |
| Where am I going? | Task 7（集成测试）后合并 main |
| What's the goal? | FastAPI API 层 10 端点 + RBA 调度（无 LLM）|
| What have I learned? | 见 findings.md；LLM 归 skills 侧 |
| What have I done? | 阶段 1 完成，阶段 2 计划批准，分支已建 |
