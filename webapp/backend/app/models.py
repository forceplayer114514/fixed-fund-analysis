"""数据库 ORM 模型：6张表，字段与 spec 第3节 schema 一致。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import String, Float, Integer, Text, ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Fund(Base):
    __tablename__ = "funds"

    fund_id: Mapped[str] = mapped_column(String, primary_key=True)
    fund_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    apir_code: Mapped[Optional[str]] = mapped_column(String, unique=True, nullable=True)
    confirmed_url: Mapped[str] = mapped_column(Text, nullable=False)
    fetch_method: Mapped[str] = mapped_column(String, nullable=False)
    url_type: Mapped[str] = mapped_column(String, nullable=False)
    max_pdf_pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    verified_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String, server_default=text("(datetime('now'))"))

    monthly_returns: Mapped[list["MonthlyReturn"]] = relationship(
        back_populates="fund", cascade="all, delete-orphan")
    anomalies: Mapped[list["Anomaly"]] = relationship(
        back_populates="fund", cascade="all, delete-orphan")
    metrics: Mapped[Optional["FundMetric"]] = relationship(
        back_populates="fund", cascade="all, delete-orphan", uselist=False)


class MonthlyReturn(Base):
    __tablename__ = "monthly_returns"
    __table_args__ = (UniqueConstraint("fund_id", "date", name="uq_fund_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_id: Mapped[str] = mapped_column(ForeignKey("funds.fund_id", ondelete="CASCADE"), nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)  # YYYY-MM-DD
    net_return: Mapped[float] = mapped_column(Float, nullable=False)
    nav: Mapped[float] = mapped_column(Float, nullable=False)
    commentary_truth: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

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
    updated_at: Mapped[Optional[str]] = mapped_column(
        String, server_default=text("(datetime('now'))"), onupdate=text("(datetime('now'))")
    )


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
    updated_at: Mapped[Optional[str]] = mapped_column(
        String, server_default=text("(datetime('now'))"), onupdate=text("(datetime('now'))")
    )

    fund: Mapped["Fund"] = relationship(back_populates="metrics")


class AiReport(Base):
    __tablename__ = "ai_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_ids: Mapped[str] = mapped_column(Text, nullable=False)
    date_period: Mapped[str] = mapped_column(String, nullable=False)
    report_type: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Optional[str]] = mapped_column(String, server_default=text("(datetime('now'))"))
