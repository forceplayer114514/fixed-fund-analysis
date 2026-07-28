"""discover2: 归档页发现 v2 -- 搜索排序 + 抓页 + LLM 判链接清单.

问题背景 (discover.py v1 的坑):
  阶段 B 只喂 Gemini URL 字面串, 让它凭 slug 猜"归档 vs 单份"。
  Yarra 一测: Tavily 拿到 yarracm.com/{capabilities/enhanced-income, performance}
  两个候选, Gemini 挑了前者 (营销页, 无 PDF), 忽略后者 (真归档)。
  信息量根本不够 -- Gemini 语义猜错必然发生。

v2 流程 (三步, LLM 只做能力内的活):
  1. 搜索拿 URL (Grok 直答归档页, 或 Tavily 检索 + Gemini 排序)
  2. **_fetch top-N 并发** -- 抓 HTML (playwright/requests), 代码列出页内全部 PDF 链接
  3. **discover.classify_pdf_links** -- 把链接清单编号交 LLM 判"哪几条是本基金
     月报、各是哪个月"。答案非空即认定该页为归档页, 且月报清单同时到手。

关键决策:
  - LLM 只做语义判断 (排序 URL / 判链接归属与月份), URL 一律由代码从真实抓到的
    页面里取, LLM 只能回编号, 不允许产出 URL。
  - top-N 并发抓 (ThreadPoolExecutor), 谁先判出本基金月报谁赢。
  - "哪些是月报" 只在 classify_pdf_links 一处判。原来这里另有一步下载整份 PDF
    上传打样, 且挑哪份打样要靠文件名打分 -- 打分挑错就整页误杀 (2026-07 Stake
    事故), 已删。

依赖复用: discover.py 的 _fetch / classify_pdf_links / ArchivePointer / _same_host
"""
from __future__ import annotations

import concurrent.futures
import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from . import discover as disc_mod
from .client import Client
from .discover import (
    ArchivePointer,
    _fetch,
    _pick_issuer_domain,
    _same_host,
)
from .search import TavilyError, multi_query_search

# ---- 常量 ----
TOP_N_PROBE = 4          # 排序后取 top-N 并发探测
FETCH_TIMEOUT = 30       # 单次抓页超时
PROBE_CONCURRENCY = 4    # 并发线程池

# ---- PDF 链接抽取 ----
# 抓到 HTML 后, 认下面路径特征算 PDF 候选:
#  (a) .pdf 直链
#  (b) href 里含 report/factsheet/monthly/performance 且是链接文本或路径特征
_PDF_HREF_RE = re.compile(r'<a[^>]+href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', re.I)
_HTML_LINK_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>', re.I)
_MONTHLY_HINTS = re.compile(
    r"(monthly|month-end|fund\s*report|fact\s*sheet|performance|report|update)",
    re.I,
)


def _extract_pdf_links(html: str, base_url: str) -> List[str]:
    """抓页 HTML -> `<a href="*.pdf">` 绝对 URL 列表 (去重保序).

    与 discover.extract_all_pdf_links 同义, 保留本名供既有调用方 (navigate) 用。
    """
    seen: set = set()
    out: List[str] = []
    for href in _PDF_HREF_RE.findall(html):
        full = urljoin(base_url, href)
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


