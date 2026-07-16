"""Phase 2: 归档页发现 + PDF 下载.

三层 fallback (集合差驱动, 概念抄 skills/lib/strategies.py):

  L1 官网归档页    Gemini 联网 (messages_with_search) 找页, 再 fetch, 再 Gemini 解析
  L2 Wayback CDX  http://web.archive.org/cdx/search/cdx  (纯 requests, 20 行)
  L3 fundmonitors Full Fund Profile AJAX  (纯 requests, 40 行)

产出 DiscoveryReport {links: [(ym, url), ...], gaps, per_level_contribution, ...}.
调用方 (ingest.py) 拿 links 后下载 -> extract.py 走两道闸 -> store.py 写库.

搜索会降级到 gemini-2.5-flash (中转限制), 因此搜索/读页/读 PDF 拆多次调用.
"""
from __future__ import annotations

import datetime
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from . import extract as ex_mod
from .client import Client

PROMPT_DIR = Path(__file__).parent / "prompts"
CDX_SNAPSHOTS_PER_MONTH = 3
DEFAULT_HTTP_TIMEOUT = 60


# ---------- 数据模型 ----------

@dataclass
class ArchivePointer:
    """L1 Gemini 联网返回."""
    archive_url: Optional[str]
    pagination_param: Optional[str]  # "Page" / "ArchiveYear" / None
    no_archive: bool
    latest_pdf_url: Optional[str]
    issuer_domain_confirmed: Optional[str]
    evidence: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscoveryReport:
    fund_id: str
    links: List[Tuple[str, str]] = field(default_factory=list)  # [(ym, pdf_url), ...] 去重后
    per_level_contribution: Dict[str, int] = field(default_factory=dict)
    per_level_source: Dict[str, str] = field(default_factory=dict)  # {"L1": "https://..."}
    gaps: List[str] = field(default_factory=list)                    # expected - obtained
    obtained: List[str] = field(default_factory=list)
    archive_pointer: Optional[ArchivePointer] = None
    unparseable_count: int = 0
    evidence_log: List[Dict[str, Any]] = field(default_factory=list)


# ---------- 工具 ----------

_MONTHS = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june","july","august",
     "september","october","november","december"], start=1)}
_MONTHS_ABBR = {m: i for i, m in enumerate(
    ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], start=1)}


def _parse_ym_from_text(text: str) -> Optional[str]:
    """'March 2025' / 'Mar-2025' / '2025-03' / 'monthly-report-202503.pdf' -> 'YYYY-MM'."""
    if not text:
        return None
    t = text.lower()
    # 1a) YYYYMMDD  (纯 8 位, 日 01-31)
    m = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # 1b) YYYY-MM / YYYY_MM / YYYYMM
    m = re.search(r"(?<!\d)(20\d{2})[-_/]?(0[1-9]|1[0-2])(?!\d)", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # 2) 月份名 + 年
    names = "|".join(list(_MONTHS.keys()) + list(_MONTHS_ABBR.keys()))
    m = re.search(rf"\b({names})[\-_\s\.]*(20\d{{2}})", t)
    if m:
        mon = _MONTHS.get(m.group(1)) or _MONTHS_ABBR.get(m.group(1))
        if mon:
            return f"{m.group(2)}-{mon:02d}"
    # 3) 年 + 月份名
    m = re.search(rf"(20\d{{2}})[\-_\s\.]*({names})\b", t)
    if m:
        mon = _MONTHS.get(m.group(2)) or _MONTHS_ABBR.get(m.group(2))
        if mon:
            return f"{m.group(1)}-{mon:02d}"
    return None


def _valid_ym(ym: Optional[str]) -> bool:
    if not ym:
        return False
    m = re.match(r"^(\d{4})-(\d{2})$", ym)
    if not m:
        return False
    return 1 <= int(m.group(2)) <= 12


def _dedup_links(pairs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """按 ym 去重 (首个 url 保留), 排序 ym 递增."""
    seen: Dict[str, str] = {}
    for ym, url in pairs:
        if not _valid_ym(ym) or not url:
            continue
        seen.setdefault(ym, url)
    return sorted(seen.items())


def _fetch(url: str, timeout: int = DEFAULT_HTTP_TIMEOUT) -> Optional[str]:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and r.text:
            return r.text
    except Exception:
        pass
    return None


def _curl(url: str, timeout: int = DEFAULT_HTTP_TIMEOUT) -> Optional[str]:
    """bash curl 兜底 (绕 MCP 代理拦, 概念抄 strategies.py:134)."""
    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), "-A", "Mozilla/5.0", url],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        return r.stdout if r.returncode == 0 and r.stdout else None
    except Exception:
        return None


# ---------- L1: Gemini 联网找归档页 + 解析 ----------

def _load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text()


