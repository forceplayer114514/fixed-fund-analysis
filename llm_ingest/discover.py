"""Phase 2: 归档页发现 + PDF 下载.

三层 fallback (集合差驱动, 概念抄 skills/lib/strategies.py):

  L1 官网归档页    discover2.find_archive_v2 按 engine (tavily/grok) 分派定位归档页,
                    未定位到时降级 find_archive_via_search (纯 Tavily), 再 fetch,
                    再 classify_pdf_links 判页内 PDF 链接清单
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
from dateutil import parser as _date_parser
from dateutil.parser import ParserError as _DateParserError

from . import extract as ex_mod
from .client import Client
from .search import TavilyError, multi_query_search

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
    search_sources: List[str] = field(default_factory=list)  # 搜索引擎 (Tavily/Grok) 返回的真实 URL
    search_queries: List[str] = field(default_factory=list)
    # v2 (discover2.find_archive_v2) 抓页时看见的所有 PDF URL (未筛). v1 不填。
    discovered_pdfs: List[str] = field(default_factory=list)
    # v2 已用 classify_pdf_links 判定好的 [(ym, url)]. 非空时 probe_l1_official
    # 直接采用, 不再重判一次 -- 定位归档页与列出月报本是同一次判断的两个用途,
    # 重复调用只是白烧一次 API。
    discovered_links: List[Tuple[str, str]] = field(default_factory=list)


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

# 月份名解析交给 dateutil (2026-07 教训: 手写正则堆月份缩写字典打地鼠打不完 --
# Stake 用 "Sept" 4 字母缩写, 旧版字典只有 3 字母 "sep", 正则被多出的 "t" 卡住
# 整体失配, 该月 PDF 静默消失、不进 confirmed_gaps、看起来像"文档没有这个月"。
# dateutil 自带经过多年打磨的月份名表 (各种缩写/全称/语言变体全认), 不用我们
# 自己维护字典。code 只保留两类它管不好/不该它管的事:
#   (a) 纯数字格式 (YYYYMMDD/YYYY-MM 等) -- 无自然语言歧义, dateutil 对无
#       分隔紧凑数字反而会解析失败或猜错 (实测), 这类"重复格式化"归 code。
#   (b) "月份名 + 裸 2 位数字, 无 4 位年" 时 2 位数字到底是"年"还是"日" 的
#       消歧 -- 本项目文件名惯例这个位置永远是年份 (从不编码"日"), 这是本
#       项目的业务解读, dateutil 通用逻辑猜不到 (它会当"日"处理), 由 code
#       兜底把 2 位数展开成 4 位年再喂给 dateutil 认月份名。
# 双哨兵交叉验证 (_D1/_D2 相隔近 200 年): 同一段文本用两个天差地别的 default
# 各解析一次, 年/月字段若两次结果一致, 说明是文本里真解出来的; 不一致则是
# dateutil fuzzy 模式把无关词当噪声跳过后从 default 继承的, 不可信 (防止
# "report26.pdf" 这种非月份词被误判成"1月", 月份被瞎猜)。
_D1 = datetime.datetime(2094, 3, 3)
_D2 = datetime.datetime(1904, 9, 9)
_ALPHA_WORD_RE = re.compile(r"[A-Za-z]{3,9}")
_YEAR4_RE = re.compile(r"20\d{2}")
_BARE_WORD_YEAR2_RE = re.compile(r"([A-Za-z]{3,9})[\-_\s\.]*(\d{2})(?!\d)")
_BARE_YEAR2_WORD_RE = re.compile(r"(?<!\d)(\d{2})[\-_\s\.]*([A-Za-z]{3,9})")


def _month_from_word(word: str) -> Optional[int]:
    """word 单独喂给 dateutil, 双哨兵交叉验证是否被认成月份名 (不含 day/year 语境)."""
    try:
        dt1 = _date_parser.parse(word, fuzzy=True, default=_D1)
        dt2 = _date_parser.parse(word, fuzzy=True, default=_D2)
    except (_DateParserError, ValueError, OverflowError, TypeError):
        return None
    if dt1.month != dt2.month:
        return None
    return dt1.month


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

    candidate = re.sub(r"[_.]+", " ", text)
    if not _ALPHA_WORD_RE.search(candidate):
        return None

    # 2) 含 4 位年: 整段交给 dateutil, 双哨兵确认年/月都是文本里真解出来的
    if _YEAR4_RE.search(candidate):
        try:
            dt1 = _date_parser.parse(candidate, fuzzy=True, default=_D1)
            dt2 = _date_parser.parse(candidate, fuzzy=True, default=_D2)
        except (_DateParserError, ValueError, OverflowError, TypeError):
            return None
        if dt1.year == dt2.year and dt1.month == dt2.month:
            return f"{dt1.year:04d}-{dt1.month:02d}"
        return None

    # 2b) 只有裸 2 位数字, 无 4 位年 -- 先确认相邻的字母段真是月份名, 再把 2 位
    # 数字当年份展开 (不是"日")。语序不定, 两种顺序都试。
    m2 = _BARE_WORD_YEAR2_RE.search(candidate) or _BARE_YEAR2_WORD_RE.search(candidate)
    if not m2:
        return None
    g1, g2 = m2.group(1), m2.group(2)
    word, yr2s = (g1, g2) if g1.isalpha() else (g2, g1)
    mon = _month_from_word(word)
    if mon is None:
        return None
    yr2 = int(yr2s)
    if not (19 <= yr2 <= 30):  # 只接受 19..30 范围内的 2 位年 (2019-2030 有效, 与原规则一致)
        return None
    return f"20{yr2:02d}-{mon:02d}"


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


_PDF_HREF_RE = re.compile(r'<a[^>]+href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', re.I)


def _fetch(url: str, timeout: int = DEFAULT_HTTP_TIMEOUT) -> Optional[str]:
    """抓页: 先轻量 requests, 内容看起来完整才用; 否则升级到浏览器渲染.

    多数官网归档页是服务端直出的静态/SSR 页 (Stake 实测 hellostake.com 服务端
    渲染, requests 就能拿到全部 34 个 PDF 链接, 完全不需要跑浏览器)。真正需要
    JS 渲染的是 AJAX/SPA 归档 (GCI 的 wp-load-posts, JCB 表格等) -- 这类页面
    首屏 HTML 里通常没有真实 `<a href>`。

    早期版本用"页面里有没有至少一个 <a href>"判断渲染是否完整 -- 错了: Stake
    的 Zendesk 支持文章页 (performance-updates-and-statements) requests 抓下来
    有 219 个导航/页脚 href, 但 0 个 PDF href (附件走 Zendesk JS API 异步注入),
    条件命中却跳过了 playwright, 34 份 PDF 全丢 (2026-07 回归事故)。改用"页面
    里有没有至少一个 .pdf href"这个更贴近目标的信号 -- 没有 PDF href 才值得
    升级到浏览器渲染, 避免只因页面有导航菜单就误判"内容完整"。
    """
    html = _fetch_requests(url, timeout)
    if html and _PDF_HREF_RE.search(html):
        return html
    rendered = _fetch_playwright(url, timeout)
    return rendered or html


def _fetch_playwright(url: str, timeout: int = DEFAULT_HTTP_TIMEOUT) -> Optional[str]:
    """浏览器渲染抓页 (playwright chromium headless), 仅 requests 判断内容不完整时才走.

    network_idle=True 等到 500ms 无网络请求, 保证 AJAX 加载完成.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
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
        return None


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


