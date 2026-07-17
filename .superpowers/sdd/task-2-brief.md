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

