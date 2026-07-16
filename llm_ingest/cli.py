"""CLI 入口. 当前实现: compare.

compare --fund-id gryphon_capital_income --out report.json
  逐份 PDF 跑 extract + 两道闸 + 对 DB 现值, 产 JSON 报告.
  判据:
    - 一致率 (abs(diff)<1e-6) >= 99%
    - 防线漏检 = 0 (两闸都过但值和 DB 不符)
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import discover as disc_mod
from . import extract as ex_mod
from . import pdf as pdf_mod
from . import store as store_mod
from . import verify

REPO = Path(__file__).resolve().parent.parent
DB_PATH = REPO / "data/fund_analysis.db"
PDF_ROOT = REPO / "data/pdf_cache"


def _load_db_series(fund_id: str) -> Dict[str, Tuple[float, str]]:
    """ym (YYYY-MM) -> (net_return, source_quote_或空)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.execute(
            "SELECT date, net_return, COALESCE(source_quote, '') FROM monthly_returns WHERE fund_id=? ORDER BY date",
            (fund_id,),
        )
        out: Dict[str, Tuple[float, str]] = {}
        for date, nr, sq in cur.fetchall():
            ym = date[:7]
            out[ym] = (float(nr), sq)
    finally:
        conn.close()
    return out


def _pdf_paths(fund_id: str) -> List[Path]:
    d = PDF_ROOT / fund_id
    if not d.exists():
        return []
    return sorted(p for p in d.glob("*.pdf"))


@dataclass
class PerFile:
    ym: str
    pdf: str
    extracted: Optional[float]
    db_value: Optional[float]
    diff: Optional[float]
    match: bool  # abs(diff)<1e-6
    not_found: bool
    parse_error: Optional[str]
    quote: str
    quote_passed: bool
    quote_reason: str
    rolling_passed: bool
    rolling_reason: str
    rolling_windows: int
    field_type_passed: bool
    antifab_passed: bool
    both_gates_passed: bool
    breach: bool  # 两闸都过但值和 DB 不符 = 防线漏检


@dataclass
class Report:
    fund_id: str
    total: int
    matched: int
    consistency_rate: float
    both_gates_passed: int
    breaches: int  # 判据主指标
    breach_ratio: float
    gate_stats: Dict[str, int]
    per_file: List[PerFile] = field(default_factory=list)
    started_at: float = 0.0
    duration_s: float = 0.0


