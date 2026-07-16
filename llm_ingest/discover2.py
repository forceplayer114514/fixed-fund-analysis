"""discover2: 归档页发现 v2 -- Tavily 排序 + Scrapling 抓 + PDF 打样验证.

问题背景 (discover.py v1 的坑):
  阶段 B 只喂 Gemini URL 字面串, 让它凭 slug 猜"归档 vs 单份"。
  Yarra 一测: Tavily 拿到 yarracm.com/{capabilities/enhanced-income, performance}
  两个候选, Gemini 挑了前者 (营销页, 无 PDF), 忽略后者 (真归档)。
  信息量根本不够 -- Gemini 语义猜错必然发生。

v2 流程 (四步, LLM 只做能力内的活):
  1. Tavily 拿 URL (discover.py 已通)
  2. **Gemini 排优先级**   -- 语言活: 按"最可能含月报下载链接"排序输出 JSON
  3. **_fetch top-N 并发** -- 抓 HTML (playwright/requests), 抽 PDF 链接
  4. **Gemini 判 PDF**     -- 内容活: 首份 PDF 走 extract_from_pdf, not_found=False 且
                             measure=net_monthly 则该页确认为月报归档 (或单份最新)

关键决策:
  - LLM 只做 "排序 URL" + "判 PDF 内容", 决不猜"URL 是不是归档" (Yarra 坑)
  - top-N 并发抓 (ThreadPoolExecutor), 谁先出 PDF 谁赢
  - PDF 打样必要: 有站能抓到 PDF 链接但不是月报 (PDS/白皮书/因子表), 不打样会入错

依赖复用: discover.py 的 _fetch / _load_prompt / ArchivePointer / _same_host
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

from . import extract as ex_mod
from .client import Client
from .discover import (
    ArchivePointer,
    _fetch,
    _load_prompt,
    _pick_issuer_domain,
    _same_host,
    _parse_json_response,
)
from .tavily import TavilyError, multi_query_search

# ---- 常量 ----
TOP_N_PROBE = 4          # 排序后取 top-N 并发探测
FETCH_TIMEOUT = 30       # 单次抓页超时
PROBE_CONCURRENCY = 4    # 并发线程池
PDF_DOWNLOAD_TIMEOUT = 30

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
    """抓页 HTML -> 候选 PDF 绝对 URL 列表 (去重保序).

    双通路:
      (a) `<a href="*.pdf">` 直接命中 -- 静态归档常用
      (b) `<a href="/xxx">Monthly Report XYZ</a>` 文本含月报关键词 -- 有站把 PDF
          藏在中转页 (点进去 302 到真 PDF), 保留候选让上游继续 fetch 判
    """
    seen: set = set()
    out: List[str] = []
    for href in _PDF_HREF_RE.findall(html):
        full = urljoin(base_url, href)
        if full not in seen:
            seen.add(full)
            out.append(full)
    # 再补一轮: 链接文本命中月报关键词, 即使 href 不是 .pdf (有站 PDF 走 302 中转)
    for href, text in _HTML_LINK_RE.findall(html):
        if not _MONTHLY_HINTS.search(text or ""):
            continue
        full = urljoin(base_url, href)
        if full.lower().endswith(".pdf") or full in seen:
            continue
        # 避免登录/联系页噪声
        low = full.lower()
        if any(k in low for k in ("/login", "/contact", "/subscribe", "mailto:", "tel:")):
            continue
        seen.add(full)
        out.append(full)
    return out


def _rank_pdfs_by_name_match(pdf_urls: List[str], fund_name: str) -> List[str]:
    """按 fund_name token 与 PDF slug 匹配度重排 PDF URL 列表 (降序).

    命中越多排越前。tie-break: 保原顺序 (稳定排序)。
    """
    fund_tokens = set(re.findall(r"[a-z]+", fund_name.lower()))
    fund_tokens -= {"fund", "the", "and", "of", "trust", "class"}
    if not fund_tokens:
        return pdf_urls
    def _score(u: str) -> int:
        slug = re.sub(r".*/", "", u).lower()
        slug_tokens = set(re.findall(r"[a-z]+", slug))
        return len(fund_tokens & slug_tokens)
    return sorted(pdf_urls, key=_score, reverse=True)


def _pdf_slug_match_count(pdf_url: str, fund_name: str) -> int:
    """PDF slug 与 fund_name token 的交集大小 (0 = 完全无关)."""
    fund_tokens = set(re.findall(r"[a-z]+", fund_name.lower()))
    fund_tokens -= {"fund", "the", "and", "of", "trust", "class"}
    slug = re.sub(r".*/", "", pdf_url).lower()
    slug_tokens = set(re.findall(r"[a-z]+", slug))
    return len(fund_tokens & slug_tokens)


# ---- Step 1: Gemini 排优先级 (语言活) ----

_RANK_PROMPT = """从下面 URL 列表中, 按"最可能是 {fund_name} 月度业绩报告归档页或含 PDF 下载"的可能性从高到低排序。

