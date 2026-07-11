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