def run_compare(fund_id: str, limit: Optional[int] = None, max_pages: int = 2, concurrency: int = 1) -> Report:
    db = _load_db_series(fund_id)
    pdfs = _pdf_paths(fund_id)
    if limit:
        pdfs = pdfs[:limit]

    # 前 11 月历史 (十进制) 累积传入 rolling 校验
    history: Dict[str, float] = {}

    per_files: List[PerFile] = []
    gate_stats = {
        "quote_blocked": 0,
        "rolling_blocked": 0,
        "field_type_blocked": 0,
        "antifab_blocked": 0,
        "not_found": 0,
        "parse_error": 0,
    }

    started = time.time()

    # Phase A: 并发 extract (API 密集).
    # Phase B: 串行 verify + 累积 history (rolling 校验按 ym 顺序).
    extractions: Dict[str, Any] = {}  # ym -> Extraction | Exception
    total = len(pdfs)

    def _do_extract(pdf: Path) -> Tuple[str, Any]:
        ym = pdf.stem
        try:
            return ym, ex_mod.extract_from_pdf(pdf, ym, max_pages=max_pages)
        except Exception as e:  # noqa: BLE001
            return ym, e

    if concurrency <= 1:
        for i, pdf in enumerate(pdfs, 1):
            ym, res = _do_extract(pdf)
            extractions[ym] = res
            tag = "ERR" if isinstance(res, Exception) else ("NF" if getattr(res, "not_found", False) else "ok")
            print(f"[extract {i}/{total}] {ym} {tag}", file=sys.stderr)
    else:
        with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
            futs = {ex.submit(_do_extract, p): p for p in pdfs}
            done = 0
            for fut in cf.as_completed(futs):
                ym, res = fut.result()
                extractions[ym] = res
                done += 1
                tag = "ERR" if isinstance(res, Exception) else ("NF" if getattr(res, "not_found", False) else "ok")
                print(f"[extract {done}/{total}] {ym} {tag}", file=sys.stderr)

    extract_elapsed = time.time() - started
    print(f"[extract-phase] {total} PDFs in {extract_elapsed:.1f}s (concurrency={concurrency})", file=sys.stderr)

    # Phase B: 串行 verify, 按 ym 顺序累积 history.
    for i, pdf in enumerate(pdfs, 1):
        ym = pdf.stem
        db_row = db.get(ym)
        db_val = db_row[0] if db_row else None
        res = extractions.get(ym)

        if isinstance(res, Exception):
            per_files.append(PerFile(
                ym=ym, pdf=str(pdf), extracted=None, db_value=db_val,
                diff=None, match=False, not_found=False, parse_error=f"api_error:{res}",
                quote="", quote_passed=False, quote_reason="api_error",
                rolling_passed=False, rolling_reason="api_error", rolling_windows=0,
                field_type_passed=False, antifab_passed=False,
                both_gates_passed=False, breach=False,
            ))
            print(f"[verify {i}/{total}] {ym} API ERR: {res}", file=sys.stderr)
            continue

        ex = res
        if ex.parse_error:
            gate_stats["parse_error"] += 1
        if ex.not_found:
            gate_stats["not_found"] += 1

        pdf_text = pdf_mod.full_text(pdf)
        q = verify.check_quote(ex.source_quote, pdf_text, ex.net_return)
        if not q.passed:
            gate_stats["quote_blocked"] += 1
        r = verify.check_rolling(ex.net_return, ym, history, ex.rolling)
        if not r.passed:
            gate_stats["rolling_blocked"] += 1
        f_ok, f_reason = verify.check_field_type(ex.net_return)
        if not f_ok:
            gate_stats["field_type_blocked"] += 1
        hist_recent = sorted(history.items(), reverse=True)
        af_ok, af_reason = verify.check_anti_fabrication(ex.net_return, ym, hist_recent)
        if not af_ok:
            gate_stats["antifab_blocked"] += 1

        both_ok = q.passed and r.passed and f_ok and af_ok
        diff: Optional[float] = None
        matched = False
        if ex.net_return is not None and db_val is not None:
            diff = ex.net_return - db_val
            matched = abs(diff) < 1e-6
        breach = both_ok and (ex.net_return is not None) and (db_val is not None) and (not matched)

        per_files.append(PerFile(
            ym=ym, pdf=str(pdf), extracted=ex.net_return, db_value=db_val,
            diff=diff, match=matched, not_found=ex.not_found,
            parse_error=ex.parse_error, quote=ex.source_quote,
            quote_passed=q.passed, quote_reason=q.reason,
            rolling_passed=r.passed, rolling_reason=r.reason,
            rolling_windows=r.windows_verified,
            field_type_passed=f_ok, antifab_passed=af_ok,
            both_gates_passed=both_ok, breach=breach,
        ))

        # 累积历史 (用 DB 真值, 不用提取值; 否则错误传播)
        if db_val is not None:
            history[ym] = db_val

        tag = "OK" if matched else ("BREACH" if breach else ("MISS" if not matched else ""))
        print(f"[verify {i}/{total}] {ym} ex={ex.net_return} db={db_val} diff={diff} gates=q{int(q.passed)}r{int(r.passed)}f{int(f_ok)}a{int(af_ok)} {tag}", file=sys.stderr)

    matched = sum(1 for p in per_files if p.match)
    both = sum(1 for p in per_files if p.both_gates_passed)
    breaches = sum(1 for p in per_files if p.breach)
    total_actual = len(per_files)
    return Report(
        fund_id=fund_id,
        total=total_actual,
        matched=matched,
        consistency_rate=(matched / total_actual) if total_actual else 0.0,
        both_gates_passed=both,
        breaches=breaches,
        breach_ratio=(breaches / total_actual) if total_actual else 0.0,
        gate_stats=gate_stats,
        per_file=per_files,
        started_at=started,
        duration_s=time.time() - started,
    )


def _report_to_json(r: Report) -> Dict[str, Any]:
    return {
        **{k: v for k, v in asdict(r).items() if k != "per_file"},
        "per_file": [asdict(p) for p in r.per_file],
    }


