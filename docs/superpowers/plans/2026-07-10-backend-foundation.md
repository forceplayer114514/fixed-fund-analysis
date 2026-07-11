# 后端地基实现计划 (阶段 1/5)：SQLite 模型 + 5维计算引擎 + CRUD + RBA

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 `webapp/backend` 后端地基--SQLite 数据库模型（6张表）、5维核心指标计算引擎、异常检测、RBA 利率抓取、CRUD 操作与指标编排管道，全部通过 pytest 单元测试。

**Architecture:** 在 `webapp/backend/app/` 下构建纯 Python 后端核心层（暂不含 FastAPI 路由，那是阶段2）。计算引擎从现有 `scripts/metrics.py` 移植并扩展为 5 维指标（超额收益、最大回撤、Omega 比率、胜率+最长连续跑输、Geltner 三重防火墙去平滑）。数据持久化用 SQLAlchemy 2.0 + SQLite。

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0, SQLite, pytest, requests, beautifulsoup4

## Global Constraints

- **数据完整性（最高优先级）**：禁止捏造任何金融数据；数据缺口零容忍，缺月必须报错不得插值；异常值如实保留并标记，交人工复核，不得自动纠正（CLAUDE.md 第一、五条）。
- **APIR 代码格式**：正则 `^[A-Z]{3}\d{4}AU$`（如 ETL5010AU），字段可空（nullable）以支持无 APIR 的基金（如 Stake）。
- **基金防重**：基于 `fund_name`（标准名称）设 `UNIQUE` 约束，不依赖 APIR。
- **36 个月硬门禁**：历史不足 36 个月的基金，`is_short_history_warning=1`，跳过 Geltner 去平滑，去平滑指标等同于原始指标。
- **Geltner 三重防火墙**：(1) `n >= 36`；(2) Ljung-Box `Q > 3.841`（95%置信度）；(3) `0 <= phi <= 0.85`。三者全满足才应用去平滑。
- **Omega 比率**：Target = 逐月 RBA，分母（累积亏损面积）为 0 时返回 `float('inf')`。
- **异常检测**：MAD（中位数绝对偏差），`threshold_sigma=3.0`，至少 12 个月数据才有意义。
- **超额收益算法**：逐月扣减当期 RBA 得 $r_{e,t} = r_t - RBA_t/12$，再复利年化 $(\prod(1+r_{e,t}))^{12/n} - 1$（spec 4.1 权威定义，与旧 `generate_report.py` 的分别年化相减法不同）。
- **语言**：代码注释与 print 输出使用中文（遵循 CLAUDE.md 语言偏好）。

---

## 文件结构

```
webapp/backend/
├── requirements.txt                  # 后端依赖
├── app/
│   ├── __init__.py
│   ├── config.py                     # 配置（数据库路径等）
│   ├── database.py                   # SQLAlchemy 引擎、会话、建表
│   ├── models.py                     # 6张表的 ORM 模型
│   ├── calculations.py               # 5维纯计算函数（无副作用、无IO）
│   ├── anomaly.py                    # MAD 异常检测
│   ├── rba.py                        # RBA 利率抓取与入库
│   ├── crud.py                       # 数据库 CRUD + NAV 重计算
│   └── metrics_pipeline.py           # 编排：读DB -> 计算 -> 写回 fund_metrics
└── tests/
    ├── __init__.py
    ├── conftest.py                   # 内存数据库 fixture
    ├── test_models.py
    ├── test_calculations.py
    ├── test_anomaly.py
    ├── test_rba.py
    └── test_metrics_pipeline.py
```

**职责边界**：
- `calculations.py`：纯函数，输入 list[float] 输出 float/dict，无数据库依赖，无网络IO。最易测试。
- `anomaly.py`：纯函数，输入时序 dict 列表输出异常 dict 列表。
- `models.py`：仅定义表结构，不含业务逻辑。
- `crud.py`：所有数据库读写 + NAV 重计算逻辑。
- `rba.py`：网络抓取（可被 mock 测试）+ 入库委托给 crud。
- `metrics_pipeline.py`：编排层，串联 crud + calculations + anomaly，产出 `fund_metrics` 记录。

---

### Task 1: 后端脚手架、配置与数据库连接

**Files:**
- Create: `webapp/backend/requirements.txt`
- Create: `webapp/backend/app/__init__.py`
- Create: `webapp/backend/app/config.py`
- Create: `webapp/backend/app/database.py`
- Create: `webapp/backend/tests/__init__.py`
- Create: `webapp/backend/tests/conftest.py`
- Create: `webapp/backend/tests/test_database.py`

**Interfaces:**
- Produces: `get_db()` 生成器（依赖注入用），`Base`（ declarative 基类），`init_db()` 建表函数，`SessionLocal` 会话工厂。`config.settings` 含 `DATABASE_URL`。

- [ ] **Step 1: 创建 requirements.txt**

```
sqlalchemy>=2.0.0
requests>=2.31.0
beautifulsoup4>=4.12.0
pytest>=8.0.0
```

- [ ] **Step 2: 创建 app/__init__.py 和 tests/__init__.py（空文件）**

```python
# webapp/backend/app/__init__.py
```

```python
# webapp/backend/tests/__init__.py
```

- [ ] **Step 3: 创建 app/config.py**

```python
"""后端配置：通过环境变量覆盖默认值。"""
import os
from pathlib import Path

# 数据库默认放在仓库根目录 data/fund_analysis.db
_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent  # webapp/backend -> 仓库根
_DEFAULT_DB_PATH = _BASE_DIR / "data" / "fund_analysis.db"


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")
    # RBA 抓取相关（阶段2的 LLM 配置在此预留但不启用）
    RBA_BASE_URL: str = "https://www.rba.gov.au/"
    RBA_HISTORY_API: str = "https://api.db.nomics.world/v22/series/RBA/F1/FIRMMCRTD?observations=1"


settings = Settings()
```

- [ ] **Step 4: 创建 app/database.py**

```python
"""SQLAlchemy 引擎、会话工厂与建表。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from app.config import settings


class Base(DeclarativeBase):
    pass


# SQLite 需要 check_same_thread=False 以支持多线程/异步场景
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db():
    """创建所有表（幂等）。必须在导入 models 之后调用。"""
    from app import models  # noqa: F401 确保模型已注册
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI 依赖注入用的会话生成器。也可在脚本中直接用。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 5: 创建 tests/conftest.py（内存数据库 fixture）**

```python
"""pytest 公共 fixture：每个测试用独立的内存 SQLite 数据库。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app import models  # noqa: F401 注册所有模型


@pytest.fixture
def db_session():
    """提供一个隔离的内存数据库会话，测试结束自动销毁。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
```

- [ ] **Step 6: 写失败测试 tests/test_database.py**

```python
"""验证数据库连接与建表。"""
import pytest
from sqlalchemy import inspect

from app.database import Base, init_db, SessionLocal


@pytest.mark.unit
def test_init_db_creates_all_tables():
    """init_db 应创建所有已注册的表。"""
    # 使用内存引擎覆盖默认引擎
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import app.database as db_mod

    mem_engine = create_engine("sqlite:///:memory:")
    old_engine = db_mod.engine
    db_mod.engine = mem_engine
    db_mod.SessionLocal.configure(bind=mem_engine)
    try:
        init_db()
        inspector = inspect(mem_engine)
        table_names = inspector.get_table_names()
        # 至少应包含 funds 表（后续任务逐步增加其余表）
        assert "funds" in table_names
    finally:
        db_mod.engine = old_engine
        db_mod.SessionLocal.configure(bind=old_engine)
```

- [ ] **Step 7: 运行测试验证它失败（models 尚未定义 funds 表）**

Run: `cd webapp/backend && python -m pytest tests/test_database.py -v`
Expected: FAIL（`init_db` 调用时 models 中无 Funds 模型，`funds` 表不存在）

- [ ] **Step 8: 安装依赖**

Run: `cd webapp/backend && pip install -r requirements.txt`
Expected: 成功安装 sqlalchemy, requests, beautifulsoup4, pytest

- [ ] **Step 9: 提交**

```bash
git add webapp/backend/
git commit -m "feat(backend): scaffold backend project with database connection

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 数据库模型（6张表）

