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
