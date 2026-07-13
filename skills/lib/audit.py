"""批量一致性审计：扫所有已入库基金，找静默字段错位存量。

入库后自动触发（ingest 入口末尾调 audit_all_funds）。跨份额类（5/6）按 fund_id
前缀分组组内互校验；7 所有基金两两相关 >0.98 报嫌疑对（warn）；复利验证需
rolling（audit 无 rolling 时跳过 compound）。报告写 docs/superpowers/audits/。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from lib.consistency import consistency_check


def _fund_prefix(fund_id: str) -> Optional[str]:
    """coolabah_frhy_assisted -> coolabah_frhy_；无下划线 -> None。"""
    idx = fund_id.rfind("_")
    if idx < 0:
        return None
    return fund_id[: idx + 1]


def audit_all_funds(conn: sqlite3.Connection) -> dict:
    """批量跑 consistency_check 扫所有基金。

    返回 {"fund_reports": {fund_id: {"errors_block":[...],"errors_warn":[...]}},
    "suspect_pairs": [(f1, f2, corr), ...]}。
    """
    fund_rows = conn.execute(
        "SELECT DISTINCT fund_id FROM monthly_returns ORDER BY fund_id"
    ).fetchall()
    fund_ids = [r["fund_id"] for r in fund_rows]

    fund_reports: dict = {}
    for fid in fund_ids:
        rec_rows = conn.execute(
            "SELECT date, net_return FROM monthly_returns WHERE fund_id=? "
            "ORDER BY date",
            (fid,),
        ).fetchall()
        records = [(r["date"], r["net_return"]) for r in rec_rows]
        prefix = _fund_prefix(fid)
        ok, block, warn = consistency_check(
            fid, records, conn, shareclass_prefix=prefix,
        )
        if block or warn:
            fund_reports[fid] = {
                "errors_block": block, "errors_warn": warn,
            }

    suspect_pairs = _suspect_correlation_pairs(conn, fund_ids)
    _write_report(conn, fund_ids, fund_reports, suspect_pairs)
    return {"fund_reports": fund_reports, "suspect_pairs": suspect_pairs}


def _suspect_correlation_pairs(
    conn: sqlite3.Connection, fund_ids: list[str]
) -> list[tuple]:
    """所有基金两两相关 >0.98（>=24 月重叠）报嫌疑对。"""
    series = {}
    for fid in fund_ids:
        rows = conn.execute(
            "SELECT date, net_return FROM monthly_returns WHERE fund_id=?",
            (fid,),
        ).fetchall()
        series[fid] = {r["date"]: r["net_return"] for r in rows}

    pairs = []
    for i, f1 in enumerate(fund_ids):
        for f2 in fund_ids[i + 1:]:
            common = sorted(set(series[f1]) & set(series[f2]))
            if len(common) < 24:
                continue
            a = [series[f1][d] for d in common]
            b = [series[f2][d] for d in common]
            corr = _pearson(a, b)
            if corr is not None and abs(corr) > 0.98:
                pairs.append((f1, f2, round(corr, 4)))
    return pairs


def _pearson(a: list[float], b: list[float]) -> Optional[float]:
    n = len(a)
    if n != len(b) or n == 0:
        return None
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = (sum((x - ma) ** 2 for x in a)) ** 0.5
    db = (sum((x - mb) ** 2 for x in b)) ** 0.5
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def _write_report(
    conn: sqlite3.Connection,
    fund_ids: list[str],
    fund_reports: dict,
    suspect_pairs: list[tuple],
) -> None:
    audit_dir = os.environ.get("FUND_AUDIT_DIR")
    if audit_dir is None:
        # 默认仓库根 docs/superpowers/audits/
        here = Path(__file__).resolve().parent.parent
        audit_dir = here.parent / "docs" / "superpowers" / "audits"
    audit_dir = Path(audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    # 用最后一个基金的 max date 拼时间戳（避免 Date.now）
    today = datetime.utcnow().strftime("%Y-%m-%d")
    path = audit_dir / f"{today}-consistency-audit.md"

    lines = [f"# Consistency Audit {today}", ""]
    lines.append(f"## 扫描基金数: {len(fund_ids)}")
    lines.append("")
    if not fund_reports and not suspect_pairs:
        lines.append("无 block/warn 问题。")
    else:
        for fid, rep in fund_reports.items():
            lines.append(f"### {fid}")
            for e in rep["errors_block"]:
                lines.append(f"- [block] {e}")
            for e in rep["errors_warn"]:
                lines.append(f"- [warn] {e}")
            lines.append("")
        if suspect_pairs:
            lines.append("## 嫌疑相关对（warn）")
            for f1, f2, c in suspect_pairs:
                lines.append(f"- {f1} vs {f2}: corr={c}")
    path.write_text("\n".join(lines), encoding="utf-8")
