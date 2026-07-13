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


@pytest.fixture(autouse=True)
def _write_token(monkeypatch):
    """所有测试默认带写凭证（避免每个测试显式 setenv）。"""
    monkeypatch.setenv("FUND_DB_WRITE_TOKEN", "test")


@pytest.fixture(autouse=True)
def _audit_dir(tmp_path, monkeypatch):
    """所有测试隔离审计报告输出目录到 tmp_path，避免污染仓库 docs/。

    audit_all_funds 末尾 _write_report 读 FUND_AUDIT_DIR；未设则写仓库默认
    docs/superpowers/audits/。autouse 统一重定向到 tmp_path。需断言写入的
    测试（test_audit_writes_report_file）仍可在测试体内 setenv 覆盖（同值）。
    """
    monkeypatch.setenv("FUND_AUDIT_DIR", str(tmp_path))