def cmd_discover(args: argparse.Namespace) -> int:
    r = disc_mod.run_discovery(
        fund_name=args.fund_name,
        issuer=args.issuer,
        fund_id=args.fund_id,
        issuer_domain=args.issuer_domain,
        asx_code=args.asx_code,
        inception_ym=args.inception_ym,
        latest_ym=args.latest_ym,
    )
    payload = {
        "fund_id": r.fund_id,
        "links": r.links,
        "per_level_contribution": r.per_level_contribution,
        "per_level_source": r.per_level_source,
        "obtained": r.obtained,
        "gaps": r.gaps,
        "unparseable_count": r.unparseable_count,
        "archive_pointer": (
            asdict(r.archive_pointer) if r.archive_pointer else None
        ),
        "evidence_log": r.evidence_log,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n=== discovery summary ===")
    print(f"fund_id: {r.fund_id}")
    print(f"links found: {len(r.links)}")
    print(f"per_level: {r.per_level_contribution}")
    print(f"gaps: {len(r.gaps)}")
    if r.archive_pointer:
        print(f"archive_url: {r.archive_pointer.archive_url}")
        print(f"no_archive: {r.archive_pointer.no_archive}")
        print(f"latest_pdf_url: {r.archive_pointer.latest_pdf_url}")
    print(f"unparseable: {r.unparseable_count}")
    print(f"report -> {args.out}")
    return 0 if r.links else 2


def _download_pdf(url: str, out_path: Path, timeout: int = 60) -> bool:
    """下载 PDF 到 out_path. requests + curl 兜底. 返 True/False."""
    import requests
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(r.content)
            return True
    except Exception:
        pass
    # curl 兜底
    import subprocess
    try:
        proc = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), "-A", "Mozilla/5.0", "-o", str(out_path), url],
            capture_output=True, timeout=timeout + 10,
        )
        if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
            head = out_path.read_bytes()[:4]
            if head == b"%PDF":
                return True
            out_path.unlink()
    except Exception:
        pass
    return False


def cmd_ingest(args: argparse.Namespace) -> int:
    """端到端: discover -> download -> extract -> verify -> store.

    fund 必须已在 funds 表 (先 upsert_fund). ingest 逐月读 links, 下载 PDF,
    提取, 走两闸, 写 monthly_returns / pending_review / confirmed_gaps.
    """
    fund_id = args.fund_id
    conn = store_mod.open_conn()
    store_mod.ensure_tables_if_missing(conn)

    # 若给了 fund-name/issuer, 顺带 upsert 元信息
    if args.fund_name and args.confirmed_url:
        store_mod.upsert_fund(
            conn, fund_id=fund_id, fund_name=args.fund_name,
            confirmed_url=args.confirmed_url,
            apir_code=args.apir_code, max_pdf_pages=args.max_pages,
        )

    # 加载 links
    if args.discovery_json:
        payload = json.loads(Path(args.discovery_json).read_text())
        links: List[Tuple[str, str]] = [(str(y), str(u)) for y, u in payload.get("links", [])]
    elif args.fund_name and args.issuer:
        # 现跑 discover
        rep = disc_mod.run_discovery(
            fund_name=args.fund_name, issuer=args.issuer, fund_id=fund_id,
            issuer_domain=args.issuer_domain, asx_code=args.asx_code,
            inception_ym=args.inception_ym, latest_ym=args.latest_ym,
        )
        links = rep.links
    else:
        print("必须提供 --discovery-json, 或 (--fund-name + --issuer) 现跑 discovery", file=sys.stderr)
        return 2

    if args.limit:
        links = links[: args.limit]
    print(f"[ingest] {len(links)} links to process for {fund_id}", file=sys.stderr)

    pdf_dir = PDF_ROOT / fund_id
    stats = {"monthly": 0, "pending": 0, "gap": 0, "download_fail": 0}

    for i, (ym, url) in enumerate(links, 1):
        pdf_path = pdf_dir / f"{ym}.pdf"
        # 下载 (若本地已缓存则跳过)
        if not pdf_path.exists():
            ok = _download_pdf(url, pdf_path)
            if not ok:
                stats["download_fail"] += 1
                store_mod.record_confirmed_gap(
                    conn, fund_id=fund_id, missing_month=ym,
                    exhausted_levels="download_fail",
                )
                print(f"[{i}/{len(links)}] {ym} download FAILED {url}", file=sys.stderr)
                continue
        # 提取
        try:
            ex = ex_mod.extract_from_pdf(pdf_path, ym, max_pages=args.max_pages)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(links)}] {ym} extract error: {e}", file=sys.stderr)
            store_mod.record_confirmed_gap(
                conn, fund_id=fund_id, missing_month=ym,
                exhausted_levels=f"api_error:{type(e).__name__}",
            )
            continue
        # 两闸
        pdf_text = pdf_mod.full_text(pdf_path)
        q = verify.check_quote(ex.source_quote, pdf_text, ex.net_return)
        history = store_mod.load_monthly_history(conn, fund_id)
        r = verify.check_rolling(ex.net_return, ex.ym, history, ex.rolling)
        # 写库
        dec = store_mod.write_extraction(
            conn, fund_id=fund_id, ex=ex,
            quote_check=q, rolling_check=r,
            monthly_history=history,
        )
        stats[dec.action] = stats.get(dec.action, 0) + 1
        print(f"[{i}/{len(links)}] {ym} {dec.action} {dec.gate_summary} {dec.reason}",
              file=sys.stderr)

    print(f"\n=== ingest summary ===")
    print(f"fund_id: {fund_id}")
    print(f"stats: {stats}")
    return 0 if stats["monthly"] > 0 else 2


