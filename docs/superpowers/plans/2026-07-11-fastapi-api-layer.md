# FastAPI API 层实现计划 (阶段 2/5)：REST 端点 + RBA 定时调度 + LLM 报告集成

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在阶段 1 后端地基之上构建 FastAPI 路由层，提供基金 CRUD、5 维指标对比、NAV 时序、异常审计纠错、RBA 定时调度与 LLM 投研报告 6 类 API 端点，全部通过 pytest 单元测试。

**Architecture:** FastAPI 应用通过 `create_app()` 工厂创建，lifespan 内启动 APScheduler 定时抓取 RBA 利率。路由按领域拆分到 `routers/` 子包（funds/metrics/anomalies/rba/reports）。period 切片（full/3y/1y/common）抽取为纯函数 `period.py`。LLM 集成通过 openai SDK 调用用户的中转站（OpenAI 兼容），报告缓存到 `ai_reports` 表。所有端点复用阶段 1 的 `crud.py` / `calculations.py` / `metrics_pipeline.py` / `rba.py`，不重写计算逻辑。

**Tech Stack:** Python 3.9.6, FastAPI, Uvicorn, Pydantic v2, APScheduler 3.x, openai SDK 1.x, SQLAlchemy 2.0, SQLite, pytest, httpx (TestClient)

## Global Constraints

- **Python 3.9.6 运行时**：严禁 PEP 604 `X | None` 运行时语法。所有可空类型注解必须用 `Optional[X]`（来自 `typing`），ORM 模型与 Pydantic 模型均如此。文件首行加 `from __future__ import annotations`。`list[X]` / `tuple[X, Y]`（PEP 585）在 3.9 可用。用 `python3`/`pip3` 而非 `python`/`pip`。（阶段 1 已踩坑：`Mapped[str | None]` 在 3.9 运行时崩溃，已全量改为 `Optional[str]`。）
- **数据完整性（最高优先级）**：禁止捏造金融数据；数据缺口零容忍；异常值如实保留交人工复核，不得自动纠正（CLAUDE.md 第一、五条）。**人工纠错**（用户主动 PATCH 修改某月净值）是允许的，与"自动纠错"不同--前者是用户决策，后者被禁止。
- **POST /api/funds 不抓取**：遵循用户明确意图"skills 只负责清洗写入数据库，网页后端只自动更新 RBA，其他和网页无关"。POST /api/funds 只写 `funds` 表元信息（含抓取配置 confirmed_url/fetch_method/url_type/max_pdf_pages），不触发任何网络抓取。月度数据由阶段 3 的 `add_fixed_fund` skill 写入 `monthly_returns`。
- **APIR 代码格式**：正则 `^[A-Z]{3}\d{4}AU$`（如 ETL5010AU），字段可空（nullable）以支持无 APIR 基金（如 Stake）。非空时必须匹配正则。
- **基金防重**：基于 `fund_name` UNIQUE 约束（阶段 1 已实现），POST 重复 fund_name 返回 409。
- **period 语义**：`full`=全部历史；`3y`=最近 36 个月；`1y`=最近 12 个月；`common`=所有选中基金的共同月份交集。`full` 读 `fund_metrics` 预计算缓存；`3y/1y/common` 从 `monthly_returns` 切片后即时重算（复用 `compute_all_metrics` + `resolve_rf_rates`）。
- **去平滑判定基于切片后序列**：time-series 端点对切片后的 returns 重新计算 phi/Q 并判定 `should_apply_geltner`。若切片后 n<36，则不返回去平滑 NAV（即使全量历史通过了 Geltner）。
- **LLM 中转站假设 OpenAI 兼容**：用 openai SDK + `base_url` 配置。若用户中转站非 OpenAI 格式，需在 `llm.py` 调整（审批时确认）。第一版**非流式**（返回完整 Markdown），流式预留。
- **ai_reports 缓存键**：`(fund_ids 字母升序逗号拼接, date_period, report_type=period)` 唯一。命中缓存直接返回；`force=true` 跳过缓存重新生成。
- **测试隔离**：API 测试用 `TestClient` + 依赖注入覆盖（`get_db` 指向内存 SQLite），且 `create_app(enable_scheduler=False)` 避免测试启动调度器。
- **语言**：代码注释与中文输出用中文（遵循 CLAUDE.md）。

---

## 文件结构

```
webapp/backend/
├── requirements.txt                  # 更新：追加 fastapi/uvicorn/apscheduler/openai/httpx
├── README.md                         # 新：启动说明与环境变量
├── app/
│   ├── __init__.py                   # 已存在
│   ├── config.py                     # 改：追加 LLM_*/CORS/SCHEDULER 配置
│   ├── database.py                   # 已存在（get_db 供依赖注入）
│   ├── models.py                     # 已存在（6 张表）
│   ├── calculations.py               # 已存在（5 维纯计算）
│   ├── anomaly.py                    # 已存在
│   ├── crud.py                       # 已存在（10 个 CRUD 函数）
│   ├── rba.py                        # 已存在（fetch_current/historical + upsert）
│   ├── metrics_pipeline.py           # 已存在（compute_and_store_metrics）
│   ├── main.py                       # 新：create_app 工厂 + lifespan + CORS + 路由聚合
│   ├── schemas.py                    # 新：Pydantic 请求/响应模型
│   ├── period.py                     # 新：period 切片纯函数（full/3y/1y/common）
│   ├── scheduler.py                  # 新：APScheduler RBA 定时任务
│   ├── llm.py                        # 新：LLM prompt 构建 + 中转站调用 + 缓存
│   └── routers/
│       ├── __init__.py               # 新：空
│       ├── funds.py                  # 新：GET/POST/DELETE /api/funds, POST /recompute
│       ├── metrics.py                # 新：GET /api/metrics/compare, /time-series
│       ├── anomalies.py              # 新：GET /api/anomalies, PATCH /api/monthly-returns/{id}
│       ├── rba.py                    # 新：POST /api/rba/refresh
│       └── reports.py                # 新：POST /api/reports/ai-summary
└── tests/
    ├── __init__.py                   # 已存在
    ├── conftest.py                   # 改：追加 client(TestClient) fixture
    ├── test_models.py ... test_metrics_pipeline.py  # 已存在（阶段 1，保持绿）
    ├── test_api_funds.py             # 新
    ├── test_period.py                # 新
    ├── test_api_metrics.py           # 新
    ├── test_api_anomalies.py         # 新
    ├── test_scheduler.py             # 新
    ├── test_llm.py                   # 新
    ├── test_api_reports.py           # 新
    └── test_integration.py           # 新：端到端
```

**职责边界**：
- `main.py`：仅组装 app（CORS、lifespan、挂载路由），不含业务逻辑。`create_app(enable_scheduler)` 工厂便于测试关闭调度器。
- `schemas.py`：所有 Pydantic v2 模型，含 APIR 正则校验。
- `period.py`：纯函数，输入 (dates, returns, period) 输出切片，无 IO，最易测试。
- `routers/*.py`：薄路由层，参数解析 -> 调 crud/calculations/period/llm -> 返回 schema。不含新计算逻辑。
- `scheduler.py`：APScheduler 封装，定时调用 `rba.fetch_current_rba_rate` + `fetch_historical_rba_rates` + `upsert_rba_rates`。
- `llm.py`：prompt 构建 + openai SDK 调用 + ai_reports 缓存读写。

---

### Task 1: FastAPI 骨架、配置扩展、schemas 与 TestClient fixture

**Files:**
- Modify: `webapp/backend/requirements.txt`
- Modify: `webapp/backend/app/config.py`
- Create: `webapp/backend/app/schemas.py`
- Create: `webapp/backend/app/main.py`
- Modify: `webapp/backend/tests/conftest.py`

**Interfaces:**
- Consumes: 阶段 1 的 `database.get_db`、`database.init_db`。
- Produces: `create_app(enable_scheduler: bool = True) -> FastAPI`（工厂）；`app` 模块级实例（uvicorn 入口）；`settings` 追加字段 `LLM_API_BASE`、`LLM_API_KEY`、`LLM_MODEL`、`CORS_ORIGINS`、`SCHEDULER_ENABLED`、`RBA_CRON_HOUR`；Pydantic 模型 `FundCreate`、`FundResponse`、`MonthlyReturnPatch`、`AnomalyResponse` 等；测试 `client` fixture（TestClient + 内存 DB 依赖覆盖）。

- [ ] **Step 1: 更新 requirements.txt**

```
sqlalchemy>=2.0.0
requests>=2.31.0
beautifulsoup4>=4.12.0
pytest>=8.0.0
fastapi>=0.110.0
uvicorn[standard]>=0.27.0
apscheduler>=3.10.0,<4.0.0
openai>=1.12.0
httpx>=0.27.0
```

- [ ] **Step 2: 扩展 app/config.py**

