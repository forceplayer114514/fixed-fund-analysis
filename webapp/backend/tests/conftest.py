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


@pytest.fixture
def client(db_session):
    """TestClient，依赖注入指向内存 DB。

    不用 `with TestClient(app)` 以避免触发 lifespan（lifespan 的 init_db 会用默认
    文件 DB 建表污染仓库，scheduler 也会启动）。API 测试只需 db_session + get_db
    覆盖即可；scheduler 等依赖 lifespan 的功能在 test_scheduler.py 单独测。
    """
    from app.main import create_app
    from app.database import get_db
    from fastapi.testclient import TestClient

    app = create_app(enable_scheduler=False)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()
