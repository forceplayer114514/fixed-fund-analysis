"""搜索客户端 -- 阶段 A discovery 首选后端: Tavily (REST, 结构化结果)。

Spec G: SearXNG 后端 (曾作为 2026-07-19 实测的候选主搜索) 与 sub2api
web_search 兜底均已删除。

删除原因:
  - SearXNG 是自建服务, 已下线 (localhost:8081 不通, 无 docker 进程),
    且切换用的 SEARCH_BACKEND 环境变量全仓库从未被设置过 -- 旧默认值一度
    指向这个死服务, 导致每次搜索都抛 TavilyError 并静默降级。
  - sub2api web_search 命中率仅 53% (2026-07-19 量化测试,
    `sub2api-websearch-测试报告.md`), 会 hallucinate URL (Yarra 一测抓到
    yarracapital.com, 真域是 yarracm.com), grounding 跳板
    (vertexaisearch.cloud.google.com/grounding-api-redirect/...) 展开
    需直连 Google 1e100.net, 中国网络下 20s 卡死。

现分工: Tavily 为默认结构化搜索 (阶段 A discovery), Grok 作为第二搜索引擎
(端到端可选, 见 `llm_ingest/grok.py`), 两者均为纯 REST/API 调用, 不再有
grounding 中间层或客户端过滤/over_fetch -- exclude_domains 由 Tavily
服务端原生支持。

失败语义: 当前 Tavily 调用失败 (key缺/网络异常/API错误/HTTP非200) ->
tavily_search 抛 TavilyError, 由上游 (discover.py/discover2.py) 决定
如何处理 (通常记为该 query 失败, 不再降级到其他后端)。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

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


def tavily_search(
    query: str,
    *,
    max_results: int = 8,
    search_depth: str = "basic",
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> List[TavilyResult]:
    """单次 Tavily 搜索.

    Spec G: SearXNG 后端与 SEARCH_BACKEND 分派已删 -- 该服务已下线
    (localhost:8081 不通, 无 docker 进程), 且该环境变量全仓库从未设置过,
    旧默认值让每次搜索都抛 TavilyError 静默降级到 sub2api web_search。
    exclude_domains 由 Tavily 服务端原生支持, 无需客户端过滤 + over_fetch。
    """
    return _tavily_impl(
        query, max_results=max_results, search_depth=search_depth,
        include_domains=include_domains, exclude_domains=exclude_domains,
        timeout=timeout,
    )


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