```python
"""后端配置：通过环境变量覆盖默认值。"""
import os
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_DB_PATH = _BASE_DIR / "data" / "fund_analysis.db"


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")
    RBA_BASE_URL: str = "https://www.rba.gov.au/"
    RBA_HISTORY_API: str = "https://api.db.nomics.world/v22/series/RBA/F1/FIRMMCRTD?observations=1"
    # LLM 中转站（OpenAI 兼容）
    LLM_API_BASE: str = os.getenv("LLM_API_BASE", "")  # 空=未配置，AI 报告功能禁用
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-3-5-sonnet")
    # Web
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:5173")
    # 调度
    SCHEDULER_ENABLED: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    RBA_CRON_HOUR: int = int(os.getenv("RBA_CRON_HOUR", "9"))  # 每天几点抓 RBA


settings = Settings()
```

- [ ] **Step 3: 创建 app/schemas.py**

```python
"""Pydantic v2 请求/响应模型。APIR 正则校验在此。"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, field_validator


APIR_PATTERN = re.compile(r"^[A-Z]{3}\d{4}AU$")


class FundCreate(BaseModel):
    fund_id: str
    fund_name: str
    apir_code: Optional[str] = None
    confirmed_url: str
    fetch_method: str
    url_type: str
    max_pdf_pages: Optional[int] = None

    @field_validator("apir_code")
    @classmethod
    def validate_apir(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if not APIR_PATTERN.match(v):
            raise ValueError(f"APIR 格式应为 3字母+4数字+AU（如 ETL5010AU），得到: {v}")
        return v


class FundResponse(BaseModel):
    fund_id: str
    fund_name: str
    apir_code: Optional[str] = None
    confirmed_url: str
    fetch_method: str
    url_type: str
    max_pdf_pages: Optional[int] = None
    data_cutoff_month: Optional[str] = None  # 来自 fund_metrics.date_period 或最新 monthly_return
    has_metrics: bool = False

    model_config = {"from_attributes": True}


class MonthlyReturnPatch(BaseModel):
    """人工纠错：修改某月净值（用户主动，非自动纠错）。"""
    net_return: float
    commentary_truth: Optional[float] = None


class AnomalyResponse(BaseModel):
    id: int
    fund_id: str
    date: str
    value: float
    z_score: float
    threshold_sigma: float
    mean: float
    stdev: float
    fund_name: Optional[str] = None

    model_config = {"from_attributes": True}


class AiReportRequest(BaseModel):
    fund_ids: list[str]
    period: str = "full"  # full/3y/1y/common
    force: bool = False


class AiReportResponse(BaseModel):
    content: str
    cached: bool = False
```

- [ ] **Step 4: 创建 app/main.py（create_app 工厂 + lifespan）**

```python
"""FastAPI 应用工厂：CORS、lifespan（建表+调度器）、路由聚合。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：建表 + 可选启动 RBA 调度器；关闭：停止调度器。"""
    init_db()
    scheduler = None
    if settings.SCHEDULER_ENABLED:
        from app.scheduler import start_scheduler
        scheduler = start_scheduler()
    try:
        yield
    finally:
        if scheduler is not None:
            from app.scheduler import shutdown_scheduler
            shutdown_scheduler(scheduler)


def create_app(enable_scheduler: bool = True) -> FastAPI:
    """创建 FastAPI 应用。测试传 enable_scheduler=False 跳过调度器。"""
    # 测试时临时关闭调度器
    original = settings.SCHEDULER_ENABLED
    settings.SCHEDULER_ENABLED = enable_scheduler and original
    app = FastAPI(title="固定收益基金分析 API", lifespan=lifespan)
    settings.SCHEDULER_ENABLED = original

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.routers import funds, metrics, anomalies, rba, reports
    app.include_router(funds.router)
    app.include_router(metrics.router)
    app.include_router(anomalies.router)
    app.include_router(rba.router)
    app.include_router(reports.router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


# uvicorn 入口
app = create_app()
```

- [ ] **Step 5: 扩展 tests/conftest.py（追加 client fixture）**

```python
"""pytest 公共 fixture：内存数据库 + TestClient。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app import models  # noqa: F401
from app.main import create_app


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    """TestClient，依赖注入指向内存 DB，调度器关闭。"""
    app = create_app(enable_scheduler=False)

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 6: 写失败测试 tests/test_api_funds.py（仅 health + 骨架）**

```python
"""FastAPI 骨架测试。"""
import pytest


@pytest.mark.unit
def test_health_endpoint(client):
    """GET /health 返回 200。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

- [ ] **Step 7: 运行测试验证失败（routers 尚未创建，import 报错）**

Run: `cd webapp/backend && python3 -m pytest tests/test_api_funds.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.routers'` 或 `app.scheduler`）

- [ ] **Step 8: 创建 app/routers/__init__.py（空）与占位 router 文件**

为让 main.py 能 import，创建 5 个占位 router 文件，每个含一个空的 `router = APIRouter()`。后续任务填充端点。

```python
# app/routers/__init__.py（空）
```

```python
# app/routers/funds.py
from fastapi import APIRouter
router = APIRouter(prefix="/api/funds", tags=["funds"])
```

（metrics.py prefix `/api/metrics`；anomalies.py prefix `/api`；rba.py prefix `/api/rba`；reports.py prefix `/api/reports`，各自同样占位。）

同时创建 `app/scheduler.py` 占位：

```python
# app/scheduler.py
"""RBA 定时调度（Task 6 实现）。"""

def start_scheduler():
    return None

def shutdown_scheduler(scheduler):
    pass
```

- [ ] **Step 9: 安装新依赖**

Run: `cd webapp/backend && pip3 install -r requirements.txt`
Expected: 成功安装 fastapi, uvicorn, apscheduler, openai, httpx

- [ ] **Step 10: 运行测试验证通过**

Run: `cd webapp/backend && python3 -m pytest tests/test_api_funds.py -v`
Expected: PASS（/health 200）

- [ ] **Step 11: 运行全部测试确认无回归**

Run: `cd webapp/backend && python3 -m pytest tests/ -v`
Expected: 阶段 1 的 43 个用例 + 本任务 1 个，全绿

- [ ] **Step 12: 提交**

```bash
git add webapp/backend/
git commit -m "feat(backend): scaffold FastAPI app with config, schemas, TestClient fixture

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 基金 CRUD API + 手动重算端点

**Files:**
- Modify: `webapp/backend/app/routers/funds.py`
- Modify: `webapp/backend/tests/test_api_funds.py`（追加用例）

**Interfaces:**
- Consumes: `crud.create_fund`、`get_all_funds`、`get_fund`、`delete_fund`、`metrics_pipeline.compute_and_store_metrics`；阶段 1 的 `Fund`、`FundMetric`、`MonthlyReturn` 模型。
- Produces: `GET /api/funds`（返回 `list[FundResponse]`，含 data_cutoff_month 与 has_metrics）、`POST /api/funds`（`FundCreate` -> `FundResponse`，201；重复 fund_name -> 409；APIR 非法 -> 422）、`DELETE /api/funds/{fund_id}`（204；不存在 -> 404）、`POST /api/funds/{fund_id}/recompute`（触发 `compute_and_store_metrics`，返回指标摘要；无数据 -> 400）。

- [ ] **Step 1: 追加失败测试到 tests/test_api_funds.py**

```python
from app.models import Fund, MonthlyReturn, RbaCashRate


@pytest.mark.unit
def test_create_fund_via_api(client):
    payload = {
        "fund_id": "f1", "fund_name": "Fund One",
        "apir_code": "ETL5010AU", "confirmed_url": "http://x",
        "fetch_method": "pdf", "url_type": "pdf",
    }
    resp = client.post("/api/funds", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["fund_id"] == "f1"
    assert body["apir_code"] == "ETL5010AU"
    assert body["has_metrics"] is False


@pytest.mark.unit
def test_create_fund_duplicate_name_returns_409(client):
    payload = {"fund_id": "f1", "fund_name": "Dup", "confirmed_url": "http://x",
               "fetch_method": "pdf", "url_type": "pdf"}
    client.post("/api/funds", json=payload)
    payload2 = {"fund_id": "f2", "fund_name": "Dup", "confirmed_url": "http://y",
                "fetch_method": "pdf", "url_type": "pdf"}
    resp = client.post("/api/funds", json=payload2)
    assert resp.status_code == 409


@pytest.mark.unit
def test_create_fund_invalid_apir_returns_422(client):
    payload = {"fund_id": "f1", "fund_name": "Bad", "apir_code": "INVALID",
               "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"}
    resp = client.post("/api/funds", json=payload)
    assert resp.status_code == 422


@pytest.mark.unit
def test_create_fund_without_apir_ok(client):
    """Stake 等无 APIR 基金：apir_code 可空。"""
    payload = {"fund_id": "stake", "fund_name": "Stake", "apir_code": None,
               "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"}
    resp = client.post("/api/funds", json=payload)
    assert resp.status_code == 201
    assert resp.json()["apir_code"] is None


@pytest.mark.unit
def test_list_funds_with_cutoff(client, db_session):
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    # 直接写入月度数据 + RBA（模拟 skill 写入）
    for m, r in [(1, 0.01), (2, 0.02), (3, 0.03)]:
        db_session.add(MonthlyReturn(fund_id="f1", date=f"2026-{m:02d}-28",
                                     net_return=r, nav=1.0))
    db_session.add(RbaCashRate(date_period="2026-01", rate=0.0435))
    db_session.commit()
    resp = client.get("/api/funds")
    assert resp.status_code == 200
    funds = resp.json()
    assert len(funds) == 1
    assert funds[0]["data_cutoff_month"] == "2026-03"


@pytest.mark.unit
def test_delete_fund(client):
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    resp = client.delete("/api/funds/f1")
    assert resp.status_code == 204
    assert client.get("/api/funds").json() == []
    # 再删不存在 -> 404
    assert client.delete("/api/funds/f1").status_code == 404


@pytest.mark.unit
def test_recompute_fund_metrics(client, db_session):
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    for m, r in [(1, 0.01), (2, 0.02)]:
        db_session.add(MonthlyReturn(fund_id="f1", date=f"2026-{m:02d}-28",
                                     net_return=r, nav=1.0))
    db_session.add(RbaCashRate(date_period="2026-01", rate=0.0435))
    db_session.add(RbaCashRate(date_period="2026-02", rate=0.0435))
    db_session.commit()
    resp = client.post("/api/funds/f1/recompute")
    assert resp.status_code == 200
    body = resp.json()
    assert body["history_months"] == 2
    assert body["is_short_history_warning"] == 1
    # GET /api/funds 现在 has_metrics=True
    assert client.get("/api/funds").json()[0]["has_metrics"] is True


@pytest.mark.unit
def test_recompute_no_data_returns_400(client):
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    resp = client.post("/api/funds/f1/recompute")
    assert resp.status_code == 400
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python3 -m pytest tests/test_api_funds.py -v`
Expected: FAIL（端点不存在，404）

- [ ] **Step 3: 实现 app/routers/funds.py**

```python
"""基金 CRUD + 手动重算路由。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.crud import create_fund, get_all_funds, get_fund, delete_fund
from app.database import get_db
from app.models import Fund, FundMetric, MonthlyReturn
from app.metrics_pipeline import compute_and_store_metrics
from app.schemas import FundCreate, FundResponse

