"""skills 候选数据源策略探测层。

遍历候选策略清单，结构化报告"部分成功+缺口"，区分真无解(exhausted)
vs 过早退出(premature_exit=bug)。纯探测层，不入库--主会话拿 DiscoveryReport
后调 lib.ingest 入库。

第一性原理：目标是拿逐月数据，单一路径失败≠数据不存在。
数据完整性原则同 extract.py：探测失败返回 found=False + evidence，绝不合成数据。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from lib import db as dbmod
from lib import extract as ex

# 候选策略清单（有序，适配用户约束 a-g，本地缓存最先做）
# g: human_intervention 是兜底标记，非策略
STRATEGY_LIST = [
    "local_cache",          # f: 本地已有数据（DB）最先做
    "official_evergreen",   # a: 官网 evergreen PDF/HTML
    "fundmonitors",         # c: featured/non-featured 判定
    "wayback_cdx",          # d: Wayback CDX 历史快照
    "distributions",        # b: 官网 Distributions 分红（辅助，不替代 total return）
    "third_party_rolling",  # e: 第三方滚动收益（验证/补充）
]

DEFAULT_FULL_COVERAGE_THRESHOLD = 36


@dataclass
class StrategyResult:
    strategy: str
    tried: bool = False
    found: bool = False
    months_count: int = 0
    gaps: list = field(default_factory=list)
    evidence: str = ""
    paywall_hit: bool = False
    ingest_entry: Optional[str] = None  # "add" / "add-table" / None
    fetch_method: str = ""              # "pdf" / "html"
    confirmed_url: str = ""
    skip_reason: str = ""               # 早停填 "full_coverage"


@dataclass
class DiscoveryReport:
    fund_id: str
    strategies_tried: list = field(default_factory=list)
    strategies_skipped: list = field(default_factory=list)
    strategies_succeeded: list = field(default_factory=list)
    best_strategy: Optional[str] = None
    total_months_obtainable: int = 0
    gaps: list = field(default_factory=list)
    coverage: str = "none"              # "full" | "partial" | "none"
    exhausted: bool = False             # 穷尽清单或满覆盖早停
    premature_exit: bool = False        # bug：partial/none 时未遍历完
    human_intervention_needed: bool = False
    evidence_log: list = field(default_factory=list)


ProbeFn = Callable[[dict], StrategyResult]


def _curl(url: str, timeout: int = 60) -> Optional[str]:
    """bash curl 兜底抓取（走系统网络绕 MCP 代理拦）。返回 stdout 或 None。"""
    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        return r.stdout if r.returncode == 0 and r.stdout else None
    except Exception:
        return None


def _curl_download(url: str, dest: str, timeout: int = 60) -> bool:
    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), "-o", dest, url],
            capture_output=True, timeout=timeout + 10,
        )
        return r.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0
    except Exception:
        return False


_MONTH_TOKENS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _pdf_has_monthly_table(text: str) -> bool:
    """搜 Monthly/History/Jan-Dec 判断 PDF 是否含 Year×Month 逐月历史表。"""
    if not text:
        return False
    if "Monthly" in text or "History" in text:
        return True
    hits = sum(1 for m in _MONTH_TOKENS if m in text)
    return hits >= 6


def probe_local_cache(fund_info: dict) -> StrategyResult:
    """查本地 DB 是否已有该基金月度数据（应最先做）。"""
    fund_id = fund_info.get("fund_id", "")
    db_path = fund_info.get("db_path")
    try:
        conn = dbmod.get_connection(db_path)
        rows = dbmod.get_monthly_returns(conn, fund_id)
        conn.close()
    except Exception as e:
        return StrategyResult(strategy="local_cache", tried=True, found=False,
                              evidence=f"DB 查询失败: {e}")
    if rows:
        return StrategyResult(strategy="local_cache", tried=True, found=True,
                              months_count=len(rows),
                              evidence=f"DB 已有 {len(rows)} 月数据",
                              confirmed_url="local:sqlite")
    return StrategyResult(strategy="local_cache", tried=True, found=False,
                          evidence="DB 无该基金数据")


def probe_official_evergreen(fund_info: dict) -> StrategyResult:
    """官网 evergreen PDF/HTML。fund_info 可含 official_pdf_url 或 official_pdf_path。"""
    url = fund_info.get("official_pdf_url")
    path = fund_info.get("official_pdf_path")
    if not url and not path:
        return StrategyResult(
            strategy="official_evergreen", tried=True, found=False,
            evidence="fund_info 未提供 official_pdf_url/path，需主会话 mcp__search__search 探测官网后回填",
        )
    if path and os.path.exists(path):
        pdf_path = path
    elif url:
        pdf_path = "/tmp/_strat_official.pdf"
        if not _curl_download(url, pdf_path):
            return StrategyResult(strategy="official_evergreen", tried=True, found=False,
                                  evidence=f"curl 下载失败: {url}")
    else:
        return StrategyResult(strategy="official_evergreen", tried=True, found=False,
                              evidence="无 URL/path 可用")
    try:
        text = ex.parse_pdf_text(pdf_path)
    except Exception as e:
        return StrategyResult(strategy="official_evergreen", tried=True, found=False,
                              evidence=f"parse_pdf_text 失败: {e}")
    if _pdf_has_monthly_table(text):
        return StrategyResult(
            strategy="official_evergreen", tried=True, found=True,
            months_count=fund_info.get("official_estimated_months", 0),
            evidence="PDF 含 Year×Month 逐月历史表",
            ingest_entry="add", fetch_method="pdf",
            confirmed_url=url or f"file:{path}",
        )
    return StrategyResult(
        strategy="official_evergreen", tried=True, found=False,
        evidence="PDF 仅滚动收益无逐月表（搜 Monthly/History/Jan-Dec 未命中）",
        confirmed_url=url or "",
    )


def probe_fundmonitors(fund_info: dict) -> StrategyResult:
    """fundmonitors Full Profile AJAX。fund_info 需含 fundmonitors_html_path（主会话 mcp 预抓）。"""
    path = fund_info.get("fundmonitors_html_path")
    if not path or not os.path.exists(path):
        return StrategyResult(
            strategy="fundmonitors", tried=True, found=False,
            evidence="fund_info 未提供 fundmonitors_html_path，需主会话 stealthy_fetch 抓 fund-profile.php?IsAjax=1 后回填路径",
        )
    try:
        with open(path, encoding="utf-8") as f:
            md = f.read()
    except Exception as e:
        return StrategyResult(strategy="fundmonitors", tried=True, found=False,
                              evidence=f"读取失败: {e}")
    low = md.lower()
    if "must be logged in" in low or "premium" in low:
        return StrategyResult(
            strategy="fundmonitors", tried=True, found=False,
            paywall_hit=True,
            evidence="fundmonitors 付费墙/登录墙（non-featured），立即跳过不提供账号",
        )
    records, _ytd = ex.parse_html_monthly_table(md)
    if records:
        return StrategyResult(
            strategy="fundmonitors", tried=True, found=True,
            months_count=len(records),
            evidence=f"含 Historical Performance 逐月表 {len(records)} 月",
            ingest_entry="add-table", fetch_method="html",
            confirmed_url=fund_info.get("fundmonitors_url", ""),
        )
    return StrategyResult(
        strategy="fundmonitors", tried=True, found=False,
        evidence="无 Historical Performance 逐月表（或为 N/A 摘要页）",
    )


def probe_wayback_cdx(fund_info: dict) -> StrategyResult:
    """Wayback CDX API 查 evergreen URL 历史快照。fund_info 需含 issuer_domain + slug 变体。"""
    domain = fund_info.get("issuer_domain")
    slugs = fund_info.get("wayback_slugs", [])
    if not domain:
        return StrategyResult(strategy="wayback_cdx", tried=True, found=False,
                              evidence="fund_info 未提供 issuer_domain，无法查 CDX")
    patterns = [f"{domain}/performance*", f"{domain}/wp-content/uploads/*"]
    for s in slugs:
        patterns.append(f"{domain}/performance-pdf-{s}*")
        patterns.append(f"{domain}/performance-report-{s}*")
    total = 0
    for pat in patterns:
        api = (
            f"http://web.archive.org/cdx/search/cdx?url={pat}"
            f"&output=json&fl=timestamp,original,statuscode&filter=statuscode:200&limit=200"
        )
        out = _curl(api, timeout=30)
        if out:
            try:
                arr = json.loads(out)
                total += max(0, len(arr) - 1)  # 首行是表头
            except Exception:
                pass
    if total > 0:
        return StrategyResult(
            strategy="wayback_cdx", tried=True, found=True,
            months_count=min(total, fund_info.get("target_months", DEFAULT_FULL_COVERAGE_THRESHOLD)),
            evidence=f"CDX 命中 {total} 个 200 快照（可多 PDF 合成逐月）",
            ingest_entry="add", fetch_method="pdf",
            confirmed_url="web.archive.org",
        )
    return StrategyResult(
        strategy="wayback_cdx", tried=True, found=False,
        evidence=f"CDX 零快照（查了 {len(patterns)} 个 URL 模式：归档页+slug变体+wp-content）",
    )


def probe_distributions(fund_info: dict) -> StrategyResult:
    """官网 Distributions 分红历史。辅助字段，不替代 total return。"""
    path = fund_info.get("distributions_html_path")
    if not path or not os.path.exists(path):
        return StrategyResult(
            strategy="distributions", tried=True, found=False,
            evidence="fund_info 未提供 distributions_html_path；分红仅辅助不可替代 total return",
        )
    return StrategyResult(
        strategy="distributions", tried=True, found=False,
        evidence="Distributions 仅分红历史，非月度 total return，不可作主数据源",
    )


def probe_third_party_rolling(fund_info: dict) -> StrategyResult:
    """第三方聚合站滚动收益。仅验证/补充，不主源。"""
    return StrategyResult(
        strategy="third_party_rolling", tried=True, found=False,
        evidence="第三方滚动收益仅区间值非逐月，且多为付费墙，跳过作主源",
    )


DEFAULT_PROBES: dict = {
    "local_cache": probe_local_cache,
    "official_evergreen": probe_official_evergreen,
    "fundmonitors": probe_fundmonitors,
    "wayback_cdx": probe_wayback_cdx,
    "distributions": probe_distributions,
    "third_party_rolling": probe_third_party_rolling,
}


def run_discovery(
    fund_info: dict,
    probes: Optional[dict] = None,
    full_coverage_threshold: int = DEFAULT_FULL_COVERAGE_THRESHOLD,
) -> DiscoveryReport:
    """遍历候选策略清单。

    满 coverage(>=target) 早停(剩余 tried=False skip_reason=full_coverage)。
    partial/none 必须遍历完整个 STRATEGY_LIST，否则 premature_exit=True(视为 bug)。
    """
    probes = probes if probes is not None else DEFAULT_PROBES
    target = fund_info.get("target_months", full_coverage_threshold)
    report = DiscoveryReport(fund_id=fund_info.get("fund_id", ""))
    for name in STRATEGY_LIST:
        if report.total_months_obtainable >= target:
            result = StrategyResult(strategy=name, tried=False, skip_reason="full_coverage")
            report.strategies_skipped.append(name)
        else:
            result = probes[name](fund_info)
            result.strategy = name
            report.strategies_tried.append(name)
            if result.found:
                report.strategies_succeeded.append(name)
                if result.months_count > report.total_months_obtainable:
                    report.total_months_obtainable = result.months_count
                if report.best_strategy is None:
                    report.best_strategy = name
        report.evidence_log.append(asdict(result))
    visited = len(report.strategies_tried) + len(report.strategies_skipped)
    if report.total_months_obtainable >= target:
        report.coverage = "full"
        report.exhausted = True
    elif report.total_months_obtainable > 0:
        report.coverage = "partial"
        report.exhausted = visited == len(STRATEGY_LIST)
        if not report.exhausted:
            report.premature_exit = True
    else:
        report.coverage = "none"
        report.exhausted = visited == len(STRATEGY_LIST)
        if not report.exhausted:
            report.premature_exit = True
        report.human_intervention_needed = True
    return report


def _cli() -> int:
    parser = argparse.ArgumentParser(description="skills 候选数据源策略探测")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("probe", help="探测候选数据源策略")
    p.add_argument("--fund-id", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--apir", default=None)
    p.add_argument("--issuer-domain", default=None)
    p.add_argument("--official-pdf-url", default=None)
    p.add_argument("--fundmonitors-html", default=None)
    p.add_argument("--target-months", type=int, default=None)
    p.add_argument("--db-path", default=None)
    args = parser.parse_args()

    fund_info = {
        "fund_id": args.fund_id,
        "name": args.name,
        "apir": args.apir,
        "issuer_domain": args.issuer_domain,
        "official_pdf_url": args.official_pdf_url,
        "fundmonitors_html_path": args.fundmonitors_html,
        "target_months": args.target_months or DEFAULT_FULL_COVERAGE_THRESHOLD,
        "db_path": args.db_path,
    }
    report = run_discovery(fund_info)
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if not report.premature_exit else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