**Files:**
- Create: `webapp/backend/app/models.py`
- Create: `webapp/backend/tests/test_models.py`

**Interfaces:**
- Produces: ORM 模型类 `Fund`, `MonthlyReturn`, `Anomaly`, `RbaCashRate`, `FundMetric`, `AiReport`，字段名与 spec 第3节 schema 完全一致。`Fund` 主键 `fund_id` (TEXT)，`fund_name` UNIQUE。所有子表通过 `fund_id` 外键级联删除。

- [ ] **Step 1: 写失败测试 tests/test_models.py**

```python
"""验证6张表的ORM模型：插入、唯一约束、级联删除。"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Fund, MonthlyReturn, Anomaly, RbaCashRate, FundMetric, AiReport


@pytest.mark.unit
def test_insert_fund_and_returns(db_session):
    """能插入基金及其月度收益，并通过关系访问。"""
    fund = Fund(fund_id="stake_accumulate", fund_name="Stake Accumulate",
                confirmed_url="https://example.com", fetch_method="pdf", url_type="pdf")
    db_session.add(fund)
    db_session.commit()

    ret = MonthlyReturn(fund_id="stake_accumulate", date="2026-05-31",
                        net_return=0.0053, nav=1.0053)
    db_session.add(ret)
    db_session.commit()

    assert db_session.query(MonthlyReturn).count() == 1
    assert fund.monthly_returns[0].net_return == pytest.approx(0.0053)


@pytest.mark.unit
def test_fund_name_unique_constraint(db_session):
    """fund_name 唯一约束：重复插入同名基金应报错。"""
    fund1 = Fund(fund_id="fund_a", fund_name="Duplicate Fund",
                 confirmed_url="http://a", fetch_method="pdf", url_type="pdf")
    db_session.add(fund1)
    db_session.commit()

    fund2 = Fund(fund_id="fund_b", fund_name="Duplicate Fund",
                 confirmed_url="http://b", fetch_method="pdf", url_type="pdf")
    db_session.add(fund2)
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.unit
def test_apir_code_nullable(db_session):
    """apir_code 可为空（支持 Stake 等无 APIR 基金）。"""
    fund = Fund(fund_id="stake", fund_name="Stake Fund",
                apir_code=None, confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    db_session.add(fund)
    db_session.commit()
    assert fund.apir_code is None


@pytest.mark.unit
def test_monthly_return_unique_date_per_fund(db_session):
    """同一基金同一月份不能重复插入。"""
    fund = Fund(fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    db_session.add(fund)
    db_session.commit()

    db_session.add(MonthlyReturn(fund_id="f1", date="2026-05-31", net_return=0.01, nav=1.01))
    db_session.commit()

    db_session.add(MonthlyReturn(fund_id="f1", date="2026-05-31", net_return=0.02, nav=1.02))
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.unit
def test_cascade_delete_fund_removes_children(db_session):
    """删除基金应级联删除其月度收益、异常、指标、AI报告。"""
    fund = Fund(fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    db_session.add(fund)
    db_session.commit()

    db_session.add(MonthlyReturn(fund_id="f1", date="2026-05-31", net_return=0.01, nav=1.01))
    db_session.add(Anomaly(fund_id="f1", date="2026-05-31", value=0.99,
                           z_score=3.5, threshold_sigma=3.0, mean=0.01, stdev=0.02))
    db_session.add(FundMetric(fund_id="f1", date_period="2026-05", history_months=1,
                              is_short_history_warning=1, unsmoothing_coefficient_phi=0.0,
                              is_geltner_applied=0, orig_annualized_excess_return=0.0,
                              un_annualized_excess_return=0.0, orig_max_drawdown=0.0,
                              un_max_drawdown=0.0, orig_omega_ratio=1.0, un_omega_ratio=1.0,
                              orig_excess_win_rate=0.5, un_excess_win_rate=0.5,
                              orig_max_underperform_months=1, un_max_underperform_months=1,
                              orig_annualized_volatility=0.01, un_annualized_volatility=0.01,
                              ljung_box_q=0.0, is_q_significant=0))
    db_session.commit()

    db_session.delete(fund)
    db_session.commit()

    assert db_session.query(MonthlyReturn).count() == 0
    assert db_session.query(Anomaly).count() == 0
    assert db_session.query(FundMetric).count() == 0


@pytest.mark.unit
def test_rba_cash_rate_upsert_style(db_session):
    """RBA 利率表以 date_period 为主键。"""
    db_session.add(RbaCashRate(date_period="2026-05", rate=0.0435))
    db_session.commit()
    assert db_session.query(RbaCashRate).count() == 1
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python -m pytest tests/test_models.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.models'`）

- [ ] **Step 3: 实现 app/models.py**

```python
"""数据库 ORM 模型：6张表，字段与 spec 第3节 schema 一致。"""
from sqlalchemy import String, Float, Integer, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Fund(Base):
    __tablename__ = "funds"

    fund_id: Mapped[str] = mapped_column(String, primary_key=True)
    fund_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    apir_code: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    confirmed_url: Mapped[str] = mapped_column(Text, nullable=False)
    fetch_method: Mapped[str] = mapped_column(String, nullable=False)
    url_type: Mapped[str] = mapped_column(String, nullable=False)
    max_pdf_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verified_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str | None] = mapped_column(String, server_default="(datetime('now'))")

    monthly_returns: Mapped[list["MonthlyReturn"]] = relationship(
        back_populates="fund", cascade="all, delete-orphan")
    anomalies: Mapped[list["Anomaly"]] = relationship(
        back_populates="fund", cascade="all, delete-orphan")
    metrics: Mapped["FundMetric | None"] = relationship(
        back_populates="fund", cascade="all, delete-orphan", uselist=False)


class MonthlyReturn(Base):
    __tablename__ = "monthly_returns"
    __table_args__ = (UniqueConstraint("fund_id", "date", name="uq_fund_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_id: Mapped[str] = mapped_column(ForeignKey("funds.fund_id", ondelete="CASCADE"), nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)  # YYYY-MM-DD
    net_return: Mapped[float] = mapped_column(Float, nullable=False)
    nav: Mapped[float] = mapped_column(Float, nullable=False)
    commentary_truth: Mapped[float | None] = mapped_column(Float, nullable=True)

    fund: Mapped["Fund"] = relationship(back_populates="monthly_returns")


class Anomaly(Base):
    __tablename__ = "anomalies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_id: Mapped[str] = mapped_column(ForeignKey("funds.fund_id", ondelete="CASCADE"), nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    z_score: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_sigma: Mapped[float] = mapped_column(Float, nullable=False)
    mean: Mapped[float] = mapped_column(Float, nullable=False)
    stdev: Mapped[float] = mapped_column(Float, nullable=False)

    fund: Mapped["Fund"] = relationship(back_populates="anomalies")


class RbaCashRate(Base):
    __tablename__ = "rba_cash_rates"

    date_period: Mapped[str] = mapped_column(String, primary_key=True)  # YYYY-MM
    rate: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[str | None] = mapped_column(String, server_default="(datetime('now'))")


class FundMetric(Base):
    __tablename__ = "fund_metrics"

    fund_id: Mapped[str] = mapped_column(ForeignKey("funds.fund_id", ondelete="CASCADE"), primary_key=True)
    date_period: Mapped[str] = mapped_column(String, nullable=False)
    history_months: Mapped[int] = mapped_column(Integer, nullable=False)
    is_short_history_warning: Mapped[int] = mapped_column(Integer, nullable=False)
    unsmoothing_coefficient_phi: Mapped[float] = mapped_column(Float, nullable=False)
    is_geltner_applied: Mapped[int] = mapped_column(Integer, nullable=False)
    # 维度1：进攻
    orig_annualized_excess_return: Mapped[float] = mapped_column(Float, nullable=False)
    un_annualized_excess_return: Mapped[float] = mapped_column(Float, nullable=False)
    # 维度2：防守
    orig_max_drawdown: Mapped[float] = mapped_column(Float, nullable=False)
    un_max_drawdown: Mapped[float] = mapped_column(Float, nullable=False)
    # 维度3：性价比
    orig_omega_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    un_omega_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    # 维度4：体感与煎熬度
    orig_excess_win_rate: Mapped[float] = mapped_column(Float, nullable=False)
    un_excess_win_rate: Mapped[float] = mapped_column(Float, nullable=False)
    orig_max_underperform_months: Mapped[int] = mapped_column(Integer, nullable=False)
    un_max_underperform_months: Mapped[int] = mapped_column(Integer, nullable=False)
    # 维度5：真实性辅助
    orig_annualized_volatility: Mapped[float] = mapped_column(Float, nullable=False)
    un_annualized_volatility: Mapped[float] = mapped_column(Float, nullable=False)
    ljung_box_q: Mapped[float] = mapped_column(Float, nullable=False)
    is_q_significant: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[str | None] = mapped_column(String, server_default="(datetime('now'))")

    fund: Mapped["Fund"] = relationship(back_populates="metrics")


class AiReport(Base):
    __tablename__ = "ai_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_ids: Mapped[str] = mapped_column(Text, nullable=False)
    date_period: Mapped[str] = mapped_column(String, nullable=False)
    report_type: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str | None] = mapped_column(String, server_default="(datetime('now'))")
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python -m pytest tests/test_models.py tests/test_database.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/models.py webapp/backend/tests/test_models.py
git commit -m "feat(backend): add 6-table SQLAlchemy ORM models

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 基础计算函数（年化收益、波动率、最大回撤）

**Files:**
- Create: `webapp/backend/app/calculations.py`
- Create: `webapp/backend/tests/test_calculations.py`

**Interfaces:**
- Produces: `calculate_annualized_return(compounded_return: float, n_months: int, fund_name: str = "Unknown") -> float`、`calculate_annualized_volatility(returns: list[float], fund_name: str = "Unknown") -> float`、`calculate_max_drawdown(nav_series: list[float], fund_name: str = "Unknown") -> float`。行为与现有 `scripts/metrics.py` 完全一致（数值断言复用 `tests/test_metrics.py`）。

- [ ] **Step 1: 写失败测试 tests/test_calculations.py**

```python
"""基础计算函数测试，断言复用 tests/test_metrics.py 以保证移植精度。"""
import pytest
import math

