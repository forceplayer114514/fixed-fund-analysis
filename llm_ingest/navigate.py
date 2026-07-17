"""Spec C1: 自主 1 跳导航层.

场景 (根因见 spec C1): Tavily 拿到 issuer 官网某"归档入口页", 但该页本身**不含**
月报 PDF 直链, 而是把入口再嵌进内链 (例: Stake 用 /performance-updates-and-statements
挂 <a href="/legal/monthly-performance-report">, 16 份 PDF 在跳过去那页).

现有 v2 (discover2._extract_pdf_links) 只 regex-scan 单页, 完全不进内链. 本模块补:
让 Gemini 从**已抓 HTML 的同域 + 关键词内链**里挑 1 条最像月报归档的, 抓 1 跳后再走
同一 PDF 抽取. 无命中回主页重试 1 次即放弃.

设计约束 (照 spec C1 用户答问 + Anti-hallucination):
  - Gemini 挑到的 URL 必须**字面存在**于原候选集合中. 生成新 URL 一律丢.
  - 关键词白名单 + 同域, 排 /login /contact 等噪声路径.
  - visited 集合防重抓, MAX_TOTAL_FETCHES=8 硬上限防无限循环.
"""
from __future__ import annotations

import re
from typing import List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from .client import Client
from .discover import _fetch, _load_prompt, _parse_json_response, _same_host

# ---- 常量 ----
MAX_TOTAL_FETCHES = 8         # 一次 discovery 内 _fetch 总调用上限
NAV_TIMEOUT_PER_HOP = 30
NAV_MAX_CANDIDATES = 30       # 送给 Gemini 的候选链接上限, 防 prompt 爆炸

# ---- 关键词白名单 (anchor_text 命中即入选) ----
# 与 discover2._MONTHLY_HINTS 保持同步 (含 archive/statement/distribution/update)
_NAV_KEYWORDS = re.compile(
    r"(monthly|month-end|report|statement|archive|performance|distribution|update|factsheet)",
    re.I,
)

# 强信号 (排序用): anchor / URL 命中即高分, 挪到 candidates 前面让 LLM 更容易选
_NAV_STRONG_SIGNAL = re.compile(
    r"(monthly[\s\-_]*(?:performance[\s\-_]*)?report|"
    r"monthly[\s\-_]*update|"
    r"performance[\s\-_]*(?:update|report)|"
    r"fund[\s\-_]*(?:monthly|report))",
    re.I,
)

# ---- 排除路径 (URL 命中即丢) ----
_NAV_EXCLUDE_PATTERNS = (
    "/login", "/logout", "/contact", "/subscribe", "/careers",
    "/about", "/team", "/media", "/press", "mailto:", "tel:",
    "javascript:", "#",
)

# 抓 <a href="..." ...>anchor_text</a>, 支持嵌套/多行 (非贪婪 [\s\S]*?)
_ANCHOR_RE = re.compile(
    r'<a\s[^>]*?href=["\']([^"\']+)["\'][^>]*?>([\s\S]*?)</a>',
    re.I,
)


def _clean_text(html_frag: str) -> str:
    """剥 HTML 标签 + 折叠空白, 拿纯 anchor 文本."""
    txt = re.sub(r"<[^>]+>", " ", html_frag or "")
    return re.sub(r"\s+", " ", txt).strip()


def _extract_candidate_links(
    html: str,
    base_url: str,
    visited: Set[str],
) -> List[Tuple[str, str]]:
    """从 HTML 抽内链候选. 过滤:
      - 同域 (_same_host(u, base_url))
      - anchor_text 命中 _NAV_KEYWORDS
      - URL 未在 visited
      - 排除 _NAV_EXCLUDE_PATTERNS
      - .pdf 直链跳过 (归 _extract_pdf_links 管, 本层只找**中转页**)
    返回 (anchor_text, absolute_url) 去重保序.
    """
    if not html or not base_url:
        return []
    seen: Set[str] = set()
    out: List[Tuple[str, str]] = []
    for href, inner in _ANCHOR_RE.findall(html):
        if not href:
            continue
        full = urljoin(base_url, href)
        low = full.lower()
        # 快过滤
        if any(bad in low for bad in _NAV_EXCLUDE_PATTERNS):
            continue
        if low.endswith(".pdf"):
            continue
        if full in visited or full in seen:
            continue
        # 域名过滤
        if not _same_host(full, base_url):
            continue
        anchor = _clean_text(inner)
        if not anchor or not _NAV_KEYWORDS.search(anchor):
            # anchor 不含关键词, 再看 URL path 有没有 (弱信号也算)
            path = urlparse(full).path.lower()
            if not _NAV_KEYWORDS.search(path):
                continue
        seen.add(full)
        out.append((anchor or "(no-text)", full))
        if len(out) >= NAV_MAX_CANDIDATES:
            break
    # 按强信号 (anchor 或 URL 命中 _NAV_STRONG_SIGNAL) 排前, 让 Gemini 一眼看到最像的
    def _score(item: Tuple[str, str]) -> int:
        txt, u = item
        s = 0
        if _NAV_STRONG_SIGNAL.search(txt or ""):
            s += 10
        if _NAV_STRONG_SIGNAL.search(u):
            s += 10
        # 弱信号也算 (但排在强信号后)
        if _NAV_KEYWORDS.search(txt or ""):
            s += 2
        if _NAV_KEYWORDS.search(urlparse(u).path):
            s += 2
        return s
    out.sort(key=_score, reverse=True)
    return out