def find_archive_via_search(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    *,
    client: Optional[Client] = None,
) -> ArchivePointer:
    """L1 阶段 A (搜索) + 阶段 B (判 JSON).

    v1 兜底路径, 只在 v2 (discover2.find_archive_v2) 完全空手时才被调用。
    纯 Tavily REST 一次调用给结构化真 URL, 无 grounding 中间层, 无幻觉。
    """
    if client is None:
        client = Client()

    # --- 阶段 A: 搜索 ---
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
    # Spec G: 此处原有 sub2api web_search 兜底 (messages_with_search + grounding
    # 展开), 已删 -- 命中率仅 53%, 会幻觉 URL (Yarra 一测抓到 yarracapital.com,
    # 真域是 yarracm.com), grounding 跳板需直连 Google 1e100.net 国内 20s 卡死。

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

    # 兜底: 无 archive/latest 但有 sources, 挑相关度最高的**页面**。
    #
    # Spec G 10.6: 这里曾有一段"优先 PDF"捷径 -- 直接扫 real_sources 取第一个
    # 以 .pdf 结尾的 URL 当月报, 不抓页、不验域名归属、不做内容打样。实证 Tavily
    # 搜 GCI 时首位结果是第三方理财顾问站 pricefinancial.com.au 转贴的 factsheet,
    # 一旦其文件名能解析出 ym 就会被当作官方月报入库。已删除。
    #
    # 统一规矩: 搜索层只回答"哪一页", PDF 链接一律只能来自真实抓取的页面 HTML
    # (由 discover2.probe_urls 从 <a href> 正则抽取)。
    if not archive_url and not latest_pdf_url and real_sources:
        if final_domain:
            fund_tokens = re.findall(r"[a-z]+", fund_name.lower())
            fund_tokens = [t for t in fund_tokens if len(t) >= 4 and t not in {"fund", "trust", "capital"}]
            best_score = -1
            best_url = None
            for s in real_sources:
                if not _same_host(s, final_domain):
                    continue
                # 这里挑的必须是"页面", 不是 PDF -- 否则当 _pick_issuer_domain
                # 因关键词未命中而回退选中了三方站域名时 (如 pricefinancial.com.au
                # 转贴 GCI factsheet), 同域下唯一/最高分候选可能正好就是那份被转
                # 贴的 PDF 本身, 从而绕过本 task (Spec G 10.6) 要堵的口子。
                if s.lower().endswith(".pdf"):
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