from app.calculations import (
    calculate_annualized_return,
    calculate_annualized_volatility,
    calculate_max_drawdown,
)


@pytest.mark.unit
def test_calculate_annualized_return():
    assert calculate_annualized_return(1.1025, 6, "TestFund") == pytest.approx(0.21550625)
    with pytest.raises(ValueError) as excinfo:
        calculate_annualized_return(1.05, 0, "TestFund")
    assert "[TestFund]" in str(excinfo.value) and "n_months=0" in str(excinfo.value)


@pytest.mark.unit
def test_calculate_annualized_volatility():
    assert calculate_annualized_volatility([0.01, 0.02, 0.03], "TestFund") == pytest.approx(0.03464101615)
    assert calculate_annualized_volatility([0.01], "TestFund") == 0.0
    assert calculate_annualized_volatility([], "TestFund") == 0.0


@pytest.mark.unit
def test_calculate_max_drawdown():
    assert calculate_max_drawdown([100.0, 105.0, 94.5, 91.35, 110.0], "TestFund") == pytest.approx(-0.13)
    with pytest.raises(ValueError) as excinfo:
        calculate_max_drawdown([0.0, 10.0], "TestFund")
    assert "peak=0.0" in str(excinfo.value)
    assert calculate_max_drawdown([], "TestFund") == 0.0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.calculations'`）

- [ ] **Step 3: 实现 app/calculations.py（基础部分）**

```python
"""5维核心指标纯计算函数。无数据库依赖、无网络IO，仅接受 list[float]。

移植自 scripts/metrics.py 并扩展为5维体系。所有函数保持与原实现数值一致。
"""
from __future__ import annotations


def calculate_annualized_return(compounded_return: float, n_months: int,
                                fund_name: str = "Unknown") -> float:
    """复利年化收益率：(compounded_return) ** (12/n) - 1。"""
    if n_months <= 0:
        raise ValueError(f"[{fund_name}] n_months={n_months}, 无法计算年化收益率，时间序列月份数必须为正数")
    return (compounded_return ** (12.0 / n_months)) - 1.0


def calculate_annualized_volatility(returns: list[float],
                                    fund_name: str = "Unknown") -> float:
    """年化波动率：月度收益标准差 * sqrt(12)。"""
    n = len(returns)
    if n < 2:
        return 0.0
    mean_r = sum(returns) / n
    denominator = n - 1
    if denominator <= 0:
        raise ValueError(f"[{fund_name}] denominator={denominator} (n_months - 1), 无法计算年化波动率")
    variance = sum((r - mean_r) ** 2 for r in returns) / denominator
    return (variance * 12.0) ** 0.5


def calculate_max_drawdown(nav_series: list[float],
                           fund_name: str = "Unknown") -> float:
    """绝对最大回撤：基于累计NAV序列，返回最深回撤比例（负数）。"""
    if not nav_series:
        return 0.0
    max_dd = 0.0
    peak = nav_series[0]
    for nav in nav_series:
        if nav > peak:
            peak = nav
        if peak < 1e-4:
            raise ValueError(f"[{fund_name}] peak={peak} (低于 1e-4)，无法计算最大回撤，请检查该基金的 NAV 数据是否异常")
        dd = (nav - peak) / peak
        if dd < max_dd:
            max_dd = dd
    return max_dd
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/calculations.py webapp/backend/tests/test_calculations.py
git commit -m "feat(backend): add basic calculation functions (annualized return, volatility, max drawdown)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Omega 比率、超额胜率、最长连续跑输月数

**Files:**
- Modify: `webapp/backend/app/calculations.py`（追加函数）
- Modify: `webapp/backend/tests/test_calculations.py`（追加测试）

**Interfaces:**
- Produces: `calculate_omega_ratio(excess_returns: list[float]) -> float`（Target=RBA 的盈亏面积比，分母0返回 `inf`）、`calculate_excess_win_rate(excess_returns: list[float]) -> float`（跑赢RBA月数占比）、`calculate_max_consecutive_underperform(excess_returns: list[float]) -> int`（连续 `r_e <= 0` 的最长月数）。

- [ ] **Step 1: 追加失败测试到 tests/test_calculations.py**

```python
from app.calculations import (
    calculate_omega_ratio,
    calculate_excess_win_rate,
    calculate_max_consecutive_underperform,
)
import math


@pytest.mark.unit
def test_calculate_omega_ratio():
    # excess = [0.02, -0.01, 0.03, -0.02]
    # 正和 = 0.05, 负和绝对值 = 0.03, Omega = 1.6667
    assert calculate_omega_ratio([0.02, -0.01, 0.03, -0.02]) == pytest.approx(0.05 / 0.03)
    # 无跑输月份 -> inf
    assert math.isinf(calculate_omega_ratio([0.02, 0.03, 0.01]))
    # 全部跑输 -> 分子0, 分母>0 -> 0.0
    assert calculate_omega_ratio([-0.02, -0.01]) == 0.0
    # 空列表 -> 0.0
    assert calculate_omega_ratio([]) == 0.0


@pytest.mark.unit
def test_calculate_excess_win_rate():
    # [0.02, -0.01, 0.03, 0.0] -> 正数2个 -> 2/4 = 0.5（0.0 不算跑赢）
    assert calculate_excess_win_rate([0.02, -0.01, 0.03, 0.0]) == pytest.approx(0.5)
    assert calculate_excess_win_rate([0.02, 0.03]) == pytest.approx(1.0)
    assert calculate_excess_win_rate([]) == 0.0