def _extract_monthlyish_page_links(html: str, base_url: str) -> List[str]:
    """抓页 HTML -> 链接文字含月报关键词但**不是 .pdf** 的网页链接 (去重保序).

    这些是"可能通往月报的中转页", 只能当导航起跳点, 绝不可混进 PDF 清单交
    classify_pdf_links 判定 (2026-07-29 事故): Stake 那个 Zendesk 支持页抓下来
    0 条 .pdf, 本函数这一路却给出两条普通网页 --
      https://hellostake.com/legal/monthly-performance-report   (真归档页本身)
      https://hellostake.com/au/ambition-report-2025            (营销页)
    原来两路混在一个列表里返回, 判定函数把第二条当成月报 (模型从 "-2025" 里
    看到年份, 自行补了月份 01, 尽管提示词明令读不出月份不要猜), 于是:
      - 页面被判成归档页, 提前返回, 再也走不到导航兜底 -- 而真归档页就在同一
        个列表里, 本该被跳过去
      - 那条网页链接当 PDF 下载, 必然失败
      - 凭这个假的 2025-01 起点把之后 16 个月写成确认缺口
    分成两个函数, 判定只喂真 .pdf。

    副作用 (如实记): 少数站把 PDF 藏在 302 中转链接后面, 原来靠混进 PDF 清单
    碰运气命中。现在这类链接只作导航起跳, 由跳过去那页的真 .pdf 接手。真出现
    "点进去直接 302 到 PDF" 的发行商时, 在这里加一次 HEAD 判 content-type 即可,
    不要退回混列表的老做法。
    """
    seen: set = set()
    out: List[str] = []
    for href, text in _HTML_LINK_RE.findall(html):
        if not _MONTHLY_HINTS.search(text or ""):
            continue
        full = urljoin(base_url, href)
        if full.lower().split("?", 1)[0].endswith(".pdf") or full in seen:
            continue
        low = full.lower()
        if any(k in low for k in ("/login", "/contact", "/subscribe", "mailto:", "tel:")):
            continue
        seen.add(full)
        out.append(full)
    return out


# ---- Step 1: Gemini 排优先级 (语言活) ----

_RANK_PROMPT = """从下面 URL 列表中, 按"最可能是 {fund_name} 月度业绩报告归档页或含 PDF 下载"的可能性从高到低排序。

排序原则:
1. **发行商官网域** 优先 (识别: 域名含发行商关键词, 排聚合站 morningstar/investsmart/fundmonitors/livewire)
2. **path 语义**: /performance /fund-reports /monthly-reports /documents/reports > /capabilities /about /insights
3. **.pdf 直链** 最高 (直接是文件, 无中转)
4. 聚合站 (fundmonitors/morningstar 等) 若同域内包含 fact sheet 可保留但降级

score 仅用于排序对比 (相对值, 数值高的优先), 不表示"够阈值就采纳"; 代码只按 score 降序取 top-N.

只输出 JSON 数组, 无其他文字。格式:
[
  {{"url": "https://...", "score": 0-100, "reason": "一句话"}},
  ...
]

基金名: {fund_name}
发行商: {issuer}
已知官网: {issuer_domain}

URL 列表:
{url_list}
"""


def rank_urls(
    urls: List[str],
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str] = None,
    *,
    client: Optional[Client] = None,
) -> List[Dict[str, Any]]:
    """让 Gemini 排优先级. 失败降级: 用启发式 (issuer 域 + path 关键词) 打分."""
    if client is None:
        client = Client()
    if not urls:
        return []
    url_list_text = "\n".join(f"- {u}" for u in urls)
    prompt = _RANK_PROMPT.format(
        fund_name=fund_name, issuer=issuer,
        issuer_domain=issuer_domain or "(未知)",
        url_list=url_list_text,
    )
    try:
        resp = client.messages(prompt, max_tokens=2048)
        # 抠 JSON 数组
        text = resp.text.strip()
        m = re.search(r"\[[\s\S]*\]", text)
        if not m:
            raise ValueError("no_json_array")
        ranked = json.loads(m.group(0))
        # 严格校验: 只留出现在原 urls 里的 (LLM 幻觉出新 URL 一律丢)
        url_set = set(urls)
        out = []
        for item in ranked:
            if not isinstance(item, dict):
                continue
            u = item.get("url")
            if u in url_set:
                out.append({
                    "url": u,
                    "score": item.get("score", 0),
                    "reason": item.get("reason", ""),
                })
        if out:
            return out
    except Exception:
        pass
    # 降级: 启发式打分
    return _heuristic_rank(urls, fund_name, issuer, issuer_domain)


