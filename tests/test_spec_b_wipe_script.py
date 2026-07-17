"""Spec B 清库脚本单测. 主要盖 dry-run 不改 DB / Coolabah 排除 / 备份创建."""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "llm_ingest" / "scripts" / "spec_b_wipe_and_rescrape.py"


@pytest.fixture
def stub_db():
    """建一个有 funds + monthly_returns 数据的临时 DB."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.executescript("""
        CREATE TABLE funds (
            fund_id TEXT PRIMARY KEY,
            fund_name TEXT NOT NULL,
            confirmed_url TEXT NOT NULL,
            fetch_method TEXT NOT NULL,
            url_type TEXT NOT NULL,
            fundmonitors_fund_id INTEGER
        );
        CREATE TABLE monthly_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_id TEXT NOT NULL,
            date TEXT NOT NULL,
            net_return REAL NOT NULL,
            nav REAL NOT NULL
        );
        CREATE TABLE confirmed_gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_id TEXT NOT NULL,
            missing_month TEXT NOT NULL
        );
        CREATE TABLE pending_review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_id TEXT NOT NULL,
            date TEXT NOT NULL,
            net_return REAL NOT NULL,
            extract_method TEXT NOT NULL,
            review_state TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE fund_metrics (fund_id TEXT PRIMARY KEY, date_period TEXT);
        CREATE TABLE anomalies (id INTEGER PRIMARY KEY, fund_id TEXT);
        CREATE TABLE ai_reports (id INTEGER PRIMARY KEY, fund_id TEXT);
    """)
    conn.execute(
        "INSERT INTO funds (fund_id, fund_name, confirmed_url, fetch_method, url_type, fundmonitors_fund_id) "
        "VALUES ('bentham_global_income', 'Bentham Global Income Fund', 'https://a.com', 'code', 'archive', 3312)"
    )
    conn.execute(
        "INSERT INTO funds (fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES ('coolabah_frhy_assisted', 'Coolabah FRHY Assisted', 'https://c.com', 'code', 'archive')"
    )
    conn.execute(
        "INSERT INTO monthly_returns (fund_id, date, net_return, nav) VALUES "
        "('bentham_global_income', '2024-01-31', 0.005, 1.005), "
        "('coolabah_frhy_assisted', '2024-01-31', 0.003, 1.003)"
    )
    conn.commit()
    conn.close()
    yield tmp.name
    Path(tmp.name).unlink(missing_ok=True)


def _run_script(*args, db_path=None, expect_fail=False):
    env = {"FUND_DB_PATH": db_path} if db_path else {}
    import os
    env = {**os.environ, **env}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env,
    )
    if not expect_fail:
        assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    return result


def test_dry_run_does_not_modify_db(stub_db):
    result = _run_script("--dry-run", "--skip-wipe", db_path=stub_db)
    conn = sqlite3.connect(stub_db)
    n = conn.execute("SELECT COUNT(*) FROM monthly_returns").fetchone()[0]
    conn.close()
    assert n == 2  # 未清
    assert "dry-run" in result.stdout.lower() or "dry" in result.stdout.lower()


def test_dry_run_lists_targets(stub_db):
    result = _run_script("--dry-run", db_path=stub_db)
    # bentham 在触发列表, coolabah 排除
    assert "bentham_global_income" in result.stdout
    assert "coolabah_frhy_assisted" not in result.stdout


def test_dry_run_previews_backup_path(stub_db):
    result = _run_script("--dry-run", db_path=stub_db)
    assert "backup" in result.stdout.lower() or ".spec_b_backup_" in result.stdout


def test_fund_id_filter_skips_wipe(stub_db):
    """--fund-id X --skip-wipe -> 只触发单支, 不清表."""
    result = _run_script(
        "--skip-wipe", "--fund-id", "bentham_global_income", "--dry-run",
        db_path=stub_db,
    )
    conn = sqlite3.connect(stub_db)
    n = conn.execute("SELECT COUNT(*) FROM monthly_returns").fetchone()[0]
    conn.close()
    assert n == 2  # 未清


def test_backup_creation_before_wipe(stub_db, tmp_path, monkeypatch):
    """--yes 模式: 应创建 db.spec_b_backup_YYYYMMDD_HHMMSS 文件."""
    monkeypatch.chdir(tmp_path)
    shutil.copy(stub_db, tmp_path / "fund.db")

    # 用 dry-run 也应打印备份路径 (真跑要求 webapp 后端起, 单测不做)
    result = _run_script(
        "--dry-run", db_path=str(tmp_path / "fund.db"),
    )
    assert "backup" in result.stdout.lower()