@pytest.mark.unit
def test_calculate_max_consecutive_underperform():
    # excess = [0.02, -0.01, -0.03, 0.04, -0.01, -0.02, -0.01]
    # 连续 <= 0 的块：[-0.01,-0.03]长2, [-0.01,-0.02,-0.01]长3
    assert calculate_max_consecutive_underperform([0.02, -0.01, -0.03, 0.04, -0.01, -0.02, -0.01]) == 3
    # 全部跑赢 -> 0
    assert calculate_max_consecutive_underperform([0.02, 0.03]) == 0
    # 空列表 -> 0
    assert calculate_max_consecutive_underperform([]) == 0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v -k "omega or win_rate or underperform"`
Expected: FAIL（`ImportError: cannot import name 'calculate_omega_ratio'`）

- [ ] **Step 3: 追加实现到 app/calculations.py 末尾**

```python
def calculate_omega_ratio(excess_returns: list[float]) -> float:
    """Omega 比率（Target = RBA）：超额收益为正的累积面积 / 为负的累积绝对值面积。

    分母为0（无跑输月份）返回 inf；空列表返回 0.0。
    """
    if not excess_returns:
        return 0.0
    gains = sum(r for r in excess_returns if r > 0)
    losses = sum(-r for r in excess_returns if r < 0)
    if losses == 0.0:
        return float("inf")
    return gains / losses


def calculate_excess_win_rate(excess_returns: list[float]) -> float:
    """超额收益胜率：跑赢 RBA（excess > 0）的月数占比。"""
    n = len(excess_returns)
    if n == 0:
        return 0.0
    wins = sum(1 for r in excess_returns if r > 0)
    return wins / n


def calculate_max_consecutive_underperform(excess_returns: list[float]) -> int:
    """跑输 RBA 的最长连续月数（excess <= 0 视为跑输，含持平）。"""
    max_run = 0
    current = 0
    for r in excess_returns:
        if r <= 0:
            current += 1
            if current > max_run:
                max_run = current
        else:
            current = 0
    return max_run
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/calculations.py webapp/backend/tests/test_calculations.py
git commit -m "feat(backend): add Omega ratio, win rate, max consecutive underperform

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 自相关、Geltner 去平滑三重防火墙

**Files:**
- Modify: `webapp/backend/app/calculations.py`
- Modify: `webapp/backend/tests/test_calculations.py`

**Interfaces:**
- Produces: `calculate_autocorrelation(returns: list[float], fund_name: str = "Unknown") -> tuple[float, float]`（返回 `(phi, q_stat)`）、`unsmooth_returns(returns: list[float], phi: float, fund_name: str = "Unknown") -> list[float]`、`should_apply_geltner(n_months: int, phi: float, q_stat: float) -> bool`（三重防火墙判定）。

- [ ] **Step 1: 追加失败测试到 tests/test_calculations.py**

```python
from app.calculations import (
    calculate_autocorrelation,
    unsmooth_returns,
    should_apply_geltner,
)


@pytest.mark.unit
def test_calculate_autocorrelation():
    # 完全正自相关序列：phi 接近 1
    phi, q_stat = calculate_autocorrelation([0.01, 0.02, 0.03, 0.04, 0.05] * 10, "TestFund")
    assert phi > 0.9
    assert q_stat > 0
    # 短序列 -> (0.0, 0.0)
    assert calculate_autocorrelation([0.01], "TestFund") == (0.0, 0.0)


@pytest.mark.unit
def test_unsmooth_returns():
    assert unsmooth_returns([0.01, 0.015, 0.02], 0.5, "TestFund") == pytest.approx([0.01, 0.02, 0.025])
    with pytest.raises(ValueError) as excinfo:
        unsmooth_returns([0.01, 0.02], 0.999, "TestFund")
    assert "phi=0.999" in str(excinfo.value)
    assert unsmooth_returns([], 0.5, "TestFund") == []


@pytest.mark.unit
def test_should_apply_geltner_three_firewalls():
    # 全部通过：n>=36, Q>3.841, 0<=phi<=0.85
    assert should_apply_geltner(36, 0.5, 10.0) is True
    # 防火墙1：n < 36
    assert should_apply_geltner(35, 0.5, 10.0) is False
    # 防火墙2：Q <= 3.841
    assert should_apply_geltner(36, 0.5, 2.0) is False
    # 防火墙3：phi < 0
    assert should_apply_geltner(36, -0.1, 10.0) is False
    # 防火墙3：phi > 0.85
    assert should_apply_geltner(36, 0.9, 10.0) is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v -k "autocorrelation or unsmooth or geltner"`
Expected: FAIL（`ImportError`）

- [ ] **Step 3: 追加实现到 app/calculations.py 末尾**

```python
def calculate_autocorrelation(returns: list[float],
                              fund_name: str = "Unknown") -> tuple[float, float]:
    """一阶自相关系数 phi 与 Ljung-Box Q 统计量。"""
    n = len(returns)
    if n < 2:
        return 0.0, 0.0
    mean_r = sum(returns) / n
    numerator = sum((returns[t] - mean_r) * (returns[t - 1] - mean_r) for t in range(1, n))
    denominator = sum((r - mean_r) ** 2 for r in returns)
    if denominator == 0:
        return 0.0, 0.0
    phi = numerator / denominator
    q_stat = n * (n + 2) * (phi ** 2) / (n - 1)
    return phi, q_stat


def unsmooth_returns(returns: list[float], phi: float,
                     fund_name: str = "Unknown") -> list[float]:
    """Geltner 去平滑：r'_t = (r_t - phi * r_{t-1}) / (1 - phi)。"""
    if not returns:
        return []
    denom = 1.0 - phi
    if denom < 0.01:
        raise ValueError(f"[{fund_name}] phi={phi} 导致分母 1 - phi 为 {denom} (低于 0.01)，无法进行 Geltner 去平滑计算")
    unsmoothed = [returns[0]]
    for t in range(1, len(returns)):
        unsmoothed_val = (returns[t] - phi * returns[t - 1]) / denom
        unsmoothed.append(unsmoothed_val)
    return unsmoothed


# Ljung-Box 检验 95% 置信度临界值（自由度1）
LJUNG_BOX_CRITICAL_VALUE = 3.841


def should_apply_geltner(n_months: int, phi: float, q_stat: float) -> bool:
    """Geltner 去平滑三重防火墙判定。

    防火墙1：历史月数 >= 36
    防火墙2：Ljung-Box Q > 3.841（统计显著）
    防火墙3：0 <= phi <= 0.85（合理非负区间）
    三者全满足才返回 True。
    """
    if n_months < 36:
        return False
    if q_stat <= LJUNG_BOX_CRITICAL_VALUE:
        return False
    if phi < 0.0 or phi > 0.85:
        return False
    return True
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/calculations.py webapp/backend/tests/test_calculations.py
git commit -m "feat(backend): add autocorrelation, Geltner unsmoothing with triple firewall

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 异常检测（MAD）

**Files:**
- Create: `webapp/backend/app/anomaly.py`
- Create: `webapp/backend/tests/test_anomaly.py`

**Interfaces:**
- Consumes: 时序数据点 dict 列表，每个 dict 含 `date` (str), `net_return` (float), 可选 `commentary_truth` (float)。
- Produces: `detect_anomalies(time_series: list[dict], threshold_sigma: float = 3.0) -> list[dict]`，每个异常 dict 含 `date, value, z_score, threshold_sigma, mean, stdev, commentary_truth`。移植自 `scripts/anomaly_detection.py`。

- [ ] **Step 1: 写失败测试 tests/test_anomaly.py**

```python
"""MAD 异常检测测试。移植自 scripts/anomaly_detection.py 逻辑。"""
import pytest

from app.anomaly import detect_anomalies


@pytest.mark.unit
def test_detect_anomalies_finds_outlier():
    # 12个正常值(~0.005) + 1个极端值(0.5)
    ts = [{"date": f"2025-{m:02d}-28", "net_return": 0.005} for m in range(1, 13)]
    ts.append({"date": "2026-01-31", "net_return": 0.5})
    anomalies = detect_anomalies(ts, threshold_sigma=3.0)
    assert len(anomalies) == 1
    assert anomalies[0]["date"] == "2026-01-31"
    assert anomalies[0]["value"] == pytest.approx(0.5)
    assert anomalies[0]["z_score"] > 3.0


@pytest.mark.unit
def test_detect_anomalies_needs_min_12_months():
    # 不足12个月 -> 返回空
    ts = [{"date": f"2025-0{m}-28", "net_return": 0.005} for m in range(1, 7)]
    assert detect_anomalies(ts) == []


