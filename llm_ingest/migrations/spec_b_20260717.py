"""Spec B 迁移 (2026-07-17): funds 表加 discovered_source_name 列.

用于透明展示 fundmonitors 页面上实际抓到的基金名, 前端与输入名不一致时标红核对。
幂等: PRAGMA table_info 探测列存在则跳过 ALTER。

调用:
  from llm_ingest.migrations.spec_b_20260717 import apply
  apply(conn)  # 幂等, 反复调不炸
"""
from __future__ import annotations

import sqlite3


def apply(conn: sqlite3.Connection) -> None:
    """ALTER TABLE funds ADD COLUMN discovered_source_name TEXT (幂等)。

    - 探测列已存在 -> 直接返回 (支持二次调用)
    - 列不存在 -> ALTER + commit
    - 不动 funds 表既有数据 (SQLite ADD COLUMN 默认 NULL 填充旧行)
    """
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(funds)")
    cols = {row[1] for row in cur.fetchall()}
    if "discovered_source_name" not in cols:
        cur.execute("ALTER TABLE funds ADD COLUMN discovered_source_name TEXT")
        conn.commit()
