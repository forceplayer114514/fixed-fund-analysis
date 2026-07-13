"""lib.audit 批量扫描 + 入库后自动触发测试。"""
from __future__ import annotations

from lib.audit import audit_all_funds
from lib.db import create_fund, upsert_monthly_return


def _seed(conn, fid, rets, name=None, shareclass_prefix=None):
    create_fund(conn, fund_id=fid, fund_name=name or fid,
                confirmed_url="http://x", fetch_method="pdf", url_type="t",
                shareclass_prefix=shareclass_prefix)
    for d, r in rets:
        upsert_monthly_return(conn, fund_id=fid, date=d, net_return=r)


def test_audit_clean_db_returns_empty(db_conn, tmp_path, monkeypatch):
    # 隔离报告输出目录，避免污染仓库 docs/superpowers/audits/
    monkeypatch.setenv("FUND_AUDIT_DIR", str(tmp_path))
    report = audit_all_funds(db_conn)
    assert report["fund_reports"] == {}
    assert report["suspect_pairs"] == []


def test_audit_detects_misaligned_via_sibling(db_conn):
    """两 share class 差值大 -> audit 报 Check 6。

    shareclass_prefix 入库时存入 funds 表（非 fund_id 启发式），audit 读存储值
    做 B 组 6 跨份额类校验。
    """
    _seed(db_conn, "coolabah_frhy_assisted",
          [["2025-01-31", 0.0072], ["2025-02-28", 0.0073]], name="A",
          shareclass_prefix="coolabah_frhy_")
    _seed(db_conn, "coolabah_frhy_institutional",
          [["2025-01-31", 0.0040], ["2025-02-28", 0.0041]], name="I",
          shareclass_prefix="coolabah_frhy_")
    report = audit_all_funds(db_conn)
    inst = report["fund_reports"].get("coolabah_frhy_institutional", {})
    assert any("Check 6" in e for e in inst.get("errors_block", []))


def test_audit_writes_report_file(db_conn, tmp_path, monkeypatch):
    _seed(db_conn, "solo_fund", [["2025-01-31", 0.005]], name="S")
    monkeypatch.setenv("FUND_AUDIT_DIR", str(tmp_path))
    audit_all_funds(db_conn)
    import os
    files = list(tmp_path.glob("*-consistency-audit.md"))
    assert len(files) == 1