@pytest.mark.unit
def test_detect_anomalies_zero_returns_ignored():
    # net_return == 0 的点不参与统计也不报告
    ts = [{"date": f"2025-{m:02d}-28", "net_return": 0.005} for m in range(1, 13)]
    ts.append({"date": "2026-01-31", "net_return": 0.0})
    anomalies = detect_anomalies(ts)
    assert len(anomalies) == 0


@pytest.mark.unit
def test_detect_anomalies_preserves_commentary_truth():
    ts = [{"date": f"2025-{m:02d}-28", "net_return": 0.005} for m in range(1, 13)]
    ts.append({"date": "2026-01-31", "net_return": 0.5, "commentary_truth": 0.005})
    anomalies = detect_anomalies(ts)
    assert anomalies[0]["commentary_truth"] == pytest.approx(0.005)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python -m pytest tests/test_anomaly.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 app/anomaly.py**

```python
"""MAD（中位数绝对偏差）异常检测。移植自 scripts/anomaly_detection.py。

使用稳健统计量（中位数 + MAD）代替均值+标准差，免疫极端值对统计量的污染。
"""
import statistics


def detect_anomalies(time_series: list[dict], threshold_sigma: float = 3.0) -> list[dict]:
    """检测月度收益率时序中的异常点。

    Args:
        time_series: 数据点列表，每个含 date, net_return, 可选 commentary_truth。
        threshold_sigma: 判定门禁（MAD 分数绝对值 >= 此值视为异常），默认 3.0。

    Returns:
        异常点 dict 列表，每个含 date, value, z_score, threshold_sigma, mean, stdev, commentary_truth。
    """
    returns = [dp["net_return"] for dp in time_series
               if "net_return" in dp and dp["net_return"] != 0.0]
    if len(returns) < 12:
        return []  # 至少需要一年数据才有统计意义

    median = statistics.median(returns)
    abs_deviations = [abs(x - median) for x in returns]
    mad = statistics.median(abs_deviations)

    # 由 MAD 估计标准差：std = MAD * 1.4826。MAD 为 0 时用极小值避免除零
    robust_stdev = mad * 1.4826 if mad != 0 else 1e-6

    anomalies = []
    for dp in time_series:
        ret = dp.get("net_return")
        if ret is None or ret == 0.0:
            continue
        mad_score = (ret - median) / robust_stdev
        if abs(mad_score) >= threshold_sigma:
            anomalies.append({
                "date": dp["date"],
                "value": ret,
                "z_score": mad_score,
                "threshold_sigma": threshold_sigma,
                "mean": median,  # 稳健均值用中位数
                "stdev": robust_stdev,
                "commentary_truth": dp.get("commentary_truth", None),
            })
    return anomalies
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python -m pytest tests/test_anomaly.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/anomaly.py webapp/backend/tests/test_anomaly.py
git commit -m "feat(backend): add MAD anomaly detection

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 整合计算引擎 compute_all_metrics

**Files:**
- Modify: `webapp/backend/app/calculations.py`（追加整合函数）
- Modify: `webapp/backend/tests/test_calculations.py`（追加整合测试）

**Interfaces:**
- Produces: `compute_all_metrics(returns: list[float], rf_rates: list[float], fund_name: str = "Unknown") -> dict`。输入月度原始收益率序列与对应逐月 RBA 年化利率序列（等长），输出包含全部5维 orig/un 指标的 dict，键名与 `FundMetric` 模型字段一致（不含 `fund_id`/`updated_at`）。

- [ ] **Step 1: 追加失败测试到 tests/test_calculations.py**

```python
from app.calculations import compute_all_metrics


@pytest.mark.unit
def test_compute_all_metrics_short_history():
    """不足36个月：跳过去平滑，un 指标 == orig 指标，is_geltner_applied=0。"""
    returns = [0.005, 0.006, 0.004, 0.007, 0.005, 0.006]
    rf_rates = [0.0435] * 6  # 6个月，恒定 RBA 4.35%
    m = compute_all_metrics(returns, rf_rates, "ShortFund")
    assert m["history_months"] == 6
    assert m["is_short_history_warning"] == 1
    assert m["is_geltner_applied"] == 0
    # un 指标应等于 orig 指标
    assert m["un_annualized_excess_return"] == pytest.approx(m["orig_annualized_excess_return"])
    assert m["un_omega_ratio"] == pytest.approx(m["orig_omega_ratio"])
    assert m["un_max_drawdown"] == pytest.approx(m["orig_max_drawdown"])


@pytest.mark.unit
def test_compute_all_metrics_excess_return_formula():
    """验证超额收益采用逐月扣减复利法（spec 4.1）。"""
    # 单月：r=0.01, RBA=0.0435 -> r_e = 0.01 - 0.0435/12 = 0.01 - 0.003625 = 0.006375
    # 年化(1月)：(1.006375)^12 - 1
    returns = [0.01]
    rf_rates = [0.0435]
    m = compute_all_metrics(returns, rf_rates, "TestFund")
    expected = (1.006375) ** 12 - 1
    assert m["orig_annualized_excess_return"] == pytest.approx(expected, rel=1e-6)


@pytest.mark.unit
def test_compute_all_metrics_win_rate_and_underperform():
    """验证胜率与最长连续跑输。"""
    # 4个月，全部跑赢RBA
    returns = [0.02, 0.03, 0.025, 0.015]
    rf_rates = [0.0435] * 4
    m = compute_all_metrics(returns, rf_rates, "TestFund")
    assert m["orig_excess_win_rate"] == pytest.approx(1.0)
    assert m["orig_max_underperform_months"] == 0
    # Omega 应为 inf（无跑输月）
    assert math.isinf(m["orig_omega_ratio"])


@pytest.mark.unit
def test_compute_all_metrics_with_geltner():
    """足够长且自相关显著的序列应触发去平滑。"""
    # 构造60个月强自相关序列
    returns = []
    val = 0.005
    for i in range(60):
        returns.append(val)
        val = val * 0.9 + 0.005 * 0.1  # 平滑漂移，产生强自相关
    rf_rates = [0.0435] * 60
    m = compute_all_metrics(returns, rf_rates, "SmoothFund")
    assert m["history_months"] == 60
    assert m["is_short_history_warning"] == 0
    assert m["is_q_significant"] in (0, 1)
    # 去平滑后波动率应 >= 原始波动率（去平滑放大真实波动）
    if m["is_geltner_applied"] == 1:
        assert m["un_annualized_volatility"] >= m["orig_annualized_volatility"]
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v -k "compute_all"`
Expected: FAIL（`ImportError: cannot import name 'compute_all_metrics'`）

- [ ] **Step 3: 追加实现到 app/calculations.py 末尾**

```python
def _build_nav_series(returns: list[float]) -> list[float]:
    """由月度收益率构建累计复利 NAV 序列（起点 1.0）。"""
    nav = 1.0
    nav_series = [nav]
    for r in returns:
        nav = nav * (1.0 + r)
        nav_series.append(nav)
    return nav_series


def _excess_returns(returns: list[float], rf_rates: list[float]) -> list[float]:
    """逐月超额收益：r_e,t = r_t - RBA_t / 12。"""
    return [r - (rf / 12.0) for r, rf in zip(returns, rf_rates)]


def _annualized_excess_return_compounded(excess: list[float], n_months: int,
                                         fund_name: str) -> float:
    """spec 4.1 权威算法：逐月扣减后复利年化 (∏(1+r_e))^(12/n) - 1。"""
    if n_months <= 0:
        return 0.0
    comp = 1.0
    for r in excess:
        comp *= (1.0 + r)
    return calculate_annualized_return(comp, n_months, fund_name=fund_name)