排序原则:
1. **发行商官网域** 优先 (识别: 域名含发行商关键词, 排聚合站 morningstar/investsmart/fundmonitors/livewire)
2. **path 语义**: /performance /fund-reports /monthly-reports /documents/reports > /capabilities /about /insights
3. **.pdf 直链** 最高 (直接是文件, 无中转)
4. 聚合站 (fundmonitors/morningstar 等) 若同域内包含 fact sheet 可保留但降级

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


# ---- Step 2: 并发抓页 + 抽 PDF 链接 ----

def _probe_one(url: str) -> Dict[str, Any]:
    """抓一页, 返 {url, html_ok, pdf_urls, error}."""
    html = _fetch(url, timeout=FETCH_TIMEOUT)
    if not html:
        return {"url": url, "html_ok": False, "pdf_urls": [], "error": "fetch_failed"}
    # URL 本身就是 .pdf: 直接算 PDF 候选
    if url.lower().endswith(".pdf"):
        return {"url": url, "html_ok": False, "pdf_urls": [url], "error": None}
    pdf_urls = _extract_pdf_links(html, url)
    return {"url": url, "html_ok": True, "pdf_urls": pdf_urls, "error": None,
            "html_snippet": html[:2000]}


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


# ---- Step 3: PDF 打样 (extract 判 net_return) ----

def _download_pdf(url: str, timeout: int = PDF_DOWNLOAD_TIMEOUT) -> Optional[bytes]:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"},
                         allow_redirects=True)
        if r.status_code == 200 and r.content:
            # 简验: PDF magic
            if r.content[:4] == b"%PDF":
                return r.content
    except Exception:
        pass
    return None


def confirm_pdf_is_monthly_report(
    pdf_url: str, fund_name: str, *, client: Optional[Client] = None,
) -> Tuple[bool, Optional[ex_mod.Extraction]]:
    """下载 PDF -> 走 extract -> not_found=False + measure 合理 -> 是月报.

    返 (is_monthly, extraction). 下载失败/解析失败/not_found -> (False, ex_or_None).
    """
    pdf_bytes = _download_pdf(pdf_url)
    if not pdf_bytes:
        return False, None
    # 写到临时文件 (extract_from_pdf 接 Path)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp_path = Path(f.name)
    try:
        # ym 未知先给占位 (打样只要判是不是月报, 具体 ym 由 parse_archive 阶段做)
        ex = ex_mod.extract_from_pdf(tmp_path, expected_ym="0000-00", client=client,
                                     max_pages=2)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    if ex.not_found or ex.net_return is None:
        return False, ex
    # measure 合理性: net_monthly 是月报; 其他 (annualized/quarterly/ytd) 不算
    if ex.measure and ex.measure.lower() not in ("net_monthly", "monthly_net", "monthly"):
        # 结构性字段类型错 -- 按 CLAUDE.md 五节, 不能保留, 视作"不是月报"
        return False, ex
    return True, ex


# ---- Step 4: 顶层 ----

