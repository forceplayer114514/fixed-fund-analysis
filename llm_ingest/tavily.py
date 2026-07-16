"""Tavily 搜索客户端 -- 阶段 A discovery 首选.

为什么 Tavily 优先于 sub2api web_search:
  - sub2api 的 web_search 触发是概率性的 (需要多 prompt 变体+重试)
  - grounding 返回是 vertexaisearch.cloud.google.com/grounding-api-redirect/...
    展开跳板需要直连 Google 1e100.net, 中国网络下 20s 卡死
  - Gemini 会 hallucinate URL (Yarra 一测抓到 yarracapital.com, 真域是 yarracm.com)
  - Tavily 是纯 REST 一次调用给结构化结果, 无 grounding 中间层, 无幻觉

回退语义:
  - TAVILY_API_KEY 缺失 -> tavily_search 抛 TavilyError -> 上游 fallback 到 web_search
  - 网络异常 / API 4xx -> 同上
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


def tavily_search(
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