def compute_all_metrics(returns: list[float], rf_rates: list[float],
                        fund_name: str = "Unknown") -> dict:
    """计算全部5维 orig/un 指标。

    Args:
        returns: 月度原始收益率序列。
        rf_rates: 对应逐月 RBA 年化利率序列（与 returns 等长）。
        fund_name: 基金名称（用于错误信息）。

    Returns:
        dict，键名与 FundMetric 模型字段一致（不含 fund_id/updated_at）。
    """
    n = len(returns)
    is_short = n < 36

    # 自相关与去平滑判定
    phi, q_stat = calculate_autocorrelation(returns, fund_name=fund_name) if n >= 2 else (0.0, 0.0)
    is_q_sig = q_stat > LJUNG_BOX_CRITICAL_VALUE
    is_geltner = should_apply_geltner(n, phi, q_stat) if not is_short else False
    unsmoothed = unsmooth_returns(returns, phi, fund_name=fund_name) if is_geltner else list(returns)

    # 超额收益序列
    excess_orig = _excess_returns(returns, rf_rates)
    excess_un = _excess_returns(unsmoothed, rf_rates)

    # 维度1：进攻（复利年化超额收益）
    orig_ann_excess = _annualized_excess_return_compounded(excess_orig, n, fund_name)
    un_ann_excess = _annualized_excess_return_compounded(excess_un, n, fund_name)

    # 维度2：防守（最大回撤，基于累计NAV）
    orig_max_dd = calculate_max_drawdown(_build_nav_series(returns), fund_name=fund_name)
    un_max_dd = calculate_max_drawdown(_build_nav_series(unsmoothed), fund_name=fund_name)

    # 维度3：性价比（Omega 比率）
    orig_omega = calculate_omega_ratio(excess_orig)
    un_omega = calculate_omega_ratio(excess_un)

    # 维度4：体感与煎熬度
    orig_win_rate = calculate_excess_win_rate(excess_orig)
    un_win_rate = calculate_excess_win_rate(excess_un)
    orig_max_under = calculate_max_consecutive_underperform(excess_orig)
    un_max_under = calculate_max_consecutive_underperform(excess_un)

    # 维度5：真实性辅助（波动率）
    orig_vol = calculate_annualized_volatility(returns, fund_name=fund_name)
    un_vol = calculate_annualized_volatility(unsmoothed, fund_name=fund_name)

    return {
        "history_months": n,
        "is_short_history_warning": 1 if is_short else 0,
        "unsmoothing_coefficient_phi": phi,
        "is_geltner_applied": 1 if is_geltner else 0,
        "orig_annualized_excess_return": orig_ann_excess,
        "un_annualized_excess_return": un_ann_excess,
        "orig_max_drawdown": orig_max_dd,
        "un_max_drawdown": un_max_dd,
        "orig_omega_ratio": orig_omega,
        "un_omega_ratio": un_omega,
        "orig_excess_win_rate": orig_win_rate,
        "un_excess_win_rate": un_win_rate,
        "orig_max_underperform_months": orig_max_under,
        "un_max_underperform_months": un_max_under,
        "orig_annualized_volatility": orig_vol,
        "un_annualized_volatility": un_vol,
        "ljung_box_q": q_stat,
        "is_q_significant": 1 if is_q_sig else 0,
    }
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python -m pytest tests/test_calculations.py -v`
Expected: 13 passed

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/calculations.py webapp/backend/tests/test_calculations.py
git commit -m "feat(backend): add compute_all_metrics integration engine for 5-dim indicators

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: CRUD 操作与 NAV 重计算

**Files:**
- Create: `webapp/backend/app/crud.py`
- Create: `webapp/backend/tests/test_crud.py`

**Interfaces:**
- Consumes: `db_session`（来自 conftest）、`app.models` 的 ORM 类。
- Produces: `create_fund(session, **kwargs) -> Fund`、`get_fund(session, fund_id) -> Fund | None`、`get_all_funds(session) -> list[Fund]`、`delete_fund(session, fund_id) -> bool`、`upsert_monthly_return(session, fund_id, date, net_return, commentary_truth=None) -> MonthlyReturn`、`get_returns(session, fund_id) -> list[dict]`（按日期升序，含 date/net_return/commentary_truth）、`recompute_nav(session, fund_id) -> None`（重新计算该基金全部 NAV，以 1.0 为起点复利）、`resolve_rf_rates(session, dates, fallback_rate) -> list[float]`（按月份从 rba_cash_rates 表查利率，缺失用 fallback）。

- [ ] **Step 1: 写失败测试 tests/test_crud.py**

```python
"""CRUD 与 NAV 重计算测试。"""
import pytest

from app.models import Fund, MonthlyReturn, RbaCashRate
from app.crud import (
    create_fund, get_fund, get_all_funds, delete_fund,
    upsert_monthly_return, get_returns, recompute_nav, resolve_rf_rates,
)


@pytest.mark.unit
def test_create_and_get_fund(db_session):
    fund = create_fund(db_session, fund_id="f1", fund_name="Fund One",
                       confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    assert fund.fund_id == "f1"
    assert get_fund(db_session, "f1").fund_name == "Fund One"
    assert get_fund(db_session, "nonexistent") is None


@pytest.mark.unit
def test_get_all_funds(db_session):
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    create_fund(db_session, fund_id="f2", fund_name="Fund Two",
                confirmed_url="http://y", fetch_method="pdf", url_type="pdf")
    assert len(get_all_funds(db_session)) == 2


@pytest.mark.unit
def test_delete_fund_cascades(db_session):
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "f1", "2026-05-31", 0.01)
    assert delete_fund(db_session, "f1") is True
    assert get_fund(db_session, "f1") is None
    assert len(get_returns(db_session, "f1")) == 0
    assert delete_fund(db_session, "nonexistent") is False


@pytest.mark.unit
def test_upsert_monthly_return_recompute_nav(db_session):
    """upsert 后 NAV 自动重算：3个月收益 [0.01, 0.02, 0.03] -> NAV [1.01, 1.0302, 1.061306]。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "f1", "2026-03-31", 0.01)
    upsert_monthly_return(db_session, "f1", "2026-04-30", 0.02)
    upsert_monthly_return(db_session, "f1", "2026-05-31", 0.03)

    returns = get_returns(db_session, "f1")
    assert len(returns) == 3
    assert returns[0]["date"] == "2026-03-31"
    # NAV 在数据库中应已重算
    rows = db_session.query(MonthlyReturn).order_by(MonthlyReturn.date).all()
    assert rows[0].nav == pytest.approx(1.01)
    assert rows[1].nav == pytest.approx(1.01 * 1.02)
    assert rows[2].nav == pytest.approx(1.01 * 1.02 * 1.03)


@pytest.mark.unit
def test_recompute_nav_after_mid_insertion(db_session):
    """中途插入历史月份后重算 NAV 应正确级联。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "f1", "2026-05-31", 0.03)
    upsert_monthly_return(db_session, "f1", "2026-03-31", 0.01)
    upsert_monthly_return(db_session, "f1", "2026-04-30", 0.02)

    rows = db_session.query(MonthlyReturn).order_by(MonthlyReturn.date).all()
    assert rows[0].nav == pytest.approx(1.01)
    assert rows[1].nav == pytest.approx(1.01 * 1.02)
    assert rows[2].nav == pytest.approx(1.01 * 1.02 * 1.03)


@pytest.mark.unit
def test_resolve_rf_rates(db_session):
    """按月份查 RBA 利率，缺失用 fallback。"""
    db_session.add(RbaCashRate(date_period="2026-03", rate=0.0435))
    db_session.add(RbaCashRate(date_period="2026-04", rate=0.0410))
    db_session.commit()

    dates = ["2026-03-31", "2026-04-30", "2026-05-31"]  # 5月缺失
    rates = resolve_rf_rates(db_session, dates, fallback_rate=0.0425)
    assert rates[0] == pytest.approx(0.0435)
    assert rates[1] == pytest.approx(0.0410)
    assert rates[2] == pytest.approx(0.0425)  # fallback
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python -m pytest tests/test_crud.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.crud'`）

- [ ] **Step 3: 实现 app/crud.py**