def find_archive_v2(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    *,
    client: Optional[Client] = None,
    top_n: int = TOP_N_PROBE,
) -> ArchivePointer:
    """v2 归档定位: Tavily -> Gemini 排序 -> Scrapling 抓 -> PDF 打样验证.

    返回 ArchivePointer (与 v1 兼容, 让 run_discovery 无缝接).
    """
    if client is None:
        client = Client()

    # ---- 步 1: Tavily 拿 URL ----
    try:
        real_sources = multi_query_search(
            [fund_name, f"{fund_name} performance", f"{fund_name} monthly report"],
            max_results_per_query=5,
            exclude_aggregators=True,
        )
    except TavilyError:
        real_sources = []

    if not real_sources:
        return ArchivePointer(
            archive_url=None, pagination_param=None, no_archive=True,
            latest_pdf_url=None, issuer_domain_confirmed=issuer_domain,
            evidence="Tavily 搜索无结果", raw={},
            search_sources=[], search_queries=[],
        )

    # ---- 步 1.5: 挑 issuer 域 (用现有启发式, 排聚合站) ----
    domain = _pick_issuer_domain(real_sources, issuer, fund_name) or issuer_domain

    # ---- 步 2: Gemini 排优先级 ----
    ranked = rank_urls(real_sources, fund_name, issuer, domain, client=client)
    if not ranked:
        return ArchivePointer(
            archive_url=None, pagination_param=None, no_archive=True,
            latest_pdf_url=None, issuer_domain_confirmed=domain,
            evidence="排序无结果", raw={"ranked": []},
            search_sources=real_sources, search_queries=[],
        )
    top_urls = [r["url"] for r in ranked[:top_n]]

    # ---- 步 3: 并发抓 top-N, 抽 PDF 链接 ----
    probes = probe_urls(top_urls, concurrency=min(PROBE_CONCURRENCY, len(top_urls)))

    # ---- 步 4: 分类 - 抓到 PDF 链接的按"数量+归档 hint"评分 ----
    # 逻辑:
    #   PDF 链接 >= 3 且抓到页 -> 强候选归档页
    #   PDF 链接 = 1..2       -> 单份最新 / 备选
    #   PDF 链接 = 0          -> 该 URL 非归档 (可能是营销/介绍页)
    strong_candidates: List[Dict[str, Any]] = []
    single_pdfs: List[Dict[str, Any]] = []
    for p in probes:
        if not p["pdf_urls"]:
            continue
        # 步 4.5: PDF 列表按 slug 与 fund_name 匹配度重排, 匹配度高的先打样
        # 教训 (Yarra /performance): 排序拿到的 top-1 页可能含多份 PDF, 其中
        # 只有部分是目标基金的月报, 其余是别的基金 (同域下多基金共存)。
        # 直接取 pdf_urls[0] 会打样到别的基金 PDF 判 not_found, 浪费一轮 Gemini。
        # 按 fund_name token 匹配度筛后, 目标 PDF 排到最前, 首份就命中。
        p["pdf_urls"] = _rank_pdfs_by_name_match(p["pdf_urls"], fund_name)
        if len(p["pdf_urls"]) >= 3:
            strong_candidates.append(p)
        else:
            single_pdfs.append(p)

    # ---- 步 5: 逐个 strong_candidate 首份 PDF 打样, 通过即敲定归档 ----
    for cand in strong_candidates:
        first_pdf = cand["pdf_urls"][0]
        # 优化: 首 PDF slug 与 fund_name 零匹配 -> 该页所有 PDF 都跟目标基金无关,
        # 跳过整页免除一次 Gemini API 调用 (Yarra /performance 全是 Australian Income
        # 基金 PDF, 与 Enhanced Income 无关, 打样必失败, 直接跳)
        if _pdf_slug_match_count(first_pdf, fund_name) == 0:
            continue
        ok, ex = confirm_pdf_is_monthly_report(first_pdf, fund_name, client=client)
        if ok:
            return ArchivePointer(
                archive_url=cand["url"],
                pagination_param=None,   # 由后续 parse_archive 探测
                no_archive=False,
                latest_pdf_url=first_pdf,
                issuer_domain_confirmed=domain,
                evidence=f"归档页确认: {cand['url']} 含 {len(cand['pdf_urls'])} 份 PDF, 首份验证为月报",
                raw={"ranked": ranked, "probes": [
                    {"url": p["url"], "pdf_count": len(p["pdf_urls"])} for p in probes
                ]},
                search_sources=real_sources,
                search_queries=[],
                # 把此页所有 PDF 都带回 run_discovery, 免它再让 Gemini 解析一遍
                discovered_pdfs=list(cand["pdf_urls"]),
            )

    # ---- 步 6: 无强候选 -> single_pdfs 里挑首份能通过打样的当"最新单份" (no_archive=True) ----
    for cand in single_pdfs:
        first_pdf = cand["pdf_urls"][0]
        ok, ex = confirm_pdf_is_monthly_report(first_pdf, fund_name, client=client)
        if ok:
            return ArchivePointer(
                archive_url=None,
                pagination_param=None,
                no_archive=True,
                latest_pdf_url=first_pdf,
                issuer_domain_confirmed=domain,
                evidence=f"单份最新: {first_pdf} (归档页未找到, 走 wayback 补历史)",
                raw={"ranked": ranked, "probes": [
                    {"url": p["url"], "pdf_count": len(p["pdf_urls"])} for p in probes
                ]},
                search_sources=real_sources,
                search_queries=[],
                # 单份场景下同页可能仍有其他 PDF (如 1-2 份), 一并带回
                discovered_pdfs=list(cand["pdf_urls"]),
            )

    # ---- 步 7: 全没通过打样 -> 返 no_archive 让上游走 L2/L3 ----
    return ArchivePointer(
        archive_url=None, pagination_param=None, no_archive=True,
        latest_pdf_url=None, issuer_domain_confirmed=domain,
        evidence="top-N 探测未发现月报 PDF, 走 L2/L3 兜底",
        raw={"ranked": ranked, "probes": [
            {"url": p["url"], "pdf_count": len(p["pdf_urls"]), "error": p.get("error")}
            for p in probes
        ]},
        search_sources=real_sources,
        search_queries=[],
    )
