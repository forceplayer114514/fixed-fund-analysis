# Task Plan: 阶段 2 - FastAPI API 层

## Goal
在阶段 1 后端地基上构建 FastAPI 路由层：基金 CRUD、5维指标对比、NAV时序、异常纠错、RBA定时调度（10个端点）。**本阶段不做 LLM**（归 skills/cc 侧）。详细任务步骤见 `docs/superpowers/plans/2026-07-11-fastapi-api-layer.md`。

## Current Phase
All 7 tasks complete - 准备整支 review + 合并 main

## Environment Constraints (每步都须遵守)
- **Python 3.9.6**：用 `Optional[X]`，禁 PEP 604 `X | None` 运行时。文件首行 `from __future__ import annotations`。用 `python3`/`pip3`。
- **数据完整性最高优先级**：不捏造数据，缺口零容忍，异常值保留交人工复核（人工 PATCH 纠错允许，自动纠错禁止）。
- **POST /api/funds 只注册元信息不抓取**（抓取归 skills）。
- **LLM 本阶段不做**（归 skills/cc 侧），`ai_reports` 表保留备用。
- APIR 正则 `^[A-Z]{3}\d{4}AU$`，可空。

## Phases (Tasks)
### Task 1: FastAPI 骨架 + 配置 + schemas + TestClient
- config.py 追加 CORS/SCHEDULER；schemas.py；main.py create_app 工厂+lifespan+CORS+/health；conftest.py client fixture；占位 routers(funds/metrics/anomalies/rba)+scheduler 占位
- **Status:** complete

### Task 2: 基金 CRUD API + 手动重算
- GET/POST/DELETE /api/funds, POST /api/funds/{id}/recompute
- **Status:** complete

### Task 3: period 切片 + 指标对比 API
- period.py (full/3y/1y/common), GET /api/metrics/compare
- **Status:** complete

### Task 4: 时序 API
- GET /api/metrics/time-series (对齐NAV + 去平滑NAV)
- **Status:** complete

### Task 5: 异常审计 + 人工纠错 API
- GET /api/anomalies, PATCH /api/monthly-returns/{id}
- **Status:** complete

### Task 6: RBA 定时调度 + 手动刷新
- scheduler.py (APScheduler), POST /api/rba/refresh
- **Status:** complete

### Task 7: 端到端集成测试 + README + pytest.ini
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|-----------|-----------|
| POST /api/funds 不抓取 | skills 负责抓取，后端只 RBA+计算+API（用户意图）|
| LLM 本阶段不做 | LLM 归 skills/cc 侧，后端不集成 LLM API（用户纠正）|
| period: full 读缓存 / 3y/1y/common 切片重算 | 性能+一致性 |
| 去平滑判定基于切片后序列 | 切片后 n<36 则不去平滑 |
| APScheduler 定时 RBA | 成熟、单进程、lifespan 启停 |
| create_app(enable_scheduler) 工厂 | 测试关闭调度器 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| SQLite 跨线程 no such table（TestClient threadpool）| 1 | conftest 内存 DB 用 StaticPool + check_same_thread=False |
| JSON Out of range float（Omega=inf）| 1 | schemas.sanitize_for_json 把 inf/NaN 转 None |
| test helper RBA UNIQUE 冲突（多基金同月份）| 1 | _seed_fund_with_data 写 RBA 前检查已存在 |

## Notes
- 详细任务步骤见 `docs/superpowers/plans/2026-07-11-fastapi-api-layer.md`
- 阶段 1 接口契约见 `findings.md`
- TDD：写失败测试 -> 跑(失败) -> 实现 -> 跑(通过) -> 提交
- 分支：feature/api-layer（从 main）