```python
"""数据库 CRUD 操作与 NAV 重计算。"""
from sqlalchemy.orm import Session

from app.models import Fund, MonthlyReturn, RbaCashRate, Anomaly, FundMetric


def create_fund(session: Session, **kwargs) -> Fund:
    fund = Fund(**kwargs)
    session.add(fund)
    session.commit()
    session.refresh(fund)
    return fund


def get_fund(session: Session, fund_id: str) -> Fund | None:
    return session.get(Fund, fund_id)


def get_all_funds(session: Session) -> list[Fund]:
    return session.query(Fund).order_by(Fund.fund_name).all()


def delete_fund(session: Session, fund_id: str) -> bool:
    fund = session.get(Fund, fund_id)
    if fund is None:
        return False
    session.delete(fund)  # 级联删除子表
    session.commit()
    return True


def upsert_monthly_return(session: Session, fund_id: str, date: str,
                          net_return: float, commentary_truth: float | None = None) -> MonthlyReturn:
    """插入或更新某月收益，随后重算该基金全部 NAV。"""
    existing = session.query(MonthlyReturn).filter_by(fund_id=fund_id, date=date).first()
    if existing:
        existing.net_return = net_return
        if commentary_truth is not None:
            existing.commentary_truth = commentary_truth
        row = existing
    else:
        row = MonthlyReturn(fund_id=fund_id, date=date, net_return=net_return,
                            nav=1.0, commentary_truth=commentary_truth)
        session.add(row)
    session.commit()
    recompute_nav(session, fund_id)
    session.refresh(row)
    return row


def get_returns(session: Session, fund_id: str) -> list[dict]:
    """按日期升序返回该基金的月度收益（date, net_return, commentary_truth）。"""
    rows = session.query(MonthlyReturn).filter_by(fund_id=fund_id).order_by(MonthlyReturn.date).all()
    return [{"date": r.date, "net_return": r.net_return, "commentary_truth": r.commentary_truth}
            for r in rows]


def recompute_nav(session: Session, fund_id: str) -> None:
    """重新计算该基金全部累计 NAV（以 1.0 为起点复利）。

    在插入/更新任意月度收益后调用，确保 NAV 序列始终连续正确。
    """
    rows = session.query(MonthlyReturn).filter_by(fund_id=fund_id).order_by(MonthlyReturn.date).all()
    nav = 1.0
    for r in rows:
        nav = nav * (1.0 + r.net_return)
        r.nav = nav
    session.commit()


def resolve_rf_rates(session: Session, dates: list[str], fallback_rate: float) -> list[float]:
    """按月份从 rba_cash_rates 表查年化利率，缺失月份用 fallback。"""
    rates = []
    for d in dates:
        month_key = d[:7]  # YYYY-MM
        rba = session.get(RbaCashRate, month_key)
        rates.append(rba.rate if rba else fallback_rate)
    return rates


def replace_anomalies(session: Session, fund_id: str, anomalies: list[dict]) -> None:
    """清空并重写某基金的异常记录。

    显式映射字段（忽略 detect_anomalies 返回的 commentary_truth，
    因为 Anomaly 表不存储此字段--它属于 monthly_returns 表）。
    """
    session.query(Anomaly).filter_by(fund_id=fund_id).delete()
    for a in anomalies:
        session.add(Anomaly(
            fund_id=fund_id,
            date=a["date"],
            value=a["value"],
            z_score=a["z_score"],
            threshold_sigma=a["threshold_sigma"],
            mean=a["mean"],
            stdev=a["stdev"],
        ))
    session.commit()


def upsert_metrics(session: Session, fund_id: str, metrics: dict) -> None:
    """插入或更新某基金的5维指标记录。"""
    existing = session.get(FundMetric, fund_id)
    if existing:
        for key, val in metrics.items():
            setattr(existing, key, val)
    else:
        session.add(FundMetric(fund_id=fund_id, **metrics))
    session.commit()
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python -m pytest tests/test_crud.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/crud.py webapp/backend/tests/test_crud.py
git commit -m "feat(backend): add CRUD operations and NAV recompute

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: RBA 利率抓取与入库

**Files:**
- Create: `webapp/backend/app/rba.py`
- Create: `webapp/backend/tests/test_rba.py`

**Interfaces:**
- Consumes: `app.config.settings`（RBA URL）、`app.crud`（入库暂不在此，仅返回数据）。
- Produces: `fetch_current_rba_rate() -> float`（从 rba.gov.au 首页抓当前现金利率）、`fetch_historical_rba_rates() -> dict[str, float]`（从 DBnomics API 抓历史逐月利率，键 YYYY-MM）、`upsert_rba_rates(session, rates: dict[str, float]) -> int`（写入 rba_cash_rates 表，返回写入条数）。

- [ ] **Step 1: 写失败测试 tests/test_rba.py**

```python
"""RBA 利率抓取测试。网络请求用 monkeypatch mock。"""
import pytest
from unittest.mock import patch, MagicMock

from app.rba import fetch_current_rba_rate, fetch_historical_rba_rates, upsert_rba_rates
from app.models import RbaCashRate


@pytest.mark.unit
def test_fetch_current_rba_rate_parses_html():
    """从 RBA 首页 HTML 解析现金利率。"""
    fake_html = '''
    <html><body><article>
      <span>Cash rate target</span>
      <p class="statistic-value">4.35%</p>
    </article></body></html>
    '''
    mock_resp = MagicMock()
    mock_resp.text = fake_html
    mock_resp.raise_for_status = MagicMock()
    with patch("app.rba.requests.get", return_value=mock_resp):
        rate = fetch_current_rba_rate()
    assert rate == pytest.approx(0.0435)