def _heuristic_rank(
    urls: List[str], fund_name: str, issuer: str,
    issuer_domain: Optional[str],
) -> List[Dict[str, Any]]:
    """LLM 排序失败兜底. 规则: issuer 域 +30, path 命中 +N, PDF 直链 +20."""
    tokens = re.findall(r"[a-z]+", (issuer + " " + fund_name).lower())
    tokens = [t for t in tokens if len(t) >= 4 and t not in {"fund", "capital", "management", "trust", "income"}]
    good_paths = ("/performance", "/fund-reports", "/monthly-reports", "/reports",
                  "/documents", "/downloads", "/monthly", "/factsheets")
    bad_paths = ("/contact", "/about", "/careers", "/legal", "/insights",
                 "/media", "/team", "/news")
    scored = []
    for u in urls:
        p = urlparse(u)
        host = p.netloc.lower().lstrip("www.")
        path = p.path.lower()
        s = 0
        if any(tok in host for tok in tokens):
            s += 30
        if issuer_domain and _same_host(u, issuer_domain):
            s += 20
        if path.endswith(".pdf"):
            s += 20
        for gp in good_paths:
            if gp in path:
                s += 15
                break
        for bp in bad_paths:
            if bp in path:
                s -= 15
                break
        scored.append({"url": u, "score": s, "reason": "heuristic"})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


# ---- 定位候选页面: 按引擎分派 (Spec G 4.1) ----
#
# find_archive_v2 三步中, 第 1-2 步 (搜索 + Gemini 排序) 本质是产出
# (issuer_domain, 已排序候选页面), 第 3 步 (抓页 + 列出 PDF 链接 + 交 LLM 判) 消费它。
# 按引擎分派只切第 1-2 步:
#   - Tavily 是检索, 给的是一堆 URL, 需要 Gemini 排序
#   - Grok 是 agentic search, 直接给答案, 已排好序, 再让 Gemini 排是多此一举
#     (实测答 GCI 归档页 3 轮全对 https://gcapinvest.com/our-lit)
# 第 3 步保持共用, 天然是反捏造闸: Grok 若主动提了 PDF, 那些 URL 不在抓下来的
# 页面 <a href> 里, 自动被丢弃。

def _grok_answer_archive(fund_name: str, issuer: str, asx_code: Optional[str]):
    """薄封装, 便于测试 monkeypatch (避免 patch 到 grok 模块全局)."""
    from .grok import answer_archive
    return answer_archive(fund_name, issuer, asx_code)


def _locate_via_tavily(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str],
    client: Optional[Client],
) -> Tuple[Optional[str], List[Dict[str, Any]], List[str]]:
    """Tavily 路径: 三次 query 拿 URL 池 -> 挑 issuer 域 -> Gemini 排序."""
    try:
        sources = multi_query_search(
            [fund_name, f"{fund_name} performance", f"{fund_name} monthly report"],
            max_results_per_query=5,
            exclude_aggregators=True,
        )
    except TavilyError:
        sources = []
    if not sources:
        return (issuer_domain, [], [])
    domain = _pick_issuer_domain(sources, issuer, fund_name) or issuer_domain
    ranked = rank_urls(sources, fund_name, issuer, domain, client=client)
    return (domain, ranked, sources)


def locate_candidates(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    *,
    engine: str = "tavily",
    client: Optional[Client] = None,
) -> Tuple[Optional[str], List[Dict[str, Any]], Dict[str, Any]]:
    """返 (issuer_domain, ranked, evidence).

    ranked 元素形如 {"url": str, "score": int, "reason": str}, 已排序。
    evidence 供 evidence_log 记录: engine_requested / engine_used /
    fallback_reason / sources。

    engine="grok" 且 Grok 失败时自动降级 Tavily, 但**降级必须可见** --
    evidence 里记 engine_used 与 fallback_reason, 上层再写进 job 日志。
    (Spec G 4.5: 旧代码注释禁止 SearXNG->Tavily 自动降级, 顾虑是"静默烧额度且
    故障不可见"; 这里的降级不静默, 且 Grok 的 503 是 15-20% 的高频瞬时故障,
    不降级会让相应比例的摄取直接失败。)
    """
    ev: Dict[str, Any] = {
        "engine_requested": engine,
        "engine_used": engine,
        "fallback_reason": "",
        "sources": [],
    }

    if engine == "grok":
        try:
            ans = _grok_answer_archive(fund_name, issuer, asx_code)
        except Exception as e:  # noqa: BLE001  (GrokError 及网络异常一并降级)
            ev["engine_used"] = "tavily"
            ev["fallback_reason"] = f"{type(e).__name__}: {e}"
        else:
            ev["sources"] = list(ans.sources)
            ev["grok_evidence"] = ans.evidence
            if ans.archive_url:
                ranked = [{
                    "url": ans.archive_url, "score": 100, "reason": "grok_answer",
                }]
                return (ans.issuer_domain or issuer_domain, ranked, ev)
            ev["engine_used"] = "tavily"
            ev["fallback_reason"] = "grok_no_archive_url"

    domain, ranked, sources = _locate_via_tavily(
        fund_name, issuer, issuer_domain, client)
    ev["engine_used"] = "tavily" if engine != "tavily" else "tavily"
    ev["sources"] = sources
    if not ranked:
        ev["reason"] = "搜索无结果"
    return (domain, ranked, ev)


