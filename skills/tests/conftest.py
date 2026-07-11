"""pytest fixtures for skills DB tests.

所有测试使用 tmp_path 创建的临时 SQLite 文件,绝不触碰真实 DB
(data/fund_analysis.db)。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保 skills 目录在 sys.path 中,以便 `from lib.db import ...` 在
# 任意 cwd 下运行 `python3 -m pytest` 都能成功导入。
_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

import pytest

from lib.db import ensure_tables, get_connection


@pytest.fixture
def db_conn(tmp_path):
    """返回一个已建表的临时 SQLite 连接,测试结束后关闭。

    使用 tmp_path 隔离,绝不污染 data/fund_analysis.db。连接经 get_connection
    创建,因此 PRAGMA foreign_keys=ON 与 row_factory=sqlite3.Row 均已启用。
    """
    db_path = tmp_path / "test_fund.db"
    conn = get_connection(str(db_path))
    ensure_tables(conn)
    yield conn
    conn.close()