_HREF_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\']', re.I)


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


def extract_all_pdf_links(html: str, base_url: str) -> List[str]:
    """归档页 HTML -> 页内**全部** PDF 直链绝对 URL (去重保序), 不做任何筛选.

    扫全文, 无长度截断 -- parse_archive_page 原先把 html 截到 80KB 才交给 LLM,
    归档页头部 (导航/脚本/样式) 一旦超过这个长度 (Stake 归档页实测 149KB, 全部
    34 个 PDF 链接落在 8.3~14 万字节区间) LLM 一个链接都看不到, 返回 0 links;
    且固定截断长度对长历史基金注定失效 (10 年约 120 份月报, 20 年约 240 份)。

    "哪些是本基金的月报" 的判断**不在这里** -- 见 classify_pdf_links。本函数
    只负责如实列出页面上真实存在的链接, 是反捏造链条的第一环 (下游只能从这份
    清单里按编号挑, 无法凭空产出 URL)。
    """
    seen: Set[str] = set()
    out: List[str] = []
    for href in _HREF_RE.findall(html):
        full = urljoin(base_url, href)
        if not full.lower().split("?", 1)[0].endswith(".pdf"):
            continue
        if full in seen:
            continue
        seen.add(full)
        out.append(full)
    return out


class ClassifyError(RuntimeError):
    """classify_pdf_links 整批判定失败 (模型不可达/回文不可解析).

    刻意抛出而非静默跳过: 静默跳一批 = 该批覆盖的月份全部消失, 且会被上游当作
    "页面上压根没有这些月" 记进 confirmed_gaps (exhausted_levels=no_link_found),
    即把"我们没问成"谎报成"发行商没发布"。CLAUDE.md 一.3 对缺口零容忍。
    """


# 单次送模型的链接条数上限。240 份月报 (20 年) 一次全塞进去, 输出侧 240 条
# {"i":N,"ym":"YYYY-MM"} 会撞 max_tokens 被截断 -- 截断即静默丢月份, 正是要
# 消灭的失效模式。分批把每次输出压在几百 token 内。
SELECT_BATCH = 60
_SELECT_MAX_TOKENS = 4096
_SELECT_ATTEMPTS = 2