def cmd_compare(args: argparse.Namespace) -> int:
    r = run_compare(args.fund_id, limit=args.limit, max_pages=args.max_pages, concurrency=args.concurrency)
    Path(args.out).write_text(json.dumps(_report_to_json(r), ensure_ascii=False, indent=2))
    print(f"\n=== compare summary ===")
    print(f"fund_id: {r.fund_id}")
    print(f"total: {r.total}  matched: {r.matched}  consistency: {r.consistency_rate:.4f}")
    print(f"both_gates_passed: {r.both_gates_passed}  breaches: {r.breaches}  breach_ratio: {r.breach_ratio:.4f}")
    print(f"gate_stats: {r.gate_stats}")
    print(f"duration: {r.duration_s:.1f}s")
    print(f"report -> {args.out}")
    # 判据: breaches=0 且 consistency>=0.99
    verdict = (r.breaches == 0) and (r.consistency_rate >= 0.99)
    print(f"VERDICT: {'PASS' if verdict else 'FAIL'}")
    return 0 if verdict else 2


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="llm_ingest")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compare", help="run extract+verify vs DB for a fund")
    c.add_argument("--fund-id", required=True)
    c.add_argument("--out", default="report.json")
    c.add_argument("--limit", type=int, default=None, help="only first N PDFs (debug)")
    c.add_argument("--max-pages", type=int, default=2)
    c.add_argument("--concurrency", type=int, default=1, help="并发 extract 数, 1=串行")
    c.set_defaults(func=cmd_compare)

    d = sub.add_parser("discover", help="find archive PDFs via L1 (Gemini web search) + L2 (wayback)")
    d.add_argument("--fund-name", required=True, help="e.g. 'Gryphon Capital Income Trust'")
    d.add_argument("--issuer", required=True, help="e.g. 'Gryphon Capital Investments'")
    d.add_argument("--fund-id", required=True, help="internal id, e.g. gryphon_capital_income")
    d.add_argument("--issuer-domain", default=None, help="known official domain, may be empty")
    d.add_argument("--asx-code", default=None)
    d.add_argument("--inception-ym", default=None, help="YYYY-MM (若已知), 未给则从 L1 结果反推")
    d.add_argument("--latest-ym", default=None, help="YYYY-MM, 默认当前-2月")
    d.add_argument("--out", default="discovery.json")
    d.set_defaults(func=cmd_discover)

    i = sub.add_parser("ingest", help="end-to-end: discover -> download -> extract -> verify -> store")
    i.add_argument("--fund-id", required=True)
    i.add_argument("--fund-name", default=None, help="给了则 upsert 到 funds 表")
    i.add_argument("--issuer", default=None, help="现跑 discovery 时必给")
    i.add_argument("--issuer-domain", default=None)
    i.add_argument("--asx-code", default=None)
    i.add_argument("--apir-code", default=None)
    i.add_argument("--confirmed-url", default=None, help="funds.confirmed_url; 无则用 issuer_domain")
    i.add_argument("--inception-ym", default=None)
    i.add_argument("--latest-ym", default=None)
    i.add_argument("--discovery-json", default=None,
                   help="预跑 discover 的 JSON, 免除本次现跑 (省 Gemini 调用)")
    i.add_argument("--limit", type=int, default=None, help="仅处理前 N 个链接 (调试)")
    i.add_argument("--max-pages", type=int, default=2)
    i.set_defaults(func=cmd_ingest)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
