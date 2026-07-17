#!/usr/bin/env python3
"""Spec B: 清 monthly_returns 等 6 表 + 批量触发 fundmonitors L1 重爬.

7 步:
  1. 前置检查 (webapp 后端 / DB 文件 / .env / 磁盘)
  2. 备份 data/fund_analysis.db -> data/fund_analysis.db.spec_b_backup_{ts}
  3. 单事务清 6 表 (monthly_returns / confirmed_gaps / pending_review /
     fund_metrics / anomalies / ai_reports); funds 表保留
  4. 读 funds 过滤 Coolabah x 2 (延后 Spec C)
  5. ThreadPoolExecutor(max_workers=4) 并发触发 POST /api/ingest/funds
  6. 每 5 秒轮询 GET /api/ingest/jobs/{id} 直到全部 succeeded/failed
  7. 打印汇总

CLI:
  --yes         跳过 YES 确认
  --dry-run     只打印将执行的 SQL / 触发列表, 不实操
  --fund-id X   只跑单支 (跳过清库)
  --skip-wipe   跳步 2-3, 复用现有 DB (调试)

Exit code:
  0 - 所有 job succeeded
  1 - 前置检查失败 / 用户拒 YES
  2 - 至少一支 job failed
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as _dt
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WEBAPP_HOST = os.environ.get("WEBAPP_HOST", "http://127.0.0.1:8000")
HEALTH_URL = f"{WEBAPP_HOST}/health"
INGEST_URL = f"{WEBAPP_HOST}/api/ingest/funds"
JOB_URL_TPL = f"{WEBAPP_HOST}/api/ingest/jobs/{{}}"

EXCLUDED_FUNDS = {"coolabah_frhy_assisted", "coolabah_frhy_institutional"}
WIPE_TABLES = [
    "monthly_returns", "confirmed_gaps", "pending_review",
    "fund_metrics", "anomalies", "ai_reports",
]

DEFAULT_DB_PATH = Path("data/fund_analysis.db")


def _db_path() -> Path:
    """DB 路径解析: 优先 FUND_DB_PATH 环境变量, 兜底 data/fund_analysis.db."""
    env = os.environ.get("FUND_DB_PATH")
    return Path(env) if env else DEFAULT_DB_PATH


def _http_get_json(url: str, timeout: int = 30) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def _http_post_json(url: str, body: Dict[str, Any],
                    timeout: int = 30) -> Optional[Dict[str, Any]]:
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def check_prerequisites(dry_run: bool) -> Tuple[bool, str]:
    """前置检查. dry-run 模式跳过 webapp 后端和 .env 检查 (只测脚本自身)."""
    db = _db_path()
    if not db.exists():
        return False, f"DB 文件不存在: {db}"
    if dry_run:
        return True, "ok (dry-run 跳过 webapp/.env 检查)"
    # webapp 后端存活
    r = _http_get_json(HEALTH_URL)
    if r is None or r.get("status") != "ok":
        return False, f"webapp 后端未启 ({HEALTH_URL}). 起动: python webapp/backend/run.py"
    # .env SUB2API_KEY (虽本期 L1 不用, L2 fallback 用)
    if not os.environ.get("SUB2API_KEY"):
        # 尝试 .env 读
        env_file = Path(".env")
        if env_file.exists():
            has_key = False
            for line in env_file.read_text().splitlines():
                if line.startswith("SUB2API_KEY="):
                    has_key = True
                    break
            if not has_key:
                return False, ".env 无 SUB2API_KEY (L2 PDF 通路需要)"
        else:
            return False, ".env 不存在"
    # 磁盘空间
    free = shutil.disk_usage(db.parent).free
    if free < 100 * 1024 * 1024:  # 100 MB
        return False, f"磁盘空间不足 ({free / 1e6:.1f} MB < 100 MB)"
    return True, "ok"


def make_backup(dry_run: bool) -> Optional[Path]:
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    src = _db_path()
    dst = src.with_name(f"{src.name}.spec_b_backup_{ts}")
    print(f"[backup] {src} -> {dst}")
    if dry_run:
        return dst
    shutil.copy2(src, dst)
    print(f"[backup] done ({dst.stat().st_size / 1e6:.1f} MB)")
    return dst


def wipe_tables(dry_run: bool) -> None:
    db = _db_path()
    print(f"[wipe] target: {db}")
    print(f"[wipe] tables: {WIPE_TABLES}")
    if dry_run:
        for t in WIPE_TABLES:
            print(f"[wipe] DRY-RUN would: DELETE FROM {t}")
        return
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.cursor()
        conn.execute("BEGIN")
        for t in WIPE_TABLES:
            # 表可能不存在 (schema 演化), 忽略
            try:
                cur.execute(f"DELETE FROM {t}")
                print(f"[wipe] {t}: {cur.rowcount} 行删")
            except sqlite3.OperationalError as e:
                print(f"[wipe] {t}: 跳过 ({e})")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_target_funds(fund_id_filter: Optional[str] = None) -> List[Tuple[str, str]]:
    conn = sqlite3.connect(str(_db_path()))
    try:
        rows = conn.execute("SELECT fund_id, fund_name FROM funds").fetchall()
    finally:
        conn.close()
    out = []
    for fid, fname in rows:
        if fid in EXCLUDED_FUNDS:
            continue
        if fund_id_filter and fid != fund_id_filter:
            continue
        out.append((fid, fname))
    return out


def trigger_one(fund_id: str, fund_name: str,
                dry_run: bool) -> Tuple[str, Optional[str], str]:
    """POST /api/ingest/funds; 返 (fund_id, job_id or None, message)."""
    body = {
        "fund_id": fund_id, "fund_name": fund_name,
        "issuer": None, "confirmed_url": None, "issuer_domain": None,
        "asx_code": None, "apir_code": None, "max_pdf_pages": None, "limit": None,
    }
    if dry_run:
        return (fund_id, None, f"DRY-RUN would POST {body}")
    resp = _http_post_json(INGEST_URL, body, timeout=60)
    if resp is None:
        return (fund_id, None, "POST 失败")
    return (fund_id, resp.get("job_id"), "queued")


def poll_jobs(job_ids: List[Tuple[str, str]], poll_sec: int = 5,
              max_wait_sec: int = 1800) -> Dict[str, Dict[str, Any]]:
    """轮询 job 直到全终态或超时. 返 {fund_id: final_job_json}."""
    results: Dict[str, Dict[str, Any]] = {}
    pending = dict(job_ids)  # fund_id -> job_id
    deadline = time.time() + max_wait_sec
    while pending and time.time() < deadline:
        time.sleep(poll_sec)
        done_now = []
        for fid, jid in pending.items():
            r = _http_get_json(JOB_URL_TPL.format(jid))
            if r is None:
                continue
            state = r.get("state", "")
            if state in ("succeeded", "failed"):
                results[fid] = r
                done_now.append(fid)
                print(f"[poll] {fid} -> {state} ({r.get('stats')})")
        for fid in done_now:
            pending.pop(fid)
    if pending:
        for fid, jid in pending.items():
            print(f"[poll] {fid}: timeout, job_id={jid}")
            results[fid] = {"state": "timeout", "job_id": jid}
    return results


def summarize(results: Dict[str, Dict[str, Any]]) -> int:
    """打印汇总. 返 exit code (0 = 全 succeeded, 2 = 有 failed)."""
    print("\n" + "=" * 60)
    print("[summary]")
    print("=" * 60)
    fail_count = 0
    for fid, r in sorted(results.items()):
        state = r.get("state", "?")
        stats = r.get("stats", {}) or {}
        err = r.get("error", "")
        if state == "succeeded":
            print(f"[OK]   {fid}: monthly={stats.get('monthly', 0)}, "
                  f"pending={stats.get('pending', 0)}, gap={stats.get('gap', 0)}")
        else:
            fail_count += 1
            print(f"[FAIL] {fid}: state={state}, error={err}")
    print("=" * 60)
    print(f"total: {len(results)}, failed: {fail_count}")
    return 0 if fail_count == 0 else 2


def confirm_prompt(target_count: int) -> bool:
    """交互 YES 确认."""
    print(f"\n将清 6 表 (funds 表保留) 并重跑 {target_count} 支基金。")
    print(f"备份路径: {_db_path()}.spec_b_backup_{{ts}}")
    ans = input("输 YES 继续, 其他任意键退出: ").strip()
    return ans == "YES"


def main() -> int:
    ap = argparse.ArgumentParser(description="Spec B 全清 + 重爬")
    ap.add_argument("--yes", action="store_true", help="跳过交互 YES 确认")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将执行的 SQL / 触发列表, 不实操")
    ap.add_argument("--fund-id", default=None, help="只跑单支 (需与 --skip-wipe 配合)")
    ap.add_argument("--skip-wipe", action="store_true",
                    help="跳过步 2-3 (备份+清表), 复用现有 DB")
    args = ap.parse_args()

    print(f"[spec_b] {'DRY-RUN' if args.dry_run else 'LIVE'} mode")

    ok, msg = check_prerequisites(args.dry_run)
    if not ok:
        print(f"[prereq] FAIL: {msg}")
        return 1
    print(f"[prereq] {msg}")

    targets = load_target_funds(args.fund_id)
    if not targets:
        print(f"[targets] 无匹配基金 (fund_id={args.fund_id})")
        return 1
    print(f"[targets] {len(targets)} 支: {[t[0] for t in targets]}")

    if not args.skip_wipe:
        if not args.yes and not args.dry_run and not confirm_prompt(len(targets)):
            print("[confirm] 用户拒绝, 退出")
            return 1
        make_backup(args.dry_run)
        wipe_tables(args.dry_run)

    if args.dry_run:
        for fid, fname in targets:
            _, _, msg2 = trigger_one(fid, fname, dry_run=True)
            print(f"[trigger] {fid}: {msg2}")
        print("[spec_b] DRY-RUN 完成, 未实际触发")
        return 0

    # 并发触发
    triggered: List[Tuple[str, str]] = []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(trigger_one, fid, fname, False): fid
                   for fid, fname in targets}
        for fut in cf.as_completed(futures):
            fid, jid, msg2 = fut.result()
            print(f"[trigger] {fid} -> {jid} ({msg2})")
            if jid:
                triggered.append((fid, jid))
    if not triggered:
        print("[trigger] 全部触发失败")
        return 2

    results = poll_jobs(triggered)
    return summarize(results)


if __name__ == "__main__":
    sys.exit(main())