def classify_pdf_links(
    urls: List[str],
    fund_name: str,
    *,
    client: Optional[Client] = None,
) -> Tuple[List[Tuple[str, str]], List[Dict[str, str]], int]:
    """PDF 链接清单 -> [(ym, url)] 月报清单. 判定交给 LLM, 映射与校验交给代码.

    取代原先散在 7 处的"手写文件名规则"(黑名单 + fund_name token 打分 +
    _parse_ym_from_text 反解)。那套规则两个方向都错, 且每处各写一份:
      - 放过: "current-product-disclosure-statement-gryphon.pdf" (黑名单只有
        缩写 "pds", 没有拼开的 "product disclosure statement")
      - 拦住: "Monthly-Fact-Sheet-June-2026.pdf" (黑名单有 "fact_sheet", 但对
        把月报就叫 fact sheet 的发行商, 这是静默丢掉全部真月报)
      - 误收: "Yarra-Australian-Equities-Fund-30-June-2024.pdf" 反解出 2024-06
        且不在黑名单, 会被当成本基金 2024-06 月报入库 (实为兄弟基金)
    文件名本身信息是够的 -- 不可靠的是用固定规则去匹配它。

    反捏造 (与 parse_archive_page 白名单同源, 更严): 模型只被允许回**编号**,
    禁止输出 URL; 代码按编号从入参 urls 取回真实链接。模型给的编号越界或 ym
    不合法一律丢弃并计入 dropped。ym 只是初判, 真正的月份闸在
    extract.extract_from_pdf (读 PDF 正文核对 expected_ym)。

    返回 (pairs, rejected, dropped):
      pairs    -- _dedup_links 后的 [(ym, url)]
      rejected -- [{"url": ..., "why": ...}] 模型判定"不是本基金月报"的条目, 供
                  日志/evidence 展示 (不是错误, 是正常筛除)
      dropped  -- 模型回答里代码拒绝采信的条数 (编号越界 / ym 非法)
    整批不可判 (模型不可达或回文非 JSON) -> 抛 ClassifyError, 不静默返回空。
    """
    if not urls:
        return ([], [], 0)
    if not fund_name:
        raise ValueError("classify_pdf_links 需要 fund_name 才能判断归属")
    if client is None:
        client = Client()

    template = _load_prompt("select_monthly_reports.md")
    pairs: List[Tuple[str, str]] = []
    rejected: List[Dict[str, str]] = []
    dropped = 0

    for start in range(0, len(urls), SELECT_BATCH):
        chunk = urls[start:start + SELECT_BATCH]
        listing = "\n".join(f"{i}. {u}" for i, u in enumerate(chunk, 1))
        prompt = (template
                  .replace("{fund_name}", fund_name)
                  .replace("{listing}", listing))
        obj: Optional[Dict[str, Any]] = None
        last_err = ""
        for _attempt in range(_SELECT_ATTEMPTS):
            try:
                resp = client.messages(prompt, max_tokens=_SELECT_MAX_TOKENS)
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                continue
            obj = _parse_json_response(resp.text)
            if obj is not None:
                break
            last_err = "回文不含可解析 JSON"
        if obj is None:
            raise ClassifyError(
                f"链接 {start + 1}~{start + len(chunk)} 判定失败 ({last_err}); "
                f"不静默跳过, 否则这些月份会被谎报为发行商未发布"
            )

        for item in obj.get("reports") or []:
            if not isinstance(item, dict):
                dropped += 1
                continue
            try:
                i = int(item.get("i"))
            except (TypeError, ValueError):
                dropped += 1
                continue
            if not 1 <= i <= len(chunk):
                dropped += 1
                continue
            ym = str(item.get("ym") or "").strip()
            if not _valid_ym(ym):
                dropped += 1
                continue
            pairs.append((ym, chunk[i - 1]))

        for item in obj.get("rejected") or []:
            if not isinstance(item, dict):
                continue
            try:
                i = int(item.get("i"))
            except (TypeError, ValueError):
                continue
            if 1 <= i <= len(chunk):
                rejected.append({"url": chunk[i - 1],
                                 "why": str(item.get("why") or "")})

    return (_dedup_links(pairs), rejected, dropped)


_PAGINATION_HINT_RE = re.compile(r"(load\s*more|next\s*page|archiveyear|[?&]page=)", re.I)


