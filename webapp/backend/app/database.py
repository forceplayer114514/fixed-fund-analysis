"""SQLAlchemy 引擎、会话工厂与建表。"""
from sqlalchemy import create_engine, event
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

if settings.DATABASE_URL.startswith("sqlite"):
    # SQLite 默认不启用外键约束 -- models.py 里 confirmed_gaps/pending_review
    # 的 ForeignKey(..., ondelete="CASCADE") 声明了但从未生效, 删 fund 时这两张
    # 表的行会变孤儿 (monthly_returns/anomalies/metrics 有 ORM 级 cascade 不受影响,
    # 但这两张只在 DB 层声明, 没有对应 ORM relationship)。每条连接开启后补 PRAGMA。
    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

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
