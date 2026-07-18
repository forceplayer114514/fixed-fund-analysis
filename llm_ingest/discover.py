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
from urllib.parse import urljoin

import requests

from . import extract as ex_mod
from .client import Client, resolve_sources
from .tavily import TavilyError, multi_query_search

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
    search_sources: List[str] = field(default_factory=list)  # grounding 展开后的真实 URL
    search_queries: List[str] = field(default_factory=list)
    # v2 (discover2.find_archive_v2) 抓页时已直接看见的所有 PDF URL. v1 不填。
    # run_discovery 若发现此字段非空, 优先用它反解 ym, 跳过再让 Gemini 解析归档页。
    discovered_pdfs: List[str] = field(default_factory=list)


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
    # 2) 月份名 + 年 (\b 边界不识别 _underscore, 用 (?:\b|_) 兼容 Stake 命名)
    names = "|".join(list(_MONTHS.keys()) + list(_MONTHS_ABBR.keys()))
    m = re.search(rf"(?:\b|_)({names})[\-_\s\.]*(20\d{{2}})", t)
    if m:
        mon = _MONTHS.get(m.group(1)) or _MONTHS_ABBR.get(m.group(1))
        if mon:
            return f"{m.group(2)}-{mon:02d}"
    # 2b) 月份名 + 2 位年 (Stake: AccumulateReport_January26.pdf)
    m = re.search(rf"(?:\b|_)({names})[\-_\s\.]*(\d{{2}})(?!\d)", t)
    if m:
        mon = _MONTHS.get(m.group(1)) or _MONTHS_ABBR.get(m.group(1))
        if mon:
            yr2 = int(m.group(2))
            # 只接受 19..30 范围内的 2 位年 (2019-2030 有效)
            if 19 <= yr2 <= 30:
                return f"20{yr2:02d}-{mon:02d}"
    # 3) 年 + 月份名 (同样兼容 _)
    m = re.search(rf"(20\d{{2}})[\-_\s\.]*({names})(?:\b|_)", t)
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
    """浏览器渲染抓页 (playwright chromium headless).

    默认走 playwright — 覆盖 AJAX/SPA 归档 (GCI 的 wp-load-posts, JCB 表格等).
    静态站也能过, 就是慢一些 (~2s launch + ~1s render). Phase 2 不做二级兜底,
    强的功能优先 (用户指令).
    network_idle=True 等到 500ms 无网络请求, 保证 AJAX 加载完成.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        # 未装 playwright, 退回 requests (静态站仍可用)
        return _fetch_requests(url, timeout)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
                return page.content()
            finally:
                browser.close()
    except Exception:
        # 页 load 超时/浏览器崩溃 -> 退 requests
        return _fetch_requests(url, timeout)


def _fetch_requests(url: str, timeout: int = DEFAULT_HTTP_TIMEOUT) -> Optional[str]:
    """静态 requests 抓页 (备用: playwright 不可用或崩溃时)."""
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


def _same_host(a: str, b: str) -> bool:
    """比域名, 忽略 www./协议差异."""
    from urllib.parse import urlparse
    ha = urlparse(a).netloc.lower().lstrip("www.").removeprefix("www.")
    hb = urlparse(b).netloc.lower().lstrip("www.").removeprefix("www.")
    return bool(ha) and ha == hb


def _validate_url_in_sources(url: Optional[str], sources: List[str]) -> bool:
    """URL 的域必须出现在 sources 里 (即真被搜索命中过, 而非模型幻觉)."""
    if not url:
        return False
    return any(_same_host(url, s) for s in sources)


def _pick_issuer_domain(sources: List[str], issuer: str, fund_name: str) -> Optional[str]:
    """从 sources 里挑最像发行商官网的域. 启发式: 域名含发行商关键词, 排聚合站."""
    from urllib.parse import urlparse
    tokens = re.findall(r"[a-z]+", (issuer + " " + fund_name).lower())
    tokens = [t for t in tokens if len(t) >= 4 and t not in {"fund", "capital", "management", "australia", "trust", "income", "asset", "monthly"}]
    excludes = {"morningstar.com", "morningstar.com.au", "lonsec.com.au", "fundmonitors.com",
                "asx.com.au", "yahoo.com", "reuters.com", "bloomberg.com",
                "linkedin.com", "wikipedia.org", "google.com", "youtube.com"}
    def _host(u: str) -> str:
        h = urlparse(u).netloc.lower()
        if h.startswith("www."):
            h = h[4:]
        return h
    for s in sources:
        host = _host(s)
        if not host or host in excludes:
            continue
        if any(tok in host for tok in tokens):
            return f"https://{host}"
    for s in sources:
        host = _host(s)
        if host and host not in excludes:
            return f"https://{host}"
    return None


def _search_and_resolve(
    client: Client,
    query_prompt: str,
    max_uses: int = 6,
) -> Tuple[List[str], List[str]]:
    """一次搜索调用 -> (真实 URL, queries). 短明 prompt 强触发工具."""
    resp = client.messages_with_search(query_prompt, max_tokens=2048, max_uses=max_uses)
    reals = resolve_sources(resp.search_sources) if resp.search_sources else []
    return reals, list(resp.search_queries)


def _search_with_retry(
    client: Client, prompts: List[str], max_tries: int = 3,
) -> Tuple[List[str], List[str]]:
    """轮流试多组 prompt, 拿到 sources 立即返回. sub2api web_search 触发是概率性的."""
    all_queries: List[str] = []
    for prompt in prompts:
        for _ in range(max_tries):
            sources, queries = _search_and_resolve(client, prompt)
            all_queries.extend(queries)
            if sources:
                return sources, all_queries
    return [], all_queries


def find_archive_via_search(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    *,
    client: Optional[Client] = None,
) -> ArchivePointer:
    """L1 阶段 A (搜索) + 阶段 B (判 JSON).

    sub2api 的 web_search 触发是概率性的 — 用多个显式子问 + [SOURCE] 标记 prompt
    实测触发率高; 触发失败时重试并换 prompt 模板 (`_search_with_retry`)。
    信 grounding sources 展开后的 URL, 不信文字回答里生成的 URL (幻觉高发)。
    """
    if client is None:
        client = Client()

    # --- 阶段 A: 搜索 ---
    # 首选 Tavily REST 一次调用给结构化真 URL, 无 grounding 中间层, 无幻觉。
    # Tavily 失败 (key 缺 / 网错 / 全 query 空返) 再降级 sub2api web_search。
    real_sources: List[str] = []
    queries: List[str] = []
    try:
        tavily_queries = [
            fund_name,
            f"{fund_name} performance",
            f"{fund_name} monthly report",
        ]
        real_sources = multi_query_search(
            tavily_queries,
            max_results_per_query=5,
            exclude_aggregators=True,
        )
        queries = tavily_queries if real_sources else []
    except TavilyError:
        real_sources = []

    if not real_sources:
        # --- 回退: sub2api web_search (英文简洁 prompt, 强制触发) ---
        # 教训:
        # - 中文 prompt: Gemini 只跑 1 条 vague query 且返幻觉 URL (澳洲基金语料全英)
        # - 全裸 [SOURCE] <url>: 只出 grounding-redirect 链, 中国网络下 follow 超时
        # 现改: 英文 + 强制 markdown link 格式 `[hostname](url)`, label 是纯域名
        # -> _parse_grounding 走 label 短路, 免走 Google 直连
        p_variants = [
            (
                f"Use web_search TWICE:\n"
                f"1. Query: {fund_name}\n"
                f"2. Query: {fund_name} performance\n"
                f"Return 5 URLs per query, format each as markdown link `[hostname](url)`."
            ),
            (
                f"Use web_search TWICE:\n"
                f"1. Query: {fund_name} monthly report\n"
                f"2. Query: {fund_name} fact sheet\n"
                f"Return 5 URLs per query, format each as markdown link `[hostname](url)`."
            ),
        ]
        real_sources, queries = _search_with_retry(client, p_variants, max_tries=2)

    # --- 阶段 B: 让 Gemini 读 sources 判 JSON ---
    domain = _pick_issuer_domain(real_sources, issuer, fund_name) if real_sources else issuer_domain
    ptr_json: Dict[str, Any] = {}
    if real_sources:
        tmpl = _load_prompt("find_archive.md")
        prompt = (
            tmpl.replace("{fund_name}", fund_name)
                .replace("{issuer}", issuer)
                .replace("{issuer_domain}", domain or issuer_domain or "")
                .replace("{asx_code}", asx_code or "")
        )
        prompt += "\n\n---搜索结果 (真实展开后 URL) ---\n"
        prompt += "\n".join(f"- {u}" for u in real_sources)
        # max_tokens 1024 曾致 Gemini "思考+JSON" 混排被截半 (Yarra 阶段 B 观察);
        # 提到 2048 给足余量。若模型输长中文推理再吐 JSON, 短 budget 直接把 JSON 截掉。
        resp = client.messages(prompt, max_tokens=2048)
        ptr_json = _parse_json_response(resp.text) or {}

    llm_archive = ptr_json.get("archive_url")
    llm_latest = ptr_json.get("latest_pdf_url")
    llm_domain = ptr_json.get("issuer_domain_confirmed") or domain

    # 交叉验证仍要跑 (Gemini 会不听话即使 sources 摆在面前)
    archive_url = llm_archive if _validate_url_in_sources(llm_archive, real_sources) else None
    latest_pdf_url = llm_latest if _validate_url_in_sources(llm_latest, real_sources) else None
    final_domain = llm_domain if _validate_url_in_sources(llm_domain, real_sources) else domain

    # 兜底: 无 archive/latest 但有 sources, 挑相关度最高的
    if not archive_url and not latest_pdf_url and real_sources:
        # 优先 PDF
        for s in real_sources:
            if s.lower().endswith(".pdf"):
                latest_pdf_url = s
                break
        # 其次 domain 下, path 匹配基金关键词的
        if not latest_pdf_url and final_domain:
            fund_tokens = re.findall(r"[a-z]+", fund_name.lower())
            fund_tokens = [t for t in fund_tokens if len(t) >= 4 and t not in {"fund", "trust", "capital"}]
            best_score = -1
            best_url = None
            for s in real_sources:
                if not _same_host(s, final_domain):
                    continue
                from urllib.parse import urlparse
                path_low = urlparse(s).path.lower()
                score = sum(1 for t in fund_tokens if t in path_low)
                if any(x in path_low for x in ("/contact", "/about", "/careers", "/legal", "/privacy", "/insights")):
                    score -= 2
                if score > best_score:
                    best_score = score
                    best_url = s
            if best_url:
                latest_pdf_url = best_url

    return ArchivePointer(
        archive_url=archive_url,
        pagination_param=ptr_json.get("pagination_param"),
        no_archive=bool(ptr_json.get("no_archive")) or (archive_url is None),
        latest_pdf_url=latest_pdf_url,
        issuer_domain_confirmed=final_domain,
        evidence=str(ptr_json.get("evidence") or ""),
        raw=ptr_json,
        search_sources=real_sources,
        search_queries=queries,
    )


_YM_RE = re.compile(r"^\d{4}-\d{2}$")
_HREF_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\']', re.I)

# 噪声过滤: 归档页里除月报外还常挂 PDS/TMD/FSG/研究报告等静态 PDF, 靠日期匹配后仍
# 会混入 (如 "TMD-...-11-October-2023.pdf" 匹到 2023-10)。这些不是月度业绩报告,
# 摄取阶段跑 extract 必然 not_found 或 measure 错。用文件名黑名单字面预筛。
_NON_MONTHLY_HINTS = re.compile(
    r"(pds|tmd|target[-_]market|fsg|financial[-_]services|whistle|"
    r"research|lonsec|morningstar|zenith|genium|platform|"
    r"fact[-_]sheet|application|additional[-_]information|"
    r"policy|guide|dictionary|handbook)",
    re.I,
)


def _extract_href_whitelist(html: str, base_url: str) -> set:
    """从 HTML 抽所有 `<a href="...">` 归一化后的绝对 URL 白名单."""
    out = set()
    for href in _HREF_RE.findall(html):
        try:
            full = urljoin(base_url, href).strip()
        except Exception:  # noqa: BLE001
            continue
        out.add(full)
        out.add(full.lower())
    return out


def _extract_pdf_links_by_regex(html: str, base_url: str) -> List[Tuple[str, str]]:
    """代码正则直抠归档页 `<a href>` 里的 PDF 链接 -> (ym, url), 无长度截断.

    parse_archive_page 原来无条件把 html 截到 80KB 才让 LLM 解析 -- 归档页头部
    (导航/脚本/样式) 一旦超过这个长度 (Stake 归档页实测 149KB, 全部 34 个 PDF 链接
    落在 8.3~14 万字节区间), LLM 一个链接都看不到, 返回 0 links。且固定截断长度
    基金历史越长越注定失效 (10年~120份月报, 20年~240份, 提大截断上限只是治标)。
    正则扫全文 (无长度上限, 开销可忽略), 命中直接用, parse_archive_page 只在正则
    一无所获时才退回 LLM (兜底 JS 渲染后才注入的隐藏链接等场景)。
    """
    pairs: List[Tuple[str, str]] = []
    for href in _HREF_RE.findall(html):
        full = urljoin(base_url, href)
        if not full.lower().split("?", 1)[0].endswith(".pdf"):
            continue
        fname = full.rsplit("/", 1)[-1]
        if _NON_MONTHLY_HINTS.search(fname):
            continue
        # 只信文件名反解 ym, 不 fallback 到整段 URL -- 托管路径常带上传日期/年份目录
        # (如 "/wp-content/uploads/2024/02/xxx.pdf" 是 2024-02 上传, 不是月报期数),
        # 对全 URL 兜底会把无关域名的静态文件误判成月报 (实测: Stake 页混入
        # finclear.com.au 的条款 PDF, 因路径含 "/2024/02/" 被误判为 2024-02)。
        ym = _parse_ym_from_text(fname)
        if ym and _valid_ym(ym):
            pairs.append((ym, full))
    return _dedup_links(pairs)


_PAGINATION_HINT_RE = re.compile(r"(load\s*more|next\s*page|archiveyear|[?&]page=)", re.I)


def parse_archive_page(
    html: str,
    *,
    client: Optional[Client] = None,
    base_url: str = "",
) -> Tuple[List[Tuple[str, str]], bool, str, int]:
    """L1 第二步: 把已抓好的归档页解析出 PDF 链接.

    优先代码正则直抠全文 (`_extract_pdf_links_by_regex`, 无长度截断限制) -- 命中
    则直接返回, 跳过 LLM (省 token, 且不受下面 80KB 截断影响, 对长历史基金友好)。
    正则一无所获才退回 LLM (兜底 JS 渲染后才注入链接 / 无 <a href> 结构等场景)。

    白名单校 (Spec E.2.C, 仅 LLM 路径需要):
      - ym 格式 YYYY-MM 强校
      - url 必须在 html 的 <a href="..."> 白名单里 (代码校, LLM 造 URL 被丢)

    返回 (links, has_more_pages, next_page_hint, unparseable_count)。
    代码正则路径的 has_more_pages 是启发式猜测 (页内是否有分页/load-more 字样),
    不像 LLM 路径那样能读懂具体怎么翻页, next_page_hint 恒为空串。
    """
    if base_url:
        code_pairs = _extract_pdf_links_by_regex(html, base_url)
        if code_pairs:
            has_more = bool(_PAGINATION_HINT_RE.search(html))
            return (code_pairs, has_more, "", 0)

    if client is None:
        client = Client()
    prompt = _load_prompt("parse_archive.md") + "\n\n---PAGE---\n" + html[:80_000]
    resp = client.messages(prompt, max_tokens=4096)
    obj = _parse_json_response(resp.text) or {}
    raw_links = obj.get("links") or []
    whitelist = _extract_href_whitelist(html, base_url) if base_url else None
    pairs: List[Tuple[str, str]] = []
    dropped = 0
    for item in raw_links:
        if not isinstance(item, dict):
            continue
        ym = item.get("ym")
        url = item.get("url")
        if not ym or not url:
            continue
        ym_s = str(ym).strip()
        url_s = str(url).strip()
        if not _YM_RE.match(ym_s):
            dropped += 1
            continue
        if whitelist is not None:
            if url_s not in whitelist and url_s.lower() not in whitelist:
                dropped += 1
                continue
        pairs.append((ym_s, url_s))
    unparseable = int(obj.get("unparseable_count") or 0) + dropped
    return (
        _dedup_links(pairs),
        bool(obj.get("has_more_pages")),
        str(obj.get("next_page_hint") or ""),
        unparseable,
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
    """L1 完整流程: 找归档页 -> fetch -> Gemini 解析 -> 若分页则遍历.

    优先走 v2 (discover2.find_archive_v2): Tavily 排序 + Scrapling 抓 + PDF 打样验证。
    v2 未定位到归档且未定位到最新单份时, 回退 v1 (find_archive_via_search) 兜底。

    返回 (links, pointer, unparseable_total).
    """
    if client is None:
        client = Client()
    # v2 首选
    from . import discover2 as d2
    pointer = d2.find_archive_v2(fund_name, issuer, issuer_domain, asx_code, client=client)
    # v2 完全空 (无 archive 且无 latest_pdf) 时降级 v1 (可能 v1 web_search 拿到 v2 没找到的 URL)
    if not pointer.archive_url and not pointer.latest_pdf_url:
        pointer = find_archive_via_search(fund_name, issuer, issuer_domain, asx_code, client=client)

    # v2 快路: pointer.discovered_pdfs 非空说明抓页时已看见全部 PDF URL, 直接用文件名
    # 反解 ym (URL slug 里带 "-May-2026" / "202603" 等), 免再让 Gemini 解析一遍归档页
    # (Yarra 教训: 归档页 HTML 前 80k 字符 Gemini 判月份返 0 links)。反解失败的丢, 但保留
    # 至少 latest_pdf_url 作为兜底 -- 只要有 1 份能反解出 ym 都强于让 Gemini 二解。
    if pointer.discovered_pdfs:
        # 噪声过滤 (模块级 _NON_MONTHLY_HINTS): 归档页里除月报外还常挂 PDS/TMD/FSG/
        # 研究报告等静态 PDF, 靠日期匹配后仍会混入 (如 "TMD-...-11-October-2023.pdf"
        # 匹到 2023-10)。这些不是月度业绩报告, 摄取阶段跑 extract 必然 not_found 或
        # measure 错。用文件名黑名单字面预筛。
        pairs: List[Tuple[str, str]] = []
        for u in pointer.discovered_pdfs:
            fname = u.rsplit("/", 1)[-1]
            if _NON_MONTHLY_HINTS.search(fname):
                continue
            ym = _parse_ym_from_text(fname) or _parse_ym_from_text(u)
            if ym and _valid_ym(ym):
                pairs.append((ym, u))
        if pairs:
            return (_dedup_links(pairs), pointer, 0)
        # 全反解失败 -> 单份兜底 (latest_pdf_url 已过 v2 PDF 打样, ym 由 extract 阶段的 PDF 文本决定)
        if pointer.latest_pdf_url:
            ym = _parse_ym_from_text(pointer.latest_pdf_url.rsplit("/", 1)[-1]) or ""
            if ym and _valid_ym(ym):
                return ([(ym, pointer.latest_pdf_url)], pointer, 0)

    # 无 archive_url: 单份场景, 只有 latest_pdf_url 时直接返
    if not pointer.archive_url:
        if pointer.latest_pdf_url:
            ym = _parse_ym_from_text(pointer.latest_pdf_url.rsplit("/", 1)[-1]) or ""
            if ym and _valid_ym(ym):
                return ([(ym, pointer.latest_pdf_url)], pointer, 0)
        return ([], pointer, 0)

    aggregate: List[Tuple[str, str]] = []
    unparseable_total = 0

    def _one_page(url: str) -> Tuple[List[Tuple[str, str]], bool, str, int]:
        html = _fetch(url) or _curl(url) or ""
        if not html:
            return ([], False, "", 0)
        return parse_archive_page(html, client=client, base_url=url)

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

    # L1.5 (Spec C1): archive_url 或 latest_pdf_url 非空但 aggregate 空 -> 页可能是
    # "中转入口页" (Stake 场景: hellostake.com/.../performance-updates-and-statements/...
    # 挂 <a href="/legal/monthly-performance-report">, 二级链接才有 PDF), 走 navigate
    # 层让 Gemini 挑同域内链再抓 1 跳. no_archive 状态不再作 gate -- Stake v1 会判
    # no_archive=True 但页确含月报入口内链.
    _l15_seed = pointer.archive_url or pointer.latest_pdf_url
    if not l1_links and _l15_seed:
        try:
            from .navigate import navigate_one_hop, MAX_TOTAL_FETCHES
            from .discover2 import (
                _rank_pdfs_by_name_match, _pdf_slug_match_count,
                confirm_pdf_is_monthly_report,
            )
            visited: Set[str] = {_l15_seed}
            nav_hops: List[Tuple[str, str]] = []
            # 抓归档页全 HTML (parse_archive_page 那步用过, 但没保存下来)
            arch_html = _fetch(_l15_seed) or ""
            if arch_html:
                next_url, _next_html, next_pdfs = navigate_one_hop(
                    _l15_seed, arch_html, fund_name,
                    pointer.issuer_domain_confirmed or issuer_domain,
                    visited, client,
                )
                nav_hops.append((_l15_seed, next_url or "(no_pick)"))
                if next_pdfs:
                    next_pdfs = _rank_pdfs_by_name_match(next_pdfs, fund_name)
                    # 尝试直接从文件名反解 ym (跳过 Gemini PDF 打样, 大批量场景省 tokens)
                    # 噪声过滤复用模块级 _NON_MONTHLY_HINTS
                    nav_pairs: List[Tuple[str, str]] = []
                    for u in next_pdfs:
                        fname = u.rsplit("/", 1)[-1]
                        if _NON_MONTHLY_HINTS.search(fname):
                            continue
                        ym = _parse_ym_from_text(fname) or _parse_ym_from_text(u)
                        if ym and _valid_ym(ym):
                            nav_pairs.append((ym, u))
                    if nav_pairs:
                        obtained.update(ym for ym, _ in nav_pairs)
                        aggregate.extend(nav_pairs)
                        report.per_level_contribution["L1_nav"] = len(nav_pairs)
                        report.per_level_source["L1_nav"] = next_url or ""
                        # 更新 pointer 让下游知道真归档在哪
                        pointer.archive_url = next_url
                        pointer.discovered_pdfs = list(next_pdfs)
                        pointer.raw["nav_hops"] = nav_hops
                    report.evidence_log.append({
                        "level": "L1_nav", "count": len(nav_pairs),
                        "from_url": pointer.archive_url if not nav_pairs else "(navigated)",
                        "to_url": next_url,
                        "evidence": f"导航 1 跳, {len(next_pdfs)} 份 PDF, {len(nav_pairs)} 份反解 ym",
                    })
                else:
                    report.evidence_log.append({
                        "level": "L1_nav", "count": 0, "to_url": next_url,
                        "evidence": "导航 1 跳后仍无 PDF",
                    })
        except Exception as e:  # noqa: BLE001
            report.evidence_log.append({"level": "L1_nav", "error": str(e)})

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

    # L2.6 (Spec C1): 全网 discovery 空手 -> 扫本地 pdf_cache 兜底.
    # 场景: GCI 88 份 PDF 已在 data/pdf_cache/gryphon_capital_income/*.pdf, Y.5 wipe
    # DB 后 discovery 若返 0 links, 这些现成 PDF 就没人管. 靠文件名反解 ym 作 links.
    # url 用 file:// 让下游 (cli/routers.ingest) 跳过下载但仍走两道闸 -- 不绕闸.
    if not aggregate:
        cache_dir = Path(__file__).resolve().parent.parent / "data" / "pdf_cache" / fund_id
        if cache_dir.exists():
            local_pairs: List[Tuple[str, str]] = []
            for p in sorted(cache_dir.glob("*.pdf")):
                ym = _parse_ym_from_text(p.stem)
                if ym and _valid_ym(ym):
                    local_pairs.append((ym, f"file://{p.absolute()}"))
            if local_pairs:
                aggregate.extend(local_pairs)
                obtained.update(ym for ym, _ in local_pairs)
                report.per_level_contribution["L_local"] = len(local_pairs)
                report.per_level_source["L_local"] = str(cache_dir)
                report.evidence_log.append({
                    "level": "L_local", "count": len(local_pairs),
                    "cache_dir": str(cache_dir),
                    "evidence": "全网 discovery 未产出, 回退本地 PDF 缓存",
                })

    report.links = _dedup_links(aggregate)
    report.obtained = sorted(obtained)
    report.gaps = sorted(expected - obtained) if expected else []
    return report