def parse_archive_page(
    html: str,
    *,
    fund_name: str,
    client: Optional[Client] = None,
    base_url: str = "",
) -> Tuple[List[Tuple[str, str]], bool, str, int]:
    """L1 第二步: 把已抓好的归档页解析出本基金的月报 PDF 链接.

    两步都不猜: 代码列出页内全部 PDF 直链 (extract_all_pdf_links, 扫全文无截断),
    LLM 只按编号判"哪几条是本基金月报、各是哪个月"(classify_pdf_links)。

    原来这里有两条并行通路 (正则黑名单快路 + 失败退回 LLM 读 80KB HTML), 现在
    合成一条: LLM 路只能返回 <a href> 白名单里的 URL, 而白名单恰好就是
    extract_all_pdf_links 的产物, 所以那条通路能给出的东西是新通路的子集, 留着
    只是多一份要各自维护的判断逻辑 (打地鼠的根源)。

    返回 (links, has_more_pages, next_page_hint, unparseable_count)。
    has_more_pages 是启发式猜测 (页内是否有分页/load-more 字样), next_page_hint
    恒为空串 -- 分页遍历本身当前是死代码, 另案处理。
    unparseable_count 只计"模型回答里代码拒绝采信的条数", 不含被正常筛除的
    PDS/TMD 等非月报文件。
    """
    urls = extract_all_pdf_links(html, base_url) if base_url else []
    pairs, _rejected, dropped = classify_pdf_links(urls, fund_name, client=client)
    has_more = bool(_PAGINATION_HINT_RE.search(html))
    return (pairs, has_more, "", dropped)