# ---- Step 2: 并发抓页 + 抽 PDF 链接 ----

def _probe_one(url: str) -> Dict[str, Any]:
    """抓一页, 返 {url, html_ok, pdf_urls, nav_urls, error}.

    pdf_urls 只含真 .pdf 直链 (交 classify_pdf_links 判); nav_urls 是链接文字像
    月报但不是 PDF 的网页 (只作导航起跳点)。两者必须分开, 理由见
    _extract_monthlyish_page_links。
    """
    html = _fetch(url, timeout=FETCH_TIMEOUT)
    if not html:
        return {"url": url, "html_ok": False, "pdf_urls": [], "nav_urls": [],
                "error": "fetch_failed"}
    # URL 本身就是 .pdf: 直接算 PDF 候选
    if url.lower().split("?", 1)[0].endswith(".pdf"):
        return {"url": url, "html_ok": False, "pdf_urls": [url], "nav_urls": [],
                "error": None}
    return {"url": url, "html_ok": True,
            "pdf_urls": _extract_pdf_links(html, url),
            "nav_urls": _extract_monthlyish_page_links(html, url),
            "error": None, "html_snippet": html[:2000]}


def probe_urls(urls: List[str], *, concurrency: int = PROBE_CONCURRENCY) -> List[Dict[str, Any]]:
    """并发抓 URL, 每个返 pdf_urls. 保持输入顺序返回."""
    if not urls:
        return []
    results: List[Optional[Dict[str, Any]]] = [None] * len(urls)
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(_probe_one, u): i for i, u in enumerate(urls)}
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                results[i] = {"url": urls[i], "html_ok": False, "pdf_urls": [],
                              "error": f"exception:{type(e).__name__}:{e}"}
    return [r for r in results if r is not None]


# ---- Step 3: 顶层 ----
#
# 原来这里还有一步 "PDF 打样": 下载候选页里排第一的 PDF, 整份上传给模型问
# "这是不是本基金月报", 用来确认该页是否归档页。已删, 由 classify_pdf_links
# 取代 -- 同一个判断改成只看链接清单 (一次几百 token 的文本调用, 而非一份
# 24 页 3.8 万 token 的 PDF 上传), 且顺带直接产出 [(ym, url)] 全量月报清单,
# 不必再"确认页面 -> 另起一套规则筛该页文件"分两步走。
# 打样这一步本身也是 2026-07 Stake 事故的直接故障点 (见 git 历史)。

