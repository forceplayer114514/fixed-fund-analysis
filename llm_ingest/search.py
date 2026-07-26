"""搜索客户端 -- 阶段 A discovery 首选. 默认后端 Tavily, 可切回 SearXNG.

选型依据 (2026-07-19 `tavily替代方案-最终报告.md` 实测): 5支基金x3变体x4轮,
SearXNG 池命中率打平 Tavily (80%), 噪声 0%, 延迟更快 (P50 0.8-1.1s vs
Tavily 2.2s), 且全程测试未被限流打断 (SearXNG 是自建服务, 限流作用在
底层引擎的出口IP上, 不是账号/密钥配额, 不会像 Tavily 那样把 credits 用尽)。
但 SearXNG 服务已于此后下线 (Spec G 2.8), 默认后端已改回 Tavily; SearXNG
分支代码原样保留 (阶段三才删), 用 SEARCH_BACKEND 环境变量手动切换,
不做自动侦测降级 -- 自动切换会在某后端故障时无声燃烧另一后端配额,
且故障不会被发现 (表面上还在正常出结果)。

为什么两个搜索后端都优先于 sub2api web_search (兜底, 不变):
  - sub2api 的 web_search 触发是概率性的, 命中率只有 53% (2026-07-19 量化
    测试, 见 `sub2api-websearch-测试报告.md`), 延迟慢 3-8 倍
  - grounding 返回是 vertexaisearch.cloud.google.com/grounding-api-redirect/...
    展开跳板需要直连 Google 1e100.net, 中国网络下 20s 卡死
  - Gemini 会 hallucinate URL (Yarra 一测抓到 yarracapital.com, 真域是 yarracm.com)
  - Tavily/SearXNG 都是纯 REST 一次调用给结构化结果, 无 grounding 中间层

回退语义:
  - 当前后端失败 (key缺/网络异常/API错误/HTTP非200) -> tavily_search 抛
    TavilyError -> 上游 (discover.py/discover2.py) fallback 到 sub2api
    web_search。两个后端共用同一个异常类型, 上游无需感知具体是哪个后端。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse

import requests

from .client import load_env

TAVILY_ENDPOINT = "https://api.tavily.com/search"
DEFAULT_TIMEOUT = 30


class TavilyError(RuntimeError):
    pass


@dataclass(frozen=True)
class TavilyResult:
    url: str
    title: str
    content: str  # snippet


def _api_key() -> str:
    load_env()
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        raise TavilyError("TAVILY_API_KEY 未设置 (检查 .env)")
    return key


def _tavily_impl(
    query: str,
    *,
    max_results: int = 8,
    search_depth: str = "basic",
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> List[TavilyResult]:
    """单次 Tavily 搜索. search_depth: 'basic' 免费; 'advanced' 花更多 credits."""
    payload = {
        "api_key": _api_key(),
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains
    try:
        r = requests.post(TAVILY_ENDPOINT, json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise TavilyError(f"网络错误: {e}") from e
    if r.status_code != 200:
        raise TavilyError(f"HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    out: List[TavilyResult] = []
    for x in data.get("results", []) or []:
        url = x.get("url") or ""
        if not url:
            continue
        out.append(TavilyResult(
            url=url,
            title=x.get("title") or "",
            content=x.get("content") or "",
        ))
    return out


def _searxng_impl(
    query: str,
    *,
    max_results: int = 8,
    timeout: int = DEFAULT_TIMEOUT,
    **_ignored,  # search_depth/include_domains: Tavily 独有, SearXNG 不支持, 静默忽略
) -> List[TavilyResult]:
    """单次 SearXNG 搜索. 需要 `docker restart` 后确认 settings.yml 开了
    `search.formats: [html, json]` (默认关闭, 不加直接 403)。

    显式传 engines (默认 google,bing), 不吃 SearXNG 的默认引擎池 -- 实测
    默认池里的 duckduckgo/brave/startpage 在持续高频调用下会被目标引擎
    自己限流 (CAPTCHA/Suspended), 只有显式指定的引擎才稳定返回结果, 跟
    open-websearch 必须显式传 engines 才扛得住是同一个坑 (见最终报告 3.4)。
    """
    base = os.environ.get("SEARXNG_URL", "http://localhost:8081").rstrip("/")
    engines = os.environ.get("SEARXNG_ENGINES", "google,bing")
    try:
        r = requests.get(
            f"{base}/search",
            params={"q": query, "format": "json", "engines": engines, "language": "en-AU"},
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise TavilyError(f"SearXNG 网络错误: {e}") from e
    if r.status_code != 200:
        raise TavilyError(f"SearXNG HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    out: List[TavilyResult] = []
    for x in (data.get("results") or [])[:max_results]:
        url = x.get("url") or ""
        if not url:
            continue
        out.append(TavilyResult(
            url=url,
            title=x.get("title") or "",
            content=x.get("content") or "",
        ))
    return out


def _host_blocked(url: str, blocked: List[str]) -> bool:
    """后缀匹配 (host==blocked 或 host 以 .blocked 结尾), 避免
    "notmorningstar.com.evil.com" 之类的误判/漏判。"""
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == b or host.endswith("." + b) for b in blocked)


def tavily_search(
    query: str,
    *,
    max_results: int = 8,
    search_depth: str = "basic",
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> List[TavilyResult]:
    """单次搜索, 按 SEARCH_BACKEND 环境变量分派后端 (默认 tavily)。

    Tavily 服务端支持 exclude_domains 直接过滤; SearXNG 不支持, 这里改成
    客户端过滤 -- 过滤会让条数变少, 所以先多取 (over_fetch), 过滤后再截断
    到 max_results, 避免 exclude_aggregators=True 那条路径静默返回不足数。
    """
    # 默认 tavily: SearXNG 服务已下线 (Spec G 2.8), 旧默认值 "searxng" 会让
    # 每次搜索都抛 TavilyError 并静默降级到 sub2api web_search, Tavily 形同虚设。
    backend = os.environ.get("SEARCH_BACKEND", "tavily").strip().lower()
    if backend == "tavily":
        return _tavily_impl(
            query, max_results=max_results, search_depth=search_depth,
            include_domains=include_domains, exclude_domains=exclude_domains,
            timeout=timeout,
        )
    over_fetch = max_results * 3 if exclude_domains else max_results
    results = _searxng_impl(query, max_results=over_fetch, timeout=timeout)
    if exclude_domains:
        results = [r for r in results if not _host_blocked(r.url, exclude_domains)]
    return results[:max_results]


# ---- 聚合站黑名单 (阶段 A 已知产垃圾, 上游用来 exclude_domains) ----
AGGREGATOR_DOMAINS = [
    "morningstar.com",
    "morningstar.com.au",
    "investsmart.com.au",
    "lonsec.com.au",
    "citywire.com",
    "pitchbook.com",
    "yahoo.com",
    "reuters.com",
    "bloomberg.com",
    "linkedin.com",
    "wikipedia.org",
    "youtube.com",
    "stocklight.com",
    "moneymanagement.com.au",
    "afr.com",
    "intelligentinvestor.com.au",
    "independentresearch.com.au",
]


def multi_query_search(
    queries: List[str],
    *,
    max_results_per_query: int = 5,
    exclude_aggregators: bool = False,
) -> List[str]:
    """跑一组 queries, 合并去重返 URL 列表 (保序).

    exclude_aggregators=True 时把聚合站列入 exclude_domains (Tavily 直接过滤,
    比返回后过滤省 credits 也更准).
    """
    exclude = AGGREGATOR_DOMAINS if exclude_aggregators else None
    seen = set()
    urls: List[str] = []
    for q in queries:
        try:
            results = tavily_search(
                q, max_results=max_results_per_query, exclude_domains=exclude,
            )
        except TavilyError:
            # 单 query 失败不拖累其他: 让上游看合并后是否够用
            continue
        for r in results:
            if r.url in seen:
                continue
            seen.add(r.url)
            urls.append(r.url)
    return urls