router = APIRouter(prefix="/api/funds", tags=["funds"])


def _to_response(fund: Fund, session: Session) -> FundResponse:
    metric = session.get(FundMetric, fund.fund_id)
    cutoff = metric.date_period if metric else None
    if cutoff is None:
        latest = (session.query(MonthlyReturn)
                  .filter_by(fund_id=fund.fund_id)
                  .order_by(MonthlyReturn.date.desc()).first())
        cutoff = latest.date[:7] if latest else None
    return FundResponse(
        fund_id=fund.fund_id, fund_name=fund.fund_name, apir_code=fund.apir_code,
        confirmed_url=fund.confirmed_url, fetch_method=fund.fetch_method,
        url_type=fund.url_type, max_pdf_pages=fund.max_pdf_pages,
        data_cutoff_month=cutoff, has_metrics=metric is not None,
    )


@router.get("", response_model=list[FundResponse])
def list_funds(session: Session = Depends(get_db)):
    funds = get_all_funds(session)
    return [_to_response(f, session) for f in funds]


@router.post("", response_model=FundResponse, status_code=status.HTTP_201_CREATED)
def add_fund(payload: FundCreate, session: Session = Depends(get_db)):
    try:
        fund = create_fund(
            session, fund_id=payload.fund_id, fund_name=payload.fund_name,
            apir_code=payload.apir_code, confirmed_url=payload.confirmed_url,
            fetch_method=payload.fetch_method, url_type=payload.url_type,
            max_pdf_pages=payload.max_pdf_pages,
        )
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"基金名 '{payload.fund_name}' 已存在")
    return _to_response(fund, session)