def _parse_json_response(text: str) -> Optional[Dict[str, Any]]:
    """从模型回文剥出第一个 JSON 对象, 失败返回 None."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def find_archive_via_search(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    *,
    client: Optional[Client] = None,
) -> ArchivePointer:
    """L1 第一步: Gemini 联网找归档页 URL. 返回 ArchivePointer.

    搜索会降级到 gemini-2.5-flash (中转限制). 不带 PDF, 纯文本+web_search 工具.
    """
    if client is None:
        client = Client()
    tmpl = _load_prompt("find_archive.md")
    prompt = (
        tmpl.replace("{fund_name}", fund_name)
            .replace("{issuer}", issuer)
            .replace("{issuer_domain}", issuer_domain or "")
            .replace("{asx_code}", asx_code or "")
    )
    resp = client.messages_with_search(prompt, max_tokens=1024, max_uses=5)
    obj = _parse_json_response(resp.text) or {}
    return ArchivePointer(
        archive_url=obj.get("archive_url"),
        pagination_param=obj.get("pagination_param"),
        no_archive=bool(obj.get("no_archive")),
        latest_pdf_url=obj.get("latest_pdf_url"),
        issuer_domain_confirmed=obj.get("issuer_domain_confirmed"),
        evidence=str(obj.get("evidence") or ""),
        raw=obj,
    )


def parse_archive_page(
    html: str,
    *,
    client: Optional[Client] = None,
) -> Tuple[List[Tuple[str, str]], bool, str, int]:
    """L1 第二步: 把已抓好的归档页交 Gemini 解析.

    返回 (links, has_more_pages, next_page_hint, unparseable_count).
    """
    if client is None:
        client = Client()
    prompt = _load_prompt("parse_archive.md") + "\n\n---PAGE---\n" + html[:80_000]
    resp = client.messages(prompt, max_tokens=4096)
    obj = _parse_json_response(resp.text) or {}
    raw_links = obj.get("links") or []
    pairs: List[Tuple[str, str]] = []
    for item in raw_links:
        if not isinstance(item, dict):
            continue
        ym = item.get("ym")
        url = item.get("url")
        if ym and url:
            pairs.append((str(ym), str(url)))
    return (
        _dedup_links(pairs),
        bool(obj.get("has_more_pages")),
        str(obj.get("next_page_hint") or ""),
        int(obj.get("unparseable_count") or 0),
    )


def probe_l1_official(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    *,
    client: Optional[Client] = None,
    max_pagination: int = 8,
) -> Tuple[List[Tuple[str, str]], ArchivePointer, int]:
    """L1 完整流程: 联网找页 -> fetch -> Gemini 解析 -> 若分页则遍历.

    返回 (links, pointer, unparseable_total).
    """
    if client is None:
        client = Client()
    pointer = find_archive_via_search(fund_name, issuer, issuer_domain, asx_code, client=client)
    if not pointer.archive_url:
        return ([], pointer, 0)

    aggregate: List[Tuple[str, str]] = []
    unparseable_total = 0

    def _one_page(url: str) -> Tuple[List[Tuple[str, str]], bool, str, int]:
        html = _fetch(url) or _curl(url) or ""
        if not html:
            return ([], False, "", 0)
        return parse_archive_page(html, client=client)

    links, has_more, _hint, uc = _one_page(pointer.archive_url)
    aggregate.extend(links)
    unparseable_total += uc

    # 分页遍历 (仅当 Gemini 明确报了 pagination_param)
    if pointer.pagination_param and has_more:
        for page in range(2, max_pagination + 1):
            sep = "&" if "?" in pointer.archive_url else "?"
            page_url = f"{pointer.archive_url}{sep}{pointer.pagination_param}={page}"
            more, has_more2, _, uc2 = _one_page(page_url)
            unparseable_total += uc2
            if not more:
                break
            before = len(aggregate)
            aggregate.extend(more)
            if len(_dedup_links(aggregate)) == before:  # 无新月份 = 到底
                break
            if not has_more2:
                break

    return (_dedup_links(aggregate), pointer, unparseable_total)


# ---------- L2: Wayback CDX ----------

def probe_l2_wayback(
    issuer_domain: str,
    gap_set: Set[str],
) -> List[Tuple[str, str]]:
    """L2: 用 CDX API 查 issuer_domain 快照, 从 original URL 提月份补 gap_set 中的洞.

    每月最多 CDX_SNAPSHOTS_PER_MONTH 快照 (抄自 strategies.py 补1).
    """
    if not gap_set or not issuer_domain:
        return []
    patterns = [f"{issuer_domain}/*", f"{issuer_domain}/wp-content/uploads/*"]
    snap_count: Dict[str, int] = {}
    hits: List[Tuple[str, str]] = []
    for pat in patterns:
        # https 端更稳; http 通常也可, 但本机可能被拦
        api = (
            f"https://web.archive.org/cdx/search/cdx?url={pat}"
            f"&output=json&fl=timestamp,original,statuscode"
            f"&filter=statuscode:200&filter=mimetype:application/pdf"
            f"&limit=500"
        )
        out = _curl(api, timeout=30)
        if not out:
            continue
        try:
            arr = json.loads(out)
        except json.JSONDecodeError:
            continue
        for row in arr[1:]:  # 首行表头
            if len(row) < 2:
                continue
            ts, original = row[0], row[1]
            ym = _parse_ym_from_text(original)
            if not ym or ym not in gap_set:
                continue
            if snap_count.get(ym, 0) >= CDX_SNAPSHOTS_PER_MONTH:
                continue
            snap_count[ym] = snap_count.get(ym, 0) + 1
            wayback_url = f"https://web.archive.org/web/{ts}/{original}"
            hits.append((ym, wayback_url))
    return _dedup_links(hits)


# ---------- L3: fundmonitors AJAX ----------

def probe_l3_fundmonitors(
    fundmonitors_url: str,
) -> Optional[Dict[str, Any]]:
    """L3: fundmonitors Full Fund Profile 页面里含逐月表 (AJAX 或内嵌 HTML).

    该层不直出 PDF 链接 (它是逐月收益率表, 不是 PDF), 而是产出 records=[(YYYY-MM-DD, ret), ...],
    供 ingest 直接入库 (跳过 extract). 付费墙立即返回 None.

    返回 None = 抓不到 / 付费墙; dict = {"records": [...], "source": url}.
    """
    if not fundmonitors_url:
        return None
    html = _fetch(fundmonitors_url) or _curl(fundmonitors_url)
    if not html:
        return None
    low = html.lower()
    if "must be logged in" in low or "premium" in low or "paywall" in low:
        return None
    # 找 Historical Performance 表: 简单启发式 "YYYY-MM-DD? \s+ float%"
    # 真实 fundmonitors 页面结构见 skills/lib/extract.py:1195 parse_html_monthly_table,
    # 此处只做占位 (Phase 2 若目标是"L1 覆盖为主, L3 只保底"): 返回 None 让上层降级.
    return None


# ---------- 主入口 ----------

def _recent_published_month() -> str:
    today = datetime.date.today()
    y, m = today.year, today.month
    m -= 2
    if m < 1:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _month_range(lower: str, upper: str) -> List[str]:
    """[lower..upper] YYYY-MM 递增."""
    ly, lm = int(lower[:4]), int(lower[5:7])
    uy, um = int(upper[:4]), int(upper[5:7])
    out = []
    y, m = ly, lm
    while (y, m) <= (uy, um):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m = 1
            y += 1
    return out


def run_discovery(
    fund_name: str,
    issuer: str,
    fund_id: str,
    *,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    inception_ym: Optional[str] = None,
    latest_ym: Optional[str] = None,
    client: Optional[Client] = None,
) -> DiscoveryReport:
    """L1 -> L2 (补洞) -> L3 (占位). 集合差驱动.

    inception_ym / latest_ym: 期望区间. inception_ym 若未给, 由 L1 结果反推 (取最早月).
    """
    if client is None:
        client = Client()
    upper = latest_ym or _recent_published_month()

    report = DiscoveryReport(fund_id=fund_id)
    obtained: Set[str] = set()
    aggregate: List[Tuple[str, str]] = []

    # L1
    l1_links, pointer, unp = probe_l1_official(
        fund_name, issuer, issuer_domain, asx_code, client=client,
    )
    report.archive_pointer = pointer
    report.unparseable_count = unp
    if l1_links:
        obtained.update(ym for ym, _ in l1_links)
        aggregate.extend(l1_links)
        report.per_level_contribution["L1"] = len(l1_links)
        report.per_level_source["L1"] = pointer.archive_url or ""
    report.evidence_log.append({
        "level": "L1", "count": len(l1_links),
        "archive_url": pointer.archive_url,
        "no_archive": pointer.no_archive,
        "evidence": pointer.evidence,
    })

    # 期望区间 (确定下界)
    if not inception_ym and l1_links:
        inception_ym = min(ym for ym, _ in l1_links)
    if inception_ym and _valid_ym(inception_ym):
        expected = set(_month_range(inception_ym, upper))
    else:
        expected = set()

    # L2 补洞 (只在有 issuer_domain + 有期望范围 + 有缺口)
    domain = pointer.issuer_domain_confirmed or issuer_domain
    if expected:
        gap_set = expected - obtained
        if gap_set and domain:
            # 去 scheme
            dom_clean = re.sub(r"^https?://", "", domain).rstrip("/")
            l2_links = probe_l2_wayback(dom_clean, gap_set)
            l2_new = [(ym, url) for ym, url in l2_links if ym not in obtained]
            if l2_new:
                obtained.update(ym for ym, _ in l2_new)
                aggregate.extend(l2_new)
                report.per_level_contribution["L2"] = len(l2_new)
                report.per_level_source["L2"] = "web.archive.org"
            report.evidence_log.append({"level": "L2", "gap_before": len(gap_set),
                                        "new": len(l2_new), "domain": dom_clean})

    # L3 (占位: 当前 return None, 保留接口)
    # 未来接入: 主会话或 discover CLI 传 fundmonitors_url, 走 records 直入库

    report.links = _dedup_links(aggregate)
    report.obtained = sorted(obtained)
    report.gaps = sorted(expected - obtained) if expected else []
    return report