def probe_l1_official(
    fund_name: str,
    issuer: str,
    issuer_domain: Optional[str] = None,
    asx_code: Optional[str] = None,
    *,
    client: Optional[Client] = None,
    max_pagination: int = 8,
    engine: str = "tavily",
) -> Tuple[List[Tuple[str, str]], ArchivePointer, int]:
    """L1 完整流程: 找归档页 -> 列出页内 PDF 链接 -> classify_pdf_links 判月报.

    优先走 v2 (discover2.find_archive_v2): 搜索 + 排序 + 抓页 + 判链接清单。
    v2 未定位到归档且未定位到最新单份时, 回退 v1 (find_archive_via_search) 兜底。

    返回 (links, pointer, unparseable_total).
    """
    if client is None:
        client = Client()
    # v2 首选
    from . import discover2 as d2
    pointer = d2.find_archive_v2(
        fund_name, issuer, issuer_domain, asx_code, client=client, engine=engine,
    )
    # v2 完全空 (无 archive 且无 latest_pdf) 时降级 v1 (可能 v1 web_search 拿到 v2 没找到的 URL)
    if not pointer.archive_url and not pointer.latest_pdf_url:
        pointer = find_archive_via_search(fund_name, issuer, issuer_domain, asx_code, client=client)

    # v2 快路: find_archive_v2 定位归档页时已经对该页全部 PDF 链接跑过
    # classify_pdf_links, 结果就在 discovered_links 里, 直接采用。
    if pointer.discovered_links:
        return (_dedup_links(list(pointer.discovered_links)), pointer, 0)

    # 无 archive_url: 单份场景 (v1 兜底只给出 latest_pdf_url)。同样交
    # classify_pdf_links 判, 不再手写文件名反解 -- 一条链接一次调用, 代价可忽略,
    # 换掉一处独立的判断逻辑。
    if not pointer.archive_url:
        if pointer.latest_pdf_url:
            pairs, _rej, _dr = classify_pdf_links(
                [pointer.latest_pdf_url], fund_name, client=client)
            return (pairs, pointer, 0)
        return ([], pointer, 0)

    aggregate: List[Tuple[str, str]] = []
    unparseable_total = 0

    def _one_page(url: str) -> Tuple[List[Tuple[str, str]], bool, str, int]:
        html = _fetch(url) or _curl(url) or ""
        if not html:
            return ([], False, "", 0)
        return parse_archive_page(html, fund_name=fund_name, client=client,
                                  base_url=url)

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
    fund_name: str,
    *,
    client: Optional[Client] = None,
) -> List[Tuple[str, str]]:
    """L2: 用 CDX API 查 issuer_domain 快照, 从 original URL 提月份补 gap_set 中的洞.

    每月最多 CDX_SNAPSHOTS_PER_MONTH 快照 (抄自 strategies.py 补1).

    Spec G 10.2: CDX 查的是整个发行商域名下的全部 PDF。一家发行商旗下多支基金
    的文件同处一域, 若只按"文件名月份落在缺口内"筛选, 兄弟基金的月报会被当作
    本基金数据填进缺口。而本步专用于补缺口 -- CLAUDE.md 一.3 对缺口是零容忍、
    禁填补的, 这里必须是全系统最严的地方, 不是最宽的。归属与月份判定统一走
    classify_pdf_links (与 L1 同一处逻辑), 本函数只负责:
      (a) 先用 _parse_ym_from_text 粗筛掉月份明显不在缺口内的快照, 把送模型的
          清单从 CDX 的上千条压到几十条 (纯为省调用, 不作最终判据)
      (b) 判定通过后按 ym 套 CDX_SNAPSHOTS_PER_MONTH 快照数上限
    """
    if not gap_set or not issuer_domain:
        return []
    patterns = [f"{issuer_domain}/*", f"{issuer_domain}/wp-content/uploads/*"]

    # ---- 第一趟: 粗筛月份落在缺口内的快照 ----
    cands: List[Tuple[str, str, str]] = []  # (ym, ts, original)
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
            cands.append((ym, ts, original))

    if not cands:
        return []

    # ---- 第二趟: 归属+月份交 classify_pdf_links 判 (同一处逻辑, 无第二套规则) ----
    # 同一份文件可能有多个快照 (ts 不同, original 相同), 按 original 去重后再问,
    # 免同一条链接反复送模型。
    uniq_urls: List[str] = []
    _seen_u: Set[str] = set()
    for _ym, _ts, original in cands:
        if original not in _seen_u:
            _seen_u.add(original)
            uniq_urls.append(original)
    try:
        pairs, _rej, _dr = classify_pdf_links(uniq_urls, fund_name, client=client)
    except ClassifyError:
        # 补缺口是可选增益, 判不了就不补 (缺口继续如实留着), 不因此中断整轮
        return []
    keep = {url: ym for ym, url in pairs}

    snap_count: Dict[str, int] = {}
    hits: List[Tuple[str, str]] = []
    for _ym_guess, ts, original in cands:
        ym = keep.get(original)
        if not ym or ym not in gap_set:
            continue
        if snap_count.get(ym, 0) >= CDX_SNAPSHOTS_PER_MONTH:
            continue
        snap_count[ym] = snap_count.get(ym, 0) + 1
        hits.append((ym, f"https://web.archive.org/web/{ts}/{original}"))
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
    engine: str = "tavily",
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
        fund_name, issuer, issuer_domain, asx_code, client=client, engine=engine,
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
        "locate": (pointer.raw or {}).get("locate", {}),
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
                    # Spec G 10.3 曾在这里漏了基金名过滤 (直接 discovered_pdfs =
                    # next_pdfs 整页透传, 兄弟基金月报会入库)。现在一律走
                    # classify_pdf_links, 归属判断与月份判断在同一处, 不再有
                    # "某条通路忘了过滤" 这种漏洞面。
                    nav_pairs, _nav_rej, _nav_dr = classify_pdf_links(
                        next_pdfs, fund_name, client=client)
                    if nav_pairs:
                        obtained.update(ym for ym, _ in nav_pairs)
                        aggregate.extend(nav_pairs)
                        report.per_level_contribution["L1_nav"] = len(nav_pairs)
                        report.per_level_source["L1_nav"] = next_url or ""
                        # 更新 pointer 让下游知道真归档在哪
                        pointer.archive_url = next_url
                        pointer.discovered_pdfs = list(next_pdfs)
                        pointer.discovered_links = list(nav_pairs)
                        pointer.raw["nav_hops"] = nav_hops
                    report.evidence_log.append({
                        "level": "L1_nav", "count": len(nav_pairs),
                        "from_url": pointer.archive_url if not nav_pairs else "(navigated)",
                        "to_url": next_url,
                        "evidence": f"导航 1 跳, 页内 {len(next_pdfs)} 份 PDF, "
                                    f"{len(nav_pairs)} 份判定为本基金月报",
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
            l2_links = probe_l2_wayback(dom_clean, gap_set, fund_name, client=client)
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