@pytest.mark.unit
def test_fetch_historical_rba_rates_parses_dbnomics():
    """从 DBnomics API 响应解析历史利率。"""
    fake_json = {
        "series": {"docs": [{
            "period": ["2026-03", "2026-04", "2026-05"],
            "value": ["4.35", "4.10", "4.10"]
        }]}
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_json
    mock_resp.raise_for_status = MagicMock()
    with patch("app.rba.requests.get", return_value=mock_resp):
        rates = fetch_historical_rba_rates()
    assert rates["2026-03"] == pytest.approx(0.0435)
    assert rates["2026-04"] == pytest.approx(0.0410)
    assert rates["2026-05"] == pytest.approx(0.0410)


@pytest.mark.unit
def test_upsert_rba_rates(db_session):
    """写入利率表，重复 date_period 覆盖而非报错。"""
    rates = {"2026-03": 0.0435, "2026-04": 0.0410}
    count = upsert_rba_rates(db_session, rates)
    assert count == 2
    assert db_session.get(RbaCashRate, "2026-03").rate == pytest.approx(0.0435)

    # 覆盖更新
    count = upsert_rba_rates(db_session, {"2026-03": 0.0400})
    assert db_session.get(RbaCashRate, "2026-03").rate == pytest.approx(0.0400)
    assert db_session.query(RbaCashRate).count() == 2  # 未新增
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python -m pytest tests/test_rba.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.rba'`）

- [ ] **Step 3: 实现 app/rba.py**

```python
"""RBA 官方现金利率抓取与入库。移植自 scripts/metrics.py 的 fetch_rba_cash_rate / fetch_historical_cash_rates。"""
import re
import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RbaCashRate


def fetch_current_rba_rate() -> float:
    """从 RBA 首页抓取当前官方现金利率（年化小数，如 0.0435）。"""
    resp = requests.get(settings.RBA_BASE_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    h = soup.find(string=lambda x: x and "Cash rate target" in x)
    if not h:
        raise ValueError("RBA 页面未找到 'Cash rate target' 文本")
    parent = h.find_parent("article") or h.find_parent("div")
    if not parent:
        raise ValueError("未找到 'Cash rate target' 的父容器")
    val_el = parent.find(class_="statistic-value")
    if not val_el:
        raise ValueError("未找到 class='statistic-value' 元素")

    match = re.search(r"[0-9.]+", val_el.text.strip())
    if not match:
        raise ValueError(f"无法从文本解析利率数值: '{val_el.text}'")
    return float(match.group(0)) / 100.0


def fetch_historical_rba_rates() -> dict[str, float]:
    """从 DBnomics API 抓取历史逐月现金利率，返回 {YYYY-MM: 年化小数}。"""
    resp = requests.get(settings.RBA_HISTORY_API, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    doc = data["series"]["docs"][0]

    rates = {}
    for period, val in zip(doc["period"], doc["value"]):
        if val == "NA" or val is None:
            continue
        try:
            rates[period[:7]] = float(val) / 100.0  # YYYY-MM -> 年化小数
        except ValueError:
            continue
    return rates


def upsert_rba_rates(session: Session, rates: dict[str, float]) -> int:
    """将利率字典写入 rba_cash_rates 表（重复主键覆盖），返回写入条数。"""
    count = 0
    for month_key, rate in rates.items():
        existing = session.get(RbaCashRate, month_key)
        if existing:
            existing.rate = rate
        else:
            session.add(RbaCashRate(date_period=month_key, rate=rate))
            count += 1
    session.commit()
    return count
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python -m pytest tests/test_rba.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add webapp/backend/app/rba.py webapp/backend/tests/test_rba.py
git commit -m "feat(backend): add RBA cash rate fetcher and upsert

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: 指标计算编排管道 metrics_pipeline

**Files:**
- Create: `webapp/backend/app/metrics_pipeline.py`
- Create: `webapp/backend/tests/test_metrics_pipeline.py`

**Interfaces:**
- Consumes: `app.crud`（get_returns, resolve_rf_rates, replace_anomalies, upsert_metrics）、`app.calculations.compute_all_metrics`、`app.anomaly.detect_anomalies`、`app.rba.fetch_current_rba_rate`（fallback 用）。
- Produces: `compute_and_store_metrics(session, fund_id: str, fallback_rba_rate: float | None = None) -> dict`。从数据库读取该基金月度收益与对应 RBA 利率，计算5维指标与异常，写回 `fund_metrics` 与 `anomalies` 表，返回指标 dict。

- [ ] **Step 1: 写失败测试 tests/test_metrics_pipeline.py**

```python
"""指标编排管道端到端测试。"""
import pytest

from app.models import Fund, FundMetric, Anomaly, RbaCashRate
from app.crud import create_fund, upsert_monthly_return
from app.metrics_pipeline import compute_and_store_metrics


@pytest.mark.unit
def test_compute_and_store_metrics_short_history(db_session):
    """不足36个月的基金：写入 fund_metrics，is_short_history_warning=1。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    for i, r in enumerate([0.005, 0.006, 0.004, 0.007, 0.005, 0.006]):
        upsert_monthly_return(db_session, "f1", f"2025-{i+1:02d}-28", r)

    db_session.add(RbaCashRate(date_period="2025-01", rate=0.0435))
    db_session.commit()

    metrics = compute_and_store_metrics(db_session, "f1", fallback_rba_rate=0.0435)
    assert metrics["history_months"] == 6
    assert metrics["is_short_history_warning"] == 1

    # 验证已写入数据库
    stored = db_session.get(FundMetric, "f1")
    assert stored is not None
    assert stored.is_short_history_warning == 1
    assert stored.orig_annualized_volatility == pytest.approx(metrics["orig_annualized_volatility"])


@pytest.mark.unit
def test_compute_and_store_metrics_anomalies_persisted(db_session):
    """异常值检测后写入 anomalies 表。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    # 12个正常值 + 1个极端值
    for m in range(1, 13):
        upsert_monthly_return(db_session, "f1", f"2025-{m:02d}-28", 0.005)
    upsert_monthly_return(db_session, "f1", "2026-01-31", 0.5)

    compute_and_store_metrics(db_session, "f1", fallback_rba_rate=0.0435)

    anomalies = db_session.query(Anomaly).filter_by(fund_id="f1").all()
    assert len(anomalies) == 1
    assert anomalies[0].date == "2026-01-31"
    assert anomalies[0].value == pytest.approx(0.5)


@pytest.mark.unit
def test_compute_and_store_metrics_uses_db_rba_over_fallback(db_session):
    """数据库中有 RBA 利率时优先使用，而非 fallback。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "f1", "2026-01-31", 0.01)
    db_session.add(RbaCashRate(date_period="2026-01", rate=0.0435))
    db_session.commit()

    metrics = compute_and_store_metrics(db_session, "f1", fallback_rba_rate=0.0999)
    # 应使用 DB 中的 0.0435 而非 fallback 0.0999
    excess = 0.01 - 0.0435 / 12.0
    expected_ann = (1.0 + excess) ** 12 - 1
    assert metrics["orig_annualized_excess_return"] == pytest.approx(expected_ann, rel=1e-6)


@pytest.mark.unit
def test_compute_and_store_metrics_idempotent(db_session):
    """重复调用不产生重复记录（upsert）。"""
    create_fund(db_session, fund_id="f1", fund_name="Fund One",
                confirmed_url="http://x", fetch_method="pdf", url_type="pdf")
    upsert_monthly_return(db_session, "f1", "2026-01-31", 0.01)

    compute_and_store_metrics(db_session, "f1", fallback_rba_rate=0.0435)
    compute_and_store_metrics(db_session, "f1", fallback_rba_rate=0.0435)

    assert db_session.query(FundMetric).filter_by(fund_id="f1").count() == 1
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd webapp/backend && python -m pytest tests/test_metrics_pipeline.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.metrics_pipeline'`）

- [ ] **Step 3: 实现 app/metrics_pipeline.py**

```python
"""指标计算编排管道：从数据库读取月度收益 -> 计算5维指标 + 检测异常 -> 写回数据库。"""
from sqlalchemy.orm import Session

from app.crud import get_returns, resolve_rf_rates, replace_anomalies, upsert_metrics
from app.calculations import compute_all_metrics
from app.anomaly import detect_anomalies


def compute_and_store_metrics(session: Session, fund_id: str,
                              fallback_rba_rate: float | None = None) -> dict:
    """计算并持久化某基金的5维指标与异常。

    Args:
        session: 数据库会话。
        fund_id: 基金ID。
        fallback_rba_rate: 数据库中缺失 RBA 利率时的回退值（通常为当前抓取的利率）。

    Returns:
        计算出的指标 dict（已写入 fund_metrics 表）。
    """
    time_series = get_returns(session, fund_id)
    if not time_series:
        raise ValueError(f"基金 {fund_id} 无月度收益数据，无法计算指标")

    returns = [dp["net_return"] for dp in time_series]
    dates = [dp["date"] for dp in time_series]

    if fallback_rba_rate is None:
        fallback_rba_rate = 0.0435  # 安全回退，正常流程应传入抓取值

    rf_rates = resolve_rf_rates(session, dates, fallback_rate=fallback_rba_rate)

    # 计算5维指标
    metrics = compute_all_metrics(returns, rf_rates, fund_name=fund_id)

    # 记录数据截止月份（最近月份）
    metrics["date_period"] = dates[-1][:7]

    # 检测异常并写入
    anomalies = detect_anomalies(time_series, threshold_sigma=3.0)
    replace_anomalies(session, fund_id, anomalies)

    # 写入指标
    upsert_metrics(session, fund_id, metrics)

    return metrics
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd webapp/backend && python -m pytest tests/test_metrics_pipeline.py -v`
Expected: 4 passed

- [ ] **Step 5: 运行全部测试确认无回归**

Run: `cd webapp/backend && python -m pytest tests/ -v`
Expected: 全部 passed（约 40+ 用例）

- [ ] **Step 6: 提交**

```bash
git add webapp/backend/app/metrics_pipeline.py webapp/backend/tests/test_metrics_pipeline.py
git commit -m "feat(backend): add metrics pipeline orchestrator

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 阶段 1 完成标准

全部以下条件满足：
1. `cd webapp/backend && python -m pytest tests/ -v` 全绿（约 40+ 用例）。
2. `webapp/backend/app/` 下有 8 个模块：`config, database, models, calculations, anomaly, rba, crud, metrics_pipeline`。
3. 5维计算引擎可独立运行：给定 `returns` 与 `rf_rates`，`compute_all_metrics` 返回完整 orig/un 指标 dict。
4. 数据库 6 张表可通过 `init_db()` 幂等创建，级联删除与唯一约束生效。
5. RBA 抓取可 mock 测试，`upsert_rba_rates` 支持覆盖更新。

## 后续阶段预告（不在本计划内）

- **阶段 2**：FastAPI 路由层（`/api/funds`, `/api/metrics/compare`, `/api/reports/ai-summary`）、RBA 定时调度、LLM 中转站集成。
- **阶段 3**：自定义技能重构（`add_fixed_fund`, `update_fixed_fund`），复用阶段1的 crud + rba。
- **阶段 4**：React 前端（Vite + Tailwind + Recharts）。
- **阶段 5**：JSON -> SQLite 数据迁移与旧代码清理。