def _gemini_pick_next_hop(
    candidates: List[Tuple[str, str]],
    fund_name: str,
    current_url: str,
    client: Client,
) -> Optional[str]:
    """让 Gemini 从 candidates 挑 1 条最像月报归档的 URL.
    严格校验: 返回值必须**字面**在 candidates 里, 否则视作幻觉丢弃 -> None.
    Gemini 报 picked_url=null / 解析失败 -> None.

    Spec C1 fallback: Gemini 保守返 null 时, 若 top-1 是强信号命中且不是当前页
    的语义等价 (path 相同), 兜底返回 top-1 (保证 Stake 场景可挑对).
    """
    if not candidates:
        return None
    candidate_lines = "\n".join(
        f'- text="{txt}" url={url}' for txt, url in candidates
    )
    prompt = _load_prompt("nav_pick.md").format(
        fund_name=fund_name,
        current_url=current_url,
        candidates_list=candidate_lines,
    )
    picked: Optional[str] = None
    try:
        resp = client.messages(prompt, max_tokens=512)
        obj = _parse_json_response(resp.text) or {}
        p = obj.get("picked_url")
        if p and p not in ("null", "None"):
            url_set = {u for _, u in candidates}
            if p in url_set:
                picked = p
    except Exception:  # noqa: BLE001
        pass

    if picked:
        return picked

    # 兜底 (Stake 场景): Gemini 拒挑但 top-1 明显强信号, 且 URL path 不等于当前页
    # (排除自指) -> 用 top-1. 保守: 仅当 anchor + URL 都有强信号才走兜底.
    top_txt, top_url = candidates[0]
    top_path = urlparse(top_url).path.rstrip("/").lower()
    cur_path = urlparse(current_url).path.rstrip("/").lower()
    # path 完全相同或一个是另一个的 /au 前缀变体, 判为自指
    if top_path == cur_path or top_path == "/au" + cur_path or "/au" + top_path == cur_path:
        # top-1 是自己, 试 top-2
        if len(candidates) >= 2:
            top_txt, top_url = candidates[1]
            top_path = urlparse(top_url).path.rstrip("/").lower()
            if top_path == cur_path:
                return None
        else:
            return None
    # 只在 top 强信号双命中时兜底 (anchor + URL 都要含强关键词)
    if _NAV_STRONG_SIGNAL.search(top_txt) and _NAV_STRONG_SIGNAL.search(top_url):
        return top_url
    return None


def navigate_one_hop(
    start_url: str,
    start_html: str,
    fund_name: str,
    issuer_domain: Optional[str],
    visited: Set[str],
    client: Client,
) -> Tuple[Optional[str], Optional[str], List[str]]:
    """从 start_url 起走 1 跳: 抽候选 -> Gemini 挑 -> _fetch -> _extract_pdf_links.

    参数:
      start_url: 已抓到 HTML 的当前页 URL (作为 base_url + 同域参考)
      start_html: 该页完整 HTML
      fund_name: 用于 Gemini prompt 语境
      issuer_domain: 保留兼容 (当前逻辑用 start_url 判同域), 未来可扩
      visited: 全流程共享的已抓 URL 集, 会被 mutation (加入 next_url)
      client: Gemini 客户端

    返回 (next_url, next_html, pdf_urls). 任一步失败 -> (None, None, []).
    调用方负责在 pdf_urls 上跑 confirm_pdf_is_monthly_report 打样.
    """
    _ = issuer_domain  # placeholder, 现用 start_url 的域
    # 循环 / 上限守卫: visited 已到 MAX_TOTAL_FETCHES 说明本轮 discovery 抓太多次
    if len(visited) >= MAX_TOTAL_FETCHES:
        return (None, None, [])

    candidates = _extract_candidate_links(start_html, start_url, visited)
    if not candidates:
        return (None, None, [])

    next_url = _gemini_pick_next_hop(candidates, fund_name, start_url, client)
    if not next_url:
        return (None, None, [])

    visited.add(next_url)
    next_html = _fetch(next_url, timeout=NAV_TIMEOUT_PER_HOP)
    if not next_html:
        return (next_url, None, [])

    # 延迟 import 破循环 (discover2 依赖 discover, 若顶部 import 则装载序敏感)
    from .discover2 import _extract_pdf_links
    pdf_urls = _extract_pdf_links(next_html, next_url)
    return (next_url, next_html, pdf_urls)