def find_archive_v2(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    *,
    client: Optional[Client] = None,
    top_n: int = TOP_N_PROBE,
    engine: str = "tavily",
) -> ArchivePointer:
    """v2 归档定位: 搜索 -> 排序 -> 抓页 -> classify_pdf_links 判链接清单.

    返回 ArchivePointer (与 v1 兼容, 让 run_discovery 无缝接).
    """
    if client is None:
        client = Client()

    # ---- 步 1+2: 定位候选页面 (按引擎分派, Spec G 4.1) ----
    domain, ranked, locate_ev = locate_candidates(
        fund_name, issuer, issuer_domain, asx_code, engine=engine, client=client,
    )
    real_sources = list(locate_ev.get("sources") or [])
    if not ranked:
        return ArchivePointer(
            archive_url=None, pagination_param=None, no_archive=True,
            latest_pdf_url=None, issuer_domain_confirmed=domain or issuer_domain,
            evidence=str(locate_ev.get("reason") or "定位无候选页面"),
            raw={"locate": locate_ev},
            search_sources=real_sources, search_queries=[],
        )
    top_urls = [r["url"] for r in ranked[:top_n]]

    # ---- 步 3: 并发抓 top-N, 抽 PDF 链接 ----
    probes = probe_urls(top_urls, concurrency=min(PROBE_CONCURRENCY, len(top_urls)))

    # ---- 步 4~6: 逐个来源判定, 取"判出月份最多"的那个当归档页 ----
    #
    # 原来分三步走 (按页内 PDF 数量分强/弱候选 -> 按 fund_name token 打分挑几份 ->
    # 逐份下载整份 PDF 上传打样"这是不是月报"), 数量与打样都是间接判据, 且各自带
    # 一套文件名规则; 挑错打样对象就整页误杀 (2026-07 Stake 事故)。现在一次问清:
    # 这个来源的全部 PDF 链接里, 哪几条是本基金月报、各是哪个月。
    #
    # 判据是**月份数量最多**, 不是"第一个非空" (2026-07-29 事故): Grok 那轮给的是
    # Stake 的 Zendesk 支持页, 该页 0 条真 .pdf, 但当时混进清单的网页链接里有一条
    # 营销页 ambition-report-2025 被判成月报 (模型从 "-2025" 看到年份自行补了月份
    # 01)。第一个非空即返回, 于是这一条误判就让整轮收工 -- 而真归档页
    # /legal/monthly-performance-report 就在同一批候选里, 再也没机会被看到。
    # 16 个月的来源显然强过 1 个月的来源, 月份数量是这里唯一可比的证据强度。
    month_counts: Dict[str, int] = {}
    probe_errors: Dict[str, str] = {}
    best: Optional[Dict[str, Any]] = None

    def _consider(src_url: str, pdf_urls: List[str], how: str) -> None:
        """对一个来源的 PDF 清单跑判定, 比当前最优则替换."""
        nonlocal best
        if not pdf_urls:
            return
        try:
            pairs, rejected, dropped = disc_mod.classify_pdf_links(
                pdf_urls, fund_name, client=client)
        except disc_mod.ClassifyError as e:
            # 单个来源判不了不致命 (还有其它候选与导航兜底), 记下供 evidence
            probe_errors[src_url] = f"classify_failed: {e}"
            return
        month_counts[src_url] = len(pairs)
        if not pairs:
            return
        if best is None or len(pairs) > len(best["pairs"]):
            best = {"url": src_url, "pdf_urls": list(pdf_urls), "pairs": pairs,
                    "rejected": rejected, "dropped": dropped, "how": how}

    def _pointer(b: Dict[str, Any], nav_hops: List[Tuple[str, str]]) -> ArchivePointer:
        pairs = b["pairs"]
        return ArchivePointer(
            archive_url=b["url"],
            pagination_param=None,
            # 只判出 1 个月 -> 视作"单份最新", 让上游继续走 wayback 补历史
            no_archive=len(pairs) < 2,
            # _dedup_links 已按 ym 升序, 末尾即最新月那份
            latest_pdf_url=pairs[-1][1],
            issuer_domain_confirmed=domain,
            evidence=(f"归档页确认 ({b['how']}): {b['url']} 页内 "
                      f"{len(b['pdf_urls'])} 份 PDF, 判定 {len(pairs)} 份为本基金"
                      f"月报 ({pairs[0][0]}~{pairs[-1][0]}), "
                      f"筛除 {len(b['rejected'])} 份"
                      + (f", 弃用 {b['dropped']} 条不可采信回答"
                         if b["dropped"] else "")),
            raw={"ranked": ranked, "locate": locate_ev, "nav_hops": nav_hops,
                 "rejected": b["rejected"][:20], "dropped": b["dropped"],
                 "month_counts": month_counts,
                 "probes": [
                     {"url": q["url"], "pdf_count": len(q["pdf_urls"]),
                      "nav_count": len(q.get("nav_urls") or []),
                      "monthly_count": month_counts.get(q["url"]),
                      "error": q.get("error") or probe_errors.get(q["url"])}
                     for q in probes
                 ]},
            search_sources=real_sources, search_queries=[],
            discovered_pdfs=list(b["pdf_urls"]),
            discovered_links=pairs,
        )

    from .navigate import navigate_one_hop, MAX_TOTAL_FETCHES
    nav_hops: List[Tuple[str, str]] = []
    visited: set = {p["url"] for p in probes}

    # 步 4: 候选页自身的 PDF
    for p in probes:
        _consider(p["url"], p["pdf_urls"], "候选页")
    if best is not None and len(best["pairs"]) >= 2:
        return _pointer(best, nav_hops)

    # 步 5: 候选页上"链接文字像月报"的网页 (中转页), 跳过去取该页真 PDF.
    # 场景: Stake 支持页 0 条 .pdf, 但页里明摆着
    # <a href="/legal/monthly-performance-report">, 点进去才是 16 份月报。
    # 这些链接绝不能直接当 PDF 判 (见 _extract_monthlyish_page_links), 但作为
    # 起跳点是最直接的一条线索 -- 比让模型再从整页内链里挑一次更省更稳。
    seeds: List[str] = []
    for p in probes:
        for u in (p.get("nav_urls") or []):
            if u not in visited and u not in seeds:
                seeds.append(u)
    for seed in seeds:
        if len(visited) >= MAX_TOTAL_FETCHES:
            break
        visited.add(seed)
        seed_html = _fetch(seed, timeout=FETCH_TIMEOUT)
        if not seed_html:
            continue
        nav_hops.append(("(中转链接)", seed))
        _consider(seed, _extract_pdf_links(seed_html, seed), "中转页")
    if best is not None and len(best["pairs"]) >= 2:
        return _pointer(best, nav_hops)

    # 步 6 (Spec C1): 还是不行 -> 让模型从同域内链里挑 1 跳
    nav_starts: List[str] = [
        p["url"] for p in probes if p.get("html_ok") and not p.get("pdf_urls")
    ] or [p["url"] for p in probes if p.get("html_ok")][:1]
    if domain:
        _home = domain if domain.startswith("http") else f"https://{domain}"
        if _home not in visited:
            nav_starts.append(_home)

    for start_url in nav_starts:
        if len(visited) >= MAX_TOTAL_FETCHES:
            break
        visited.add(start_url)
        # probes 只留了 2000 字符 snippet, 不够挑链, 重抓全量 HTML
        start_html = _fetch(start_url, timeout=FETCH_TIMEOUT)
        if not start_html:
            continue
        next_url, _next_html, next_pdfs = navigate_one_hop(
            start_url, start_html, fund_name, domain, visited, client,
        )
        nav_hops.append((start_url, next_url or "(no_pick)"))
        if next_url:
            _consider(next_url, next_pdfs, f"导航 {start_url} ->")
        if best is not None and len(best["pairs"]) >= 2:
            return _pointer(best, nav_hops)

    # 收尾: 只判出 1 个月的来源到这里才采用 (前面每一步都给了更强证据一次机会)
    if best is not None:
        return _pointer(best, nav_hops)

    # ---- 步 7: 全无命中 -> 返 no_archive 让上游走 L2/L3 ----
    return ArchivePointer(
        archive_url=None, pagination_param=None, no_archive=True,
        latest_pdf_url=None, issuer_domain_confirmed=domain,
        evidence="top-N 探测 + 中转页 + 导航兜底均未判出本基金月报, 走 L2/L3 兜底",
        raw={"ranked": ranked, "locate": locate_ev, "nav_hops": nav_hops,
             "month_counts": month_counts, "probes": [
                 {"url": p["url"], "pdf_count": len(p["pdf_urls"]),
                  "nav_count": len(p.get("nav_urls") or []),
                  "monthly_count": month_counts.get(p["url"]),
                  "error": p.get("error") or probe_errors.get(p["url"])}
                 for p in probes
             ]},
        search_sources=real_sources,
        search_queries=[],
    )