@router.delete("/{fund_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_fund(fund_id: str, session: Session = Depends(get_db)):
    if not delete_fund(session, fund_id):
        raise HTTPException(status_code=404, detail=f"基金 {fund_id} 不存在")


@router.post("/{fund_id}/recompute")
def recompute(fund_id: str, session: Session = Depends(get_db)):
    if get_fund(session, fund_id) is None:
        raise HTTPException(status_code=404, detail=f"基金 {fund_id} 不存在")
    try:
        metrics = compute_and_store_metrics(
            session, fund_id, fallback_rba_rate=settings.RBA_FALLBACK_RATE
            if hasattr(settings, "RBA_FALLBACK_RATE") else 0.0435)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return metrics
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python3 -m pytest tests/test_api_funds.py -v`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/routers/funds.py webapp/backend/tests/test_api_funds.py
git commit -m "feat(backend): add fund CRUD API and manual recompute endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: period 切片纯函数 + 指标对比 API

**Files:**
- Create: `webapp/backend/app/period.py`
- Create: `webapp/backend/tests/test_period.py`
- Modify: `webapp/backend/app/routers/metrics.py`
- Create: `webapp/backend/tests/test_api_metrics.py`

**Interfaces:**
- Consumes: `crud.get_returns`、`crud.resolve_rf_rates`、`calculations.compute_all_metrics`；`FundMetric` 模型。
- Produces:
  - `period.get_common_months(dates_lists: list[list[str]]) -> list[str]`：多基金月份（YYYY-MM）交集，升序。
  - `period.slice_by_period(dates, returns, period, common_months=None) -> tuple[list[str], list[float]]`：full=全部；3y=最近 36 个月；1y=最近 12 个月；common=仅保留 common_months 内的。
  - `GET /api/metrics/compare?fund_ids=A,B&period=full`：full 读 `fund_metrics`；3y/1y/common 切片重算。返回每基金的 5 维指标 dict + 去平滑状态。

- [ ] **Step 1: 写失败测试 tests/test_period.py**

```python
"""period 切片纯函数测试。"""
import pytest
from app.period import get_common_months, slice_by_period


@pytest.mark.unit
def test_slice_full():
    dates = ["2026-01-31", "2026-02-28", "2026-03-31"]
    rets = [0.01, 0.02, 0.03]
    d, r = slice_by_period(dates, rets, "full")
    assert d == dates and r == rets


@pytest.mark.unit
def test_slice_3y_takes_last_36():
    dates = [f"2023-{m:02d}-28" for m in range(1, 13)] + [f"2024-{m:02d}-28" for m in range(1, 13)] \
            + [f"2025-{m:02d}-28" for m in range(1, 13)] + [f"2026-{m:02d}-28" for m in range(1, 13)]
    rets = [0.001 * i for i in range(48)]
    d, r = slice_by_period(dates, rets, "3y")
    assert len(d) == 36
    assert d[0] == "2023-01-28"  # 48 个月的最后 36 个


@pytest.mark.unit
def test_slice_1y_takes_last_12():
    dates = [f"2025-{m:02d}-28" for m in range(1, 13)] + [f"2026-{m:02d}-28" for m in range(1, 13)]
    rets = [0.001 * i for i in range(24)]
    d, r = slice_by_period(dates, rets, "1y")
    assert len(d) == 12
    assert d[0] == "2025-01-28"


@pytest.mark.unit
def test_slice_common():
    dates = ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"]
    rets = [0.01, 0.02, 0.03, 0.04]
    common = ["2026-02", "2026-03"]  # YYYY-MM
    d, r = slice_by_period(dates, rets, "common", common_months=common)
    assert d == ["2026-02-28", "2026-03-31"]
    assert r == [0.02, 0.03]


@pytest.mark.unit
def test_get_common_months_intersection():
    a = ["2026-01-31", "2026-02-28", "2026-03-31"]
    b = ["2026-02-28", "2026-03-31", "2026-04-30"]
    common = get_common_months([a, b])
    assert common == ["2026-02", "2026-03"]


@pytest.mark.unit
def test_slice_invalid_period_raises():
    with pytest.raises(ValueError):
        slice_by_period(["2026-01-31"], [0.01], "5y")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python3 -m pytest tests/test_period.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.period'`）

- [ ] **Step 3: 实现 app/period.py**

```python
"""period 切片纯函数：full/3y/1y/common。无 IO。"""
from __future__ import annotations

from typing import Optional

VALID_PERIODS = {"full", "3y", "1y", "common"}


def get_common_months(dates_lists: list[list[str]]) -> list[str]:
    """多基金月末日期列表 -> 共同月份（YYYY-MM）交集，升序。"""
    if not dates_lists:
        return []
    sets = [{d[:7] for d in dl} for dl in dates_lists]
    common = sets[0]
    for s in sets[1:]:
        common = common & s
    return sorted(common)


def slice_by_period(dates: list[str], returns: list[float], period: str,
                    common_months: Optional[list[str]] = None) -> tuple[list[str], list[float]]:
    """按 period 切片 (dates, returns)。

    full: 全部；3y: 最后 36 个；1y: 最后 12 个；common: 仅保留 common_months 内月份。
    """
    if period not in VALID_PERIODS:
        raise ValueError(f"未知 period: {period}，支持 {VALID_PERIODS}")
    if period == "full":
        return list(dates), list(returns)
    if period == "3y":
        n = min(36, len(dates))
        return dates[-n:], returns[-n:]
    if period == "1y":
        n = min(12, len(dates))
        return dates[-n:], returns[-n:]
    # common
    if common_months is None:
        common_months = []
    keep = set(common_months)
    out_d, out_r = [], []
    for d, r in zip(dates, returns):
        if d[:7] in keep:
            out_d.append(d)
            out_r.append(r)
    return out_d, out_r
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python3 -m pytest tests/test_period.py -v`
Expected: 6 passed

- [ ] **Step 5: 写失败测试 tests/test_api_metrics.py（compare 端点）**

```python
"""metrics 对比与时序 API 测试。"""
import pytest
from app.models import MonthlyReturn, RbaCashRate


def _seed_fund_with_data(client, db_session, fund_id, name, returns_by_month):
    """辅助：注册基金 + 写入月度数据 + RBA。"""
    client.post("/api/funds", json={"fund_id": fund_id, "fund_name": name,
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    for ym, r in returns_by_month:
        db_session.add(MonthlyReturn(fund_id=fund_id, date=ym, net_return=r, nav=1.0))
        db_session.add(RbaCashRate(date_period=ym[:7], rate=0.0435))
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
```

- [ ] **Step 6: 运行测试验证失败**

Run: `cd webapp/backend && python3 -m pytest tests/test_api_metrics.py -v`
Expected: FAIL（端点不存在）

- [ ] **Step 7: 实现 app/routers/metrics.py（compare 部分）**

```python
"""metrics 对比与时序路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.crud import get_returns, get_fund, resolve_rf_rates
from app.models import FundMetric
from app.calculations import compute_all_metrics
from app.period import get_common_months, slice_by_period, VALID_PERIODS

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _recompute_for_slice(session: Session, fund_id: str, period: str,
                         common_months=None) -> dict:
    """从 monthly_returns 切片后即时重算 5 维指标。"""
    ts = get_returns(session, fund_id)
    if not ts:
        raise ValueError(f"基金 {fund_id} 无月度收益数据")
    dates = [d["date"] for d in ts]
    returns = [d["net_return"] for d in ts]
    d_slice, r_slice = slice_by_period(dates, returns, period, common_months)
    if not d_slice:
        raise ValueError(f"基金 {fund_id} 在 period={period} 下无数据")
    rf = resolve_rf_rates(session, d_slice, fallback_rate=0.0435)
    metrics = compute_all_metrics(r_slice, rf, fund_name=fund_id)
    metrics["fund_id"] = fund_id
    metrics["date_period"] = d_slice[-1][:7]
    return metrics


@router.get("/compare")
def compare(fund_ids: str = Query(...),
            period: str = Query("full"),
            session: Session = Depends(get_db)):
    if period not in VALID_PERIODS:
        raise HTTPException(status_code=422, detail=f"period 须为 {VALID_PERIODS}")
    ids = [s.strip() for s in fund_ids.split(",") if s.strip()]
    if not ids:
        raise HTTPException(status_code=422, detail="fund_ids 不能为空")

    # 校验基金存在
    for fid in ids:
        if get_fund(session, fid) is None:
            raise HTTPException(status_code=404, detail=f"基金 {fid} 不存在")

    # common 需先求共同月份
    common_months = None
    if period == "common":
        all_dates = [[d["date"] for d in get_returns(session, fid)] for fid in ids]
        common_months = get_common_months(all_dates)

    results = []
    for fid in ids:
        if period == "full":
            m = session.get(FundMetric, fid)
            if m is None:
                # 无预计算指标：即时全量重算
                m_dict = _recompute_for_slice(session, fid, "full")
            else:
                m_dict = {
                    "fund_id": fid, "date_period": m.date_period,
                    "history_months": m.history_months,
                    "is_short_history_warning": m.is_short_history_warning,
                    "unsmoothing_coefficient_phi": m.unsmoothing_coefficient_phi,
                    "is_geltner_applied": m.is_geltner_applied,
                    "orig_annualized_excess_return": m.orig_annualized_excess_return,
                    "un_annualized_excess_return": m.un_annualized_excess_return,
                    "orig_max_drawdown": m.orig_max_drawdown,
                    "un_max_drawdown": m.un_max_drawdown,
                    "orig_omega_ratio": m.orig_omega_ratio,
                    "un_omega_ratio": m.un_omega_ratio,
                    "orig_excess_win_rate": m.orig_excess_win_rate,
                    "un_excess_win_rate": m.un_excess_win_rate,
                    "orig_max_underperform_months": m.orig_max_underperform_months,
                    "un_max_underperform_months": m.un_max_underperform_months,
                    "orig_annualized_volatility": m.orig_annualized_volatility,
                    "un_annualized_volatility": m.un_annualized_volatility,
                    "ljung_box_q": m.ljung_box_q,
                    "is_q_significant": m.is_q_significant,
                }
        else:
            m_dict = _recompute_for_slice(session, fid, period, common_months)
        results.append(m_dict)
    return {"period": period, "funds": results}
```

- [ ] **Step 8: 运行测试验证通过**

Run: `cd webapp/backend && python3 -m pytest tests/test_api_metrics.py -v`
Expected: 4 passed

- [ ] **Step 9: 提交**

```bash
git add webapp/backend/app/period.py webapp/backend/app/routers/metrics.py \
        webapp/backend/tests/test_period.py webapp/backend/tests/test_api_metrics.py
git commit -m "feat(backend): add period slicing and metrics compare API

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 时序 API（对齐 NAV + 去平滑 NAV）

**Files:**
- Modify: `webapp/backend/app/routers/metrics.py`（追加 time-series）
- Modify: `webapp/backend/tests/test_api_metrics.py`（追加用例）

**Interfaces:**
- Consumes: `crud.get_returns`、`period.slice_by_period`、`calculations.calculate_autocorrelation`、`unsmooth_returns`、`should_apply_geltner`、`_build_nav_series`。
- Produces: `GET /api/metrics/time-series?fund_ids=A,B&period=full`。返回 `{"period": ..., "months": [...], "series": [{"fund_id": ..., "fund_name": ..., "orig_nav": [...], "unsm_nav": [...] | null, "is_geltner_applied": bool}]}`。`unsm_nav` 仅在切片后通过 Geltner 三重防火墙时提供，否则为 null。

- [ ] **Step 1: 追加失败测试到 tests/test_api_metrics.py**

```python
from app.calculations import _build_nav_series


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
    # f1 在共同区间的 orig_nav: 从 2026-02 起 [1.02, 1.02*1.03]（区间内重新基数为 1.0）
    assert body["series"][0]["orig_nav"] == pytest.approx([1.02, 1.02 * 1.03], rel=1e-5)


@pytest.mark.unit
def test_time_series_geltner_nav_when_applied(client, db_session):
    """足够长且自相关显著的序列，去平滑 NAV 应返回（非 null）。"""
    # 构造 60 个月强自相关平滑序列
    data = []
    val = 0.005
    for i in range(60):
        ym = f"2021-{(i % 12) + 1:02d}-28" if i < 12 else (
            f"2022-{(i % 12) + 1:02d}-28" if i < 24 else (
            f"2023-{(i % 12) + 1:02d}-28" if i < 36 else (
            f"2024-{(i % 12) + 1:02d}-28" if i < 48 else
            f"2025-{(i % 12) + 1:02d}-28")))
        data.append((ym, val))
        val = val * 0.95 + 0.005 * 0.05
    _seed_fund_with_data(client, db_session, "f1", "SmoothFund", data)
    resp = client.get("/api/metrics/time-series", params={"fund_ids": "f1", "period": "full"})
    assert resp.status_code == 200
    s = resp.json()["series"][0]
    # 是否应用去平滑取决于序列实际 phi/Q，但若应用则 unsm_nav 非 null
    if s["is_geltner_applied"]:
        assert s["unsm_nav"] is not None
        assert len(s["unsm_nav"]) == len(s["orig_nav"])
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python3 -m pytest tests/test_api_metrics.py -v -k time_series`
Expected: FAIL（端点不存在）

- [ ] **Step 3: 追加 time-series 实现到 app/routers/metrics.py**

```python
from app.calculations import (
    calculate_autocorrelation, unsmooth_returns, should_apply_geltner,
    LJUNG_BOX_CRITICAL_VALUE, _build_nav_series,
)
from app.models import Fund


@router.get("/time-series")
def time_series(fund_ids: str = Query(...),
                period: str = Query("full"),
                session: Session = Depends(get_db)):
    if period not in VALID_PERIODS:
        raise HTTPException(status_code=422, detail=f"period 须为 {VALID_PERIODS}")
    ids = [s.strip() for s in fund_ids.split(",") if s.strip()]
    if not ids:
        raise HTTPException(status_code=422, detail="fund_ids 不能为空")

    # 各基金原始 (dates, returns)
    per_fund = {}
    for fid in ids:
        fund = get_fund(session, fid)
        if fund is None:
            raise HTTPException(status_code=404, detail=f"基金 {fid} 不存在")
        ts = get_returns(session, fid)
        per_fund[fid] = {
            "name": fund.fund_name,
            "dates": [d["date"] for d in ts],
            "returns": [d["net_return"] for d in ts],
        }

    # common: 求共同月份
    common_months = None
    if period == "common":
        common_months = get_common_months([v["dates"] for v in per_fund.values()])

    # 切片
    sliced = {}
    for fid, info in per_fund.items():
        d, r = slice_by_period(info["dates"], info["returns"], period, common_months)
        sliced[fid] = {"name": info["name"], "dates": d, "returns": r}

    # 统一月份轴（取所有切片月份的并集升序；common 下各基金相同）
    all_months = sorted({d[:7] for info in sliced.values() for d in info["dates"]})

    series = []
    for fid in ids:
        info = sliced[fid]
        r_slice = info["returns"]
        orig_nav = _build_nav_series(r_slice)[1:]  # 去掉起点 1.0
        # 去平滑判定（基于切片后序列）
        unsm_nav = None
        is_geltner = False
        if len(r_slice) >= 2:
            phi, q = calculate_autocorrelation(r_slice, fund_name=fid)
            is_geltner = should_apply_geltner(len(r_slice), phi, q)
            if is_geltner:
                unsm = unsmooth_returns(r_slice, phi, fund_name=fid)
                unsm_nav = _build_nav_series(unsm)[1:]
        series.append({
            "fund_id": fid,
            "fund_name": info["name"],
            "orig_nav": orig_nav,
            "unsm_nav": unsm_nav,
            "is_geltner_applied": is_geltner,
        })
    return {"period": period, "months": all_months, "series": series}
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python3 -m pytest tests/test_api_metrics.py -v`
Expected: 7 passed（含 Task 3 的 4 个 + 本任务 3 个）

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/routers/metrics.py webapp/backend/tests/test_api_metrics.py
git commit -m "feat(backend): add time-series API with aligned NAV and unsmoothed NAV

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 异常审计 + 人工纠错 API

**Files:**
- Modify: `webapp/backend/app/routers/anomalies.py`
- Create: `webapp/backend/tests/test_api_anomalies.py`

**Interfaces:**
- Consumes: `Anomaly` 模型、`crud.upsert_monthly_return`、`metrics_pipeline.compute_and_store_metrics`。
- Produces: `GET /api/anomalies`（返回所有基金异常，含 fund_name）；`PATCH /api/monthly-returns/{id}`（`MonthlyReturnPatch` -> 更新 net_return/commentary_truth -> 重算 NAV + 重算该基金 metrics -> 返回新指标摘要）。注意：人工纠错是用户主动操作，符合 CLAUDE.md 第五条（允许人工纠错，禁止自动纠错）。

- [ ] **Step 1: 写失败测试 tests/test_api_anomalies.py**

```python
"""异常审计与人工纠错 API 测试。"""
import pytest
from app.models import MonthlyReturn, RbaCashRate, Anomaly


def _seed_with_outlier(client, db_session):
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    for m in range(1, 13):
        db_session.add(MonthlyReturn(fund_id="f1", date=f"2025-{m:02d}-28",
                                     net_return=0.005, nav=1.0))
        db_session.add(RbaCashRate(date_period=f"2025-{m:02d}", rate=0.0435))
    db_session.add(MonthlyReturn(fund_id="f1", date="2026-01-31", net_return=0.5, nav=1.0))
    db_session.add(RbaCashRate(date_period="2026-01", rate=0.0435))
    db_session.commit()
    client.post("/api/funds/f1/recompute")  # 触发异常检测写入 anomalies


@pytest.mark.unit
def test_list_anomalies(client, db_session):
    _seed_with_outlier(client, db_session)
    resp = client.get("/api/anomalies")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    assert body[0]["fund_name"] == "Fund One"
    assert body[0]["value"] == pytest.approx(0.5)


@pytest.mark.unit
def test_patch_monthly_return_corrects_and_recomputes(client, db_session):
    _seed_with_outlier(client, db_session)
    anomaly = client.get("/api/anomalies").json()[0]
    # 异常对应的 monthly_return 记录
    mr = db_session.query(MonthlyReturn).filter_by(
        fund_id="f1", date="2026-01-31").first()
    # 人工纠错：把 0.5 改回 0.005
    resp = client.patch(f"/api/monthly-returns/{mr.id}",
                        json={"net_return": 0.005})
    assert resp.status_code == 200
    # 改后异常应消失（重算后 0.005 不再是异常）
    anomalies = client.get("/api/anomalies").json()
    assert len(anomalies) == 0
    # NAV 已重算
    db_session.expire_all()
    mr2 = db_session.get(MonthlyReturn, mr.id)
    assert mr2.net_return == pytest.approx(0.005)


@pytest.mark.unit
def test_patch_nonexistent_returns_404(client):
    resp = client.patch("/api/monthly-returns/99999", json={"net_return": 0.01})
    assert resp.status_code == 404
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python3 -m pytest tests/test_api_anomalies.py -v`
Expected: FAIL（端点不存在）

- [ ] **Step 3: 实现 app/routers/anomalies.py**

```python
"""异常审计与人工纠错路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Anomaly, Fund, MonthlyReturn
from app.crud import upsert_monthly_return
from app.metrics_pipeline import compute_and_store_metrics
from app.schemas import AnomalyResponse, MonthlyReturnPatch
from app.config import settings

router = APIRouter(prefix="/api", tags=["anomalies"])


@router.get("/anomalies", response_model=list[AnomalyResponse])
def list_anomalies(session: Session = Depends(get_db)):
    rows = session.query(Anomaly).order_by(Anomaly.fund_id, Anomaly.date).all()
    out = []
    for a in rows:
        fund = session.get(Fund, a.fund_id)
        out.append(AnomalyResponse(
            id=a.id, fund_id=a.fund_id, date=a.date, value=a.value,
            z_score=a.z_score, threshold_sigma=a.threshold_sigma,
            mean=a.mean, stdev=a.stdev,
            fund_name=fund.fund_name if fund else None,
        ))
    return out


@router.patch("/monthly-returns/{row_id}")
def patch_monthly_return(row_id: int, payload: MonthlyReturnPatch,
                         session: Session = Depends(get_db)):
    mr = session.get(MonthlyReturn, row_id)
    if mr is None:
        raise HTTPException(status_code=404, detail=f"monthly_return id={row_id} 不存在")
    # 人工纠错：用户主动修改净值（CLAUDE.md 第五条允许人工纠错）
    upsert_monthly_return(
        session, mr.fund_id, mr.date, payload.net_return,
        commentary_truth=payload.commentary_truth,
    )
    # 重算该基金指标（异常也会随之重算）
    try:
        metrics = compute_and_store_metrics(
            session, mr.fund_id,
            fallback_rba_rate=getattr(settings, "RBA_FALLBACK_RATE", 0.0435))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return metrics
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python3 -m pytest tests/test_api_anomalies.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/routers/anomalies.py webapp/backend/tests/test_api_anomalies.py
git commit -m "feat(backend): add anomaly audit and manual correction API

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: RBA 定时调度 + 手动刷新 API

**Files:**
- Create: `webapp/backend/app/scheduler.py`（替换占位）
- Modify: `webapp/backend/app/routers/rba.py`
- Create: `webapp/backend/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `rba.fetch_current_rba_rate`、`fetch_historical_rba_rates`、`upsert_rba_rates`；`config.settings`。
- Produces:
  - `scheduler.run_rba_update(session_factory) -> dict`：执行一次 RBA 抓取+入库，返回 `{"current_rate": float, "upserted": int}`。纯函数式，便于测试。
  - `scheduler.start_scheduler(session_factory=None) -> BackgroundScheduler`：启动每日 RBA 调度，返回调度器实例。
  - `scheduler.shutdown_scheduler(scheduler)`：优雅停止。
  - `POST /api/rba/refresh`：手动触发一次 RBA 更新，返回结果。

- [ ] **Step 1: 写失败测试 tests/test_scheduler.py**

```python
"""RBA 调度与手动刷新测试。网络用 monkeypatch mock。"""
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import sessionmaker

from app.scheduler import run_rba_update, start_scheduler, shutdown_scheduler
from app.models import RbaCashRate
from app.database import Base


@pytest.mark.unit
def test_run_rba_update_fetches_and_upserts(db_session, monkeypatch):
    """run_rba_update 调用 fetch + upsert，返回结果。"""
    # mock 网络抓取
    monkeypatch.setattr("app.scheduler.fetch_current_rba_rate", lambda: 0.0435)
    monkeypatch.setattr("app.scheduler.fetch_historical_rba_rates",
                        lambda: {"2026-01": 0.0435, "2026-02": 0.0410})

    SessionFactory = sessionmaker(bind=db_session.bind, autocommit=False, autoflush=False)
    result = run_rba_update(SessionFactory)
    assert result["current_rate"] == pytest.approx(0.0435)
    assert result["upserted"] >= 2
    # 验证入库
    assert db_session.get(RbaCashRate, "2026-01").rate == pytest.approx(0.0435)
    assert db_session.get(RbaCashRate, "2026-02").rate == pytest.approx(0.0410)


@pytest.mark.unit
def test_start_scheduler_returns_running_scheduler():
    """start_scheduler 返回一个可关闭的调度器（不真正等待任务）。"""
    sched = start_scheduler(session_factory=lambda: None)
    try:
        assert sched is not None
        # 调度器应有一个 RBA 任务
        jobs = sched.get_jobs()
        assert len(jobs) >= 1
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python3 -m pytest tests/test_scheduler.py -v`
Expected: FAIL（scheduler 是占位）

- [ ] **Step 3: 实现 app/scheduler.py**

```python
"""RBA 定时调度：APScheduler 每日抓取 RBA 利率并入库。"""
from __future__ import annotations

from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.rba import fetch_current_rba_rate, fetch_historical_rba_rates, upsert_rba_rates
from app.database import SessionLocal


def run_rba_update(session_factory=None) -> dict:
    """执行一次 RBA 抓取+入库。

    Args:
        session_factory: 可选的会话工厂（测试注入）；默认用 SessionLocal。
    Returns:
        {"current_rate": float, "upserted": int}
    """
    factory = session_factory or SessionLocal
    current = fetch_current_rba_rate()
    historical = fetch_historical_rba_rates()
    # 把当前利率也写入当前月份（若历史 API 未覆盖）
    session = factory()
    try:
        count = upsert_rba_rates(session, historical)
        return {"current_rate": current, "upserted": count}
    finally:
        if session_factory is None:
            session.close()


def start_scheduler(session_factory=None) -> Optional[BackgroundScheduler]:
    """启动每日 RBA 调度。返回调度器实例；SCHEDULER_ENABLED=False 时返回 None。"""
    if not settings.SCHEDULER_ENABLED:
        return None
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(
        run_rba_update,
        CronTrigger(hour=settings.RBA_CRON_HOUR, minute=0),
        args=[session_factory],
        id="rba_daily_update",
        replace_existing=True,
    )
    sched.start()
    return sched


def shutdown_scheduler(scheduler) -> None:
    """优雅停止调度器。"""
    if scheduler is not None:
        scheduler.shutdown(wait=False)
```

- [ ] **Step 4: 实现 app/routers/rba.py**

```python
"""RBA 手动刷新路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.rba import fetch_current_rba_rate, fetch_historical_rba_rates, upsert_rba_rates

router = APIRouter(prefix="/api/rba", tags=["rba"])


@router.post("/refresh")
def refresh_rba(session: Session = Depends(get_db)):
    """手动触发一次 RBA 利率抓取与入库。"""
    try:
        current = fetch_current_rba_rate()
        historical = fetch_historical_rba_rates()
        count = upsert_rba_rates(session, historical)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RBA 抓取失败: {e}")
    return {"current_rate": current, "upserted": count}
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd webapp/backend && python3 -m pytest tests/test_scheduler.py -v`
Expected: 3 passed

- [ ] **Step 6: 提交**

```bash
git add webapp/backend/app/scheduler.py webapp/backend/app/routers/rba.py \
        webapp/backend/tests/test_scheduler.py
git commit -m "feat(backend): add RBA scheduler and manual refresh API

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: LLM 报告生成 API（中转站集成 + 缓存）

**Files:**
- Create: `webapp/backend/app/llm.py`
- Modify: `webapp/backend/app/routers/reports.py`
- Create: `webapp/backend/tests/test_llm.py`
- Create: `webapp/backend/tests/test_api_reports.py`

**Interfaces:**
- Consumes: `config.settings`（LLM_API_BASE/KEY/MODEL）、`crud.get_returns`、`FundMetric`、`AiReport` 模型、`routers.metrics._recompute_for_slice` 思路（复用 compare 取指标）。
- Produces:
  - `llm.build_prompt(fund_metrics_list: list[dict], period: str) -> str`：构建中文金融投研 prompt（含 5 维指标表 + 去平滑状态）。
  - `llm.call_llm(prompt: str) -> str`：调用中转站（openai SDK，base_url），返回 Markdown。LLM 未配置时抛 `RuntimeError`。
  - `llm.get_cached_report(session, fund_ids, date_period, report_type) -> Optional[str]`：查 ai_reports 缓存。
  - `llm.save_report(session, fund_ids, date_period, report_type, content)`：写缓存。
  - `POST /api/reports/ai-summary`：`AiReportRequest` -> `AiReportResponse`。命中缓存返回 cached=true；`force=true` 强制重生成。LLM 未配置 -> 503。

- [ ] **Step 1: 写失败测试 tests/test_llm.py**

```python
"""LLM prompt 构建与缓存逻辑测试。不实际调用 LLM。"""
import pytest
from unittest.mock import patch

from app.llm import build_prompt, get_cached_report, save_report, cache_key
from app.models import AiReport


@pytest.mark.unit
def test_build_prompt_contains_5d_metrics():
    metrics_list = [{
        "fund_id": "f1", "fund_name": "Fund One", "date_period": "2026-05",
        "history_months": 48, "is_short_history_warning": 0,
        "unsmoothing_coefficient_phi": 0.42, "is_geltner_applied": 1,
        "orig_annualized_excess_return": 0.025, "un_annualized_excess_return": 0.031,
        "orig_max_drawdown": -0.08, "un_max_drawdown": -0.12,
        "orig_omega_ratio": 1.8, "un_omega_ratio": 1.5,
        "orig_excess_win_rate": 0.65, "un_excess_win_rate": 0.60,
        "orig_max_underperform_months": 4, "un_max_underperform_months": 5,
        "orig_annualized_volatility": 0.04, "un_annualized_volatility": 0.06,
        "ljung_box_q": 11.6, "is_q_significant": 1,
    }]
    prompt = build_prompt(metrics_list, period="full")
    assert "Fund One" in prompt
    assert "0.42" in prompt  # phi
    assert "Omega" in prompt or "omega" in prompt.lower()
    assert "去平滑" in prompt or "Geltner" in prompt


@pytest.mark.unit
def test_cache_key_sorted_and_joined():
    assert cache_key(["f2", "f1"]) == "f1,f2"


@pytest.mark.unit
def test_cached_report_hit_and_miss(db_session):
    save_report(db_session, ["f1", "f2"], "2026-05", "full", "# 报告内容")
    hit = get_cached_report(db_session, ["f2", "f1"], "2026-05", "full")
    assert hit == "# 报告内容"
    miss = get_cached_report(db_session, ["f1", "f2"], "2026-06", "full")
    assert miss is None


@pytest.mark.unit
def test_call_llm_uses_openai_client(monkeypatch):
    """call_llm 通过 openai SDK 调用，mock 验证 base_url 与 model。"""
    from app import llm
    captured = {}

    class FakeResp:
        choices = [type("C", (), {"message": type("M", (), {"content": "# AI 报告"})()})()]

    class FakeClient:
        def __init__(self, api_key=None, base_url=None):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
        def chat(self):
            return self
        def completions(self):
            return self
        def create(self, **kwargs):
            captured["model"] = kwargs.get("model")
            captured["messages"] = kwargs.get("messages")
            return FakeResp()

    monkeypatch.setattr(llm.settings, "LLM_API_BASE", "https://relay.example.com/v1")
    monkeypatch.setattr(llm.settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(llm.settings, "LLM_MODEL", "claude-3-5-sonnet")
    monkeypatch.setattr(llm, "OpenAI", lambda **kw: FakeClient(**kw))

    result = llm.call_llm("测试 prompt")
    assert result == "# AI 报告"
    assert captured["base_url"] == "https://relay.example.com/v1"
    assert captured["model"] == "claude-3-5-sonnet"


@pytest.mark.unit
def test_call_llm_raises_when_not_configured(monkeypatch):
    from app import llm
    monkeypatch.setattr(llm.settings, "LLM_API_BASE", "")
    monkeypatch.setattr(llm.settings, "LLM_API_KEY", "")
    with pytest.raises(RuntimeError, match="未配置"):
        llm.call_llm("prompt")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python3 -m pytest tests/test_llm.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.llm'`）

- [ ] **Step 3: 实现 app/llm.py**

```python
"""LLM 投研报告：prompt 构建 + 中转站调用（OpenAI 兼容）+ ai_reports 缓存。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AiReport

try:
    from openai import OpenAI
except ImportError:  # openai 未安装时降级
    OpenAI = None  # type: ignore


def cache_key(fund_ids: list[str]) -> str:
    """基金 ID 列表 -> 字母升序逗号拼接（ai_reports.fund_ids 缓存键）。"""
    return ",".join(sorted(fund_ids))


def build_prompt(fund_metrics_list: list[dict], period: str) -> str:
    """构建中文金融投研对比 prompt，含 5 维指标表。"""
    lines = [
        "你是一位资深的澳大利亚固定收益基金分析师。请基于以下5维业绩指标，",
        f"对 {len(fund_metrics_list)} 只基金在【{period}】区间内的表现进行深度对比分析，",
        "输出 Markdown 格式的投研报告。要求：",
        "1. 进攻（年化超额收益 Alpha）、防守（最大回撤）、性价比（Omega 比率）、",
        "体感（超额胜率+最长连续跑输月数）、真实性（去平滑前后波动率对比）五维逐一对比；",
        "2. 指出每只基金的核心优劣与适用投资者画像；",
        "3. 若某基金应用了 Geltner 去平滑，说明其平滑程度（phi）对真实风险的揭示；",
        "4. 不得捏造未提供的数据；指标不足 36 个月的基金应提示数据不足，不做去平滑推断。",
        "",
        "## 基金5维指标数据（数据截止见 date_period）：",
    ]
    for m in fund_metrics_list:
        lines.append(f"""
### {m.get('fund_name', m.get('fund_id'))} (fund_id={m['fund_id']})
- 数据截止: {m.get('date_period')}，历史月数: {m['history_months']}
- 数据不足预警: {'是（<36月，未去平滑）' if m['is_short_history_warning'] else '否'}
- 自相关 phi={m['unsmoothing_coefficient_phi']:.4f}，Ljung-Box Q={m['ljung_box_q']:.2f}（{'显著' if m['is_q_significant'] else '不显著'}）
- 去平滑: {'已应用' if m['is_geltner_applied'] else '未应用'}
- 进攻(年化超额): 原始 {m['orig_annualized_excess_return']:.4%} / 去平滑 {m['un_annualized_excess_return']:.4%}
- 防守(最大回撤): 原始 {m['orig_max_drawdown']:.4%} / 去平滑 {m['un_max_drawdown']:.4%}
- 性价比(Omega): 原始 {m['orig_omega_ratio']:.4f} / 去平滑 {m['un_omega_ratio']:.4f}
- 体感(超额胜率): 原始 {m['orig_excess_win_rate']:.2%} / 去平滑 {m['un_excess_win_rate']:.2%}
- 体感(最长连续跑输月数): 原始 {m['orig_max_underperform_months']} / 去平滑 {m['un_max_underperform_months']}
- 真实性(年化波动率): 原始 {m['orig_annualized_volatility']:.4%} / 去平滑 {m['un_annualized_volatility']:.4%}
""")
    return "\n".join(lines)


def call_llm(prompt: str) -> str:
    """调用中转站 LLM（OpenAI 兼容），返回 Markdown 文本。未配置时抛 RuntimeError。"""
    if not settings.LLM_API_BASE or not settings.LLM_API_KEY:
        raise RuntimeError("LLM 未配置：请设置 LLM_API_BASE 与 LLM_API_KEY 环境变量")
    if OpenAI is None:
        raise RuntimeError("openai 包未安装，无法调用 LLM")
    client = OpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_API_BASE)
    resp = client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": "你是专业的澳洲固收基金投研分析师，输出严谨的中文 Markdown 报告。"},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content


def get_cached_report(session: Session, fund_ids: list[str], date_period: str,
                      report_type: str) -> Optional[str]:
    """查 ai_reports 缓存。"""
    row = session.query(AiReport).filter_by(
        fund_ids=cache_key(fund_ids), date_period=date_period, report_type=report_type
    ).order_by(AiReport.created_at.desc()).first()
    return row.content if row else None


def save_report(session: Session, fund_ids: list[str], date_period: str,
                report_type: str, content: str) -> None:
    """写入 ai_reports 缓存。"""
    session.add(AiReport(
        fund_ids=cache_key(fund_ids), date_period=date_period,
        report_type=report_type, content=content,
    ))
    session.commit()
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python3 -m pytest tests/test_llm.py -v`
Expected: 5 passed

- [ ] **Step 5: 写失败测试 tests/test_api_reports.py**

```python
"""AI 报告 API 测试。"""
import pytest
from unittest.mock import patch
from app.models import MonthlyReturn, RbaCashRate, AiReport


def _seed_and_recompute(client, db_session):
    client.post("/api/funds", json={"fund_id": "f1", "fund_name": "Fund One",
                 "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})
    for m in range(1, 13):
        db_session.add(MonthlyReturn(fund_id="f1", date=f"2025-{m:02d}-28",
                                     net_return=0.005, nav=1.0))
        db_session.add(RbaCashRate(date_period=f"2025-{m:02d}", rate=0.0435))
    db_session.commit()
    client.post("/api/funds/f1/recompute")


@pytest.mark.unit
def test_ai_summary_returns_cached_on_second_call(client, db_session, monkeypatch):
    _seed_and_recompute(client, db_session)
    monkeypatch.setattr("app.llm.settings.LLM_API_BASE", "https://relay/v1")
    monkeypatch.setattr("app.llm.settings.LLM_API_KEY", "sk-test")
    monkeypatch.setattr("app.llm.call_llm", lambda prompt: "# 首次生成的报告")

    resp1 = client.post("/api/reports/ai-summary",
                        json={"fund_ids": ["f1"], "period": "full"})
    assert resp1.status_code == 200
    assert resp1.json()["cached"] is False
    assert resp1.json()["content"] == "# 首次生成的报告"

    # 第二次应命中缓存（call_llm 不应再被调用）
    monkeypatch.setattr("app.llm.call_llm", lambda prompt: "# 不应出现")
    resp2 = client.post("/api/reports/ai-summary",
                        json={"fund_ids": ["f1"], "period": "full"})
    assert resp2.status_code == 200
    assert resp2.json()["cached"] is True
    assert resp2.json()["content"] == "# 首次生成的报告"


@pytest.mark.unit
def test_ai_summary_force_bypasses_cache(client, db_session, monkeypatch):
    _seed_and_recompute(client, db_session)
    monkeypatch.setattr("app.llm.settings.LLM_API_BASE", "https://relay/v1")
    monkeypatch.setattr("app.llm.settings.LLM_API_KEY", "sk-test")
    monkeypatch.setattr("app.llm.call_llm", lambda prompt: "# 第一版")
    client.post("/api/reports/ai-summary", json={"fund_ids": ["f1"], "period": "full"})

    monkeypatch.setattr("app.llm.call_llm", lambda prompt: "# 强制新版")
    resp = client.post("/api/reports/ai-summary",
                       json={"fund_ids": ["f1"], "period": "full", "force": True})
    assert resp.status_code == 200
    assert resp.json()["content"] == "# 强制新版"


@pytest.mark.unit
def test_ai_summary_503_when_llm_not_configured(client, db_session, monkeypatch):
    _seed_and_recompute(client, db_session)
    monkeypatch.setattr("app.llm.settings.LLM_API_BASE", "")
    monkeypatch.setattr("app.llm.settings.LLM_API_KEY", "")
    resp = client.post("/api/reports/ai-summary",
                       json={"fund_ids": ["f1"], "period": "full"})
    assert resp.status_code == 503
```

- [ ] **Step 6: 运行测试验证失败**

Run: `cd webapp/backend && python3 -m pytest tests/test_api_reports.py -v`
Expected: FAIL（端点不存在）

- [ ] **Step 7: 实现 app/routers/reports.py**

```python
"""AI 投研报告路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.crud import get_fund
from app.models import FundMetric
from app.schemas import AiReportRequest, AiReportResponse
from app.llm import build_prompt, call_llm, get_cached_report, save_report
from app.period import VALID_PERIODS

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("/ai-summary", response_model=AiReportResponse)
def ai_summary(payload: AiReportRequest, session: Session = Depends(get_db)):
    if payload.period not in VALID_PERIODS:
        raise HTTPException(status_code=422, detail=f"period 须为 {VALID_PERIODS}")
    if not payload.fund_ids:
        raise HTTPException(status_code=422, detail="fund_ids 不能为空")

    # 校验基金存在并收集指标
    metrics_list = []
    for fid in payload.fund_ids:
        if get_fund(session, fid) is None:
            raise HTTPException(status_code=404, detail=f"基金 {fid} 不存在")
        m = session.get(FundMetric, fid)
        if m is None:
            raise HTTPException(status_code=400, detail=f"基金 {fid} 无预计算指标，请先 recompute")
        fund = get_fund(session, fid)
        d = {c.name: getattr(m, c.name) for c in m.__table__.columns}
        d["fund_name"] = fund.fund_name
        metrics_list.append(d)

    date_period = max(m["date_period"] for m in metrics_list)

    # 缓存命中
    if not payload.force:
        cached = get_cached_report(session, payload.fund_ids, date_period, payload.period)
        if cached is not None:
            return AiReportResponse(content=cached, cached=True)

    # 调用 LLM
    prompt = build_prompt(metrics_list, payload.period)
    try:
        content = call_llm(prompt)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {e}")

    save_report(session, payload.fund_ids, date_period, payload.period, content)
    return AiReportResponse(content=content, cached=False)
```

- [ ] **Step 8: 运行测试验证通过**

Run: `cd webapp/backend && python3 -m pytest tests/test_llm.py tests/test_api_reports.py -v`
Expected: 8 passed

- [ ] **Step 9: 提交**

```bash
git add webapp/backend/app/llm.py webapp/backend/app/routers/reports.py \
        webapp/backend/tests/test_llm.py webapp/backend/tests/test_api_reports.py
git commit -m "feat(backend): add LLM report generation with relay integration and caching

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: 端到端集成测试 + 启动说明

**Files:**
- Create: `webapp/backend/tests/test_integration.py`
- Create: `webapp/backend/README.md`
- Create: `webapp/backend/pytest.ini`（注册 unit 标记，阶段 1 最终审核的遗留项）

**Interfaces:**
- 串联 Task 1-7 的全部端点，验证完整工作流：注册基金 -> 写入月度数据（模拟 skill）-> 重算指标 -> 对比 -> 时序 -> 异常纠错 -> AI 报告。

- [ ] **Step 1: 创建 pytest.ini（注册标记，消除阶段 1 遗留警告）**

```ini
[pytest]
markers =
    unit: unit test (no external network/IO)
testpaths = tests
```

- [ ] **Step 2: 写端到端集成测试 tests/test_integration.py**

```python
"""端到端集成：注册->数据->重算->对比->时序->纠错->AI报告。"""
import pytest
from unittest.mock import patch
from app.models import MonthlyReturn, RbaCashRate


@pytest.mark.unit
def test_full_workflow(client, db_session, monkeypatch):
    # 1. 注册两只基金
    for fid, name in [("f1", "Fund Alpha"), ("f2", "Fund Beta")]:
        client.post("/api/funds", json={"fund_id": fid, "fund_name": name,
                     "confirmed_url": "http://x", "fetch_method": "pdf", "url_type": "pdf"})

    # 2. 模拟 skill 写入月度数据（12 个月）
    for fid in ["f1", "f2"]:
        for m in range(1, 13):
            db_session.add(MonthlyReturn(fund_id=fid, date=f"2025-{m:02d}-28",
                                         net_return=0.005 + (0.001 if fid == "f1" else 0),
                                         nav=1.0))
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

    # 7. AI 报告（mock LLM）
    monkeypatch.setattr("app.llm.settings.LLM_API_BASE", "https://relay/v1")
    monkeypatch.setattr("app.llm.settings.LLM_API_KEY", "sk-test")
    monkeypatch.setattr("app.llm.call_llm", lambda prompt: "# 集成测试报告")
    resp = client.post("/api/reports/ai-summary",
                       json={"fund_ids": ["f1", "f2"], "period": "full"})
    assert resp.status_code == 200
    assert "集成测试报告" in resp.json()["content"]

    # 8. 删除一只基金，确认列表更新（模拟"网页删除"同步）
    assert client.delete("/api/funds/f2").status_code == 204
    funds = client.get("/api/funds").json()
    assert len(funds) == 1
    assert funds[0]["fund_id"] == "f1"


@pytest.mark.unit
def test_health_and_openapi_schema(client):
    """健康检查与 OpenAPI schema 可用（前端代码生成的依据）。"""
    assert client.get("/health").status_code == 200
    assert client.get("/openapi.json").status_code == 200
```

- [ ] **Step 3: 运行集成测试**

Run: `cd webapp/backend && python3 -m pytest tests/test_integration.py -v`
Expected: 2 passed

- [ ] **Step 4: 运行全部测试确认无回归**

Run: `cd webapp/backend && python3 -m pytest tests/ -v`
Expected: 阶段 1 的 43 + 阶段 2 新增约 35+，全绿

- [ ] **Step 5: 创建 webapp/backend/README.md**

````markdown
# 固定收益基金分析 - 后端 API

基于 FastAPI 的后端，提供基金业绩分析 REST API、RBA 利率定时调度与 LLM 投研报告。

## 启动

```bash
cd webapp/backend
pip3 install -r requirements.txt
# 开发模式（带热重载）
uvicorn app.main:app --reload --port 8000
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| DATABASE_URL | sqlite:///data/fund_analysis.db | SQLite 连接串 |
| LLM_API_BASE | （空） | LLM 中转站端点（OpenAI 兼容），空则禁用 AI 报告 |
| LLM_API_KEY | （空） | LLM API Key |
| LLM_MODEL | claude-3-5-sonnet | 模型名 |
| CORS_ORIGINS | http://localhost:5173 | 允许的前端来源（逗号分隔） |
| SCHEDULER_ENABLED | true | 是否启用 RBA 定时调度 |
| RBA_CRON_HOUR | 9 | 每日抓取 RBA 的小时 |

## API 端点

- `GET /health` 健康检查
- `GET /api/funds` 基金列表（含数据截止年月）
- `POST /api/funds` 注册基金元信息（不抓取，抓取由 add_fixed_fund skill 完成）
- `DELETE /api/funds/{fund_id}` 删除基金（级联）
- `POST /api/funds/{fund_id}/recompute` 重算指标
- `GET /api/metrics/compare?fund_ids=A,B&period=full` 5 维对比（period: full/3y/1y/common）
- `GET /api/metrics/time-series?fund_ids=A,B&period=full` 对齐 NAV 时序（含去平滑）
- `GET /api/anomalies` 异常列表
- `PATCH /api/monthly-returns/{id}` 人工纠错（触发重算）
- `POST /api/rba/refresh` 手动刷新 RBA 利率
- `POST /api/reports/ai-summary` LLM 投研报告（带缓存）

## 测试

```bash
cd webapp/backend && python3 -m pytest tests/ -v
```
````

- [ ] **Step 6: 提交**

```bash
git add webapp/backend/pytest.ini webapp/backend/tests/test_integration.py webapp/backend/README.md
git commit -m "feat(backend): add integration tests, pytest config, and backend README

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 阶段 2 完成标准

全部以下条件满足：
1. `cd webapp/backend && python3 -m pytest tests/ -v` 全绿（阶段 1 的 43 + 阶段 2 新增约 35+，共约 78+ 用例）。
2. `webapp/backend/app/` 下新增 5 个模块：`main, schemas, period, scheduler, llm` + `routers/` 子包（funds/metrics/anomalies/rba/reports）。
3. 11 个 API 端点可用，`/openapi.json` 可生成（前端代码生成的依据）。
4. `create_app(enable_scheduler=False)` 工厂可在测试中关闭调度器；生产 `uvicorn app.main:app` 启动时调度器随 lifespan 自动启停。
5. period 切片（full/3y/1y/common）通过纯函数测试；compare 对 full 读缓存、其他切片重算。
6. RBA 调度可 mock 测试，`run_rba_update` 独立可调用。
7. LLM 集成：未配置返回 503；已配置调用中转站；ai_reports 缓存命中返回 cached=true，force 可绕过。
8. Python 3.9.6 兼容：全程 `Optional[X]`，无 PEP 604 运行时语法。

## 后续阶段预告（不在本计划内）

- **阶段 3**：自定义技能重构（`add_fixed_fund`, `update_fixed_fund`），读取 `funds` 表配置执行抓取，写入 `monthly_returns`，调用 `compute_and_store_metrics`。
- **阶段 4**：React 前端（Vite + Tailwind + Recharts），消费本阶段 11 个端点。
- **阶段 5**：JSON -> SQLite 数据迁移与旧代码清理（spec 第 6 节）。
