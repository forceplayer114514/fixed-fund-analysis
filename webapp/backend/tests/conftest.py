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
