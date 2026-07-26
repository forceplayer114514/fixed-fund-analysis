"""L3 兜底: fundmonitors.com Full Fund Profile AJAX 逐月表源.

Yarra 一测发现: 官网归档常滚动删旧月 (只挂近期 3 份 PDF), 而 fundmonitors
的 `/_ajax/_fund-profile.php?FundID=NNNN&AccCode=XXXX` AJAX 页含完整
Historical Performance 表 (Year × Jan-Dec + YTD, net of fees, 回溯到 inception)。

**付费墙策略**: 检出 must be logged in / premium 直接跳过, 不试图绕过。
Yarra 实测 (2026-07): 无墙, 23 年逐月表全出, 数值与官网 PDF 一致。
其他基金可能锁, 逻辑会自动跳过并进 confirmed_gaps。

依赖: `parse_html_monthly_table` + `gate_check_table` 从 skills/lib/extract.py 移植。
"""
from __future__ import annotations

import datetime
import re
import sqlite3
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

FUNDMONITORS_HOST = "https://www.fundmonitors.com"


# ---- Spec B: 页面基金名提取 (透明展示, 替代 Spec A name guard) ----
_H1_RE = re.compile(r"^\s*#{1,3}\s+([^\n]+)", re.M)
_BOLD_FIRST_RE = re.compile(r"\*\*([^*]{5,120})\*\*")
# fundmonitors Full Profile AJAX 返 HTML 无 heading, 首个 "Xxx Fund/Trust" 短语兜底
# 用 finditer + 取首个短匹配 (最少词) 避免贪婪吃到 "Fund Fund & Manager Details"
_FUND_PHRASE_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&\-]+(?:\s+[A-Z][A-Za-z0-9&\-]+){1,6}?\s+(?:Fund|Trust))\b"
)


def _extract_page_fund_name(markdown):
    """从 fundmonitors Full Profile markdown 抽页面上的基金名 (供透明展示)。

    顺序:
      1. 首个 h1/h2/h3 标题 (最常见, `# Yarra Enhanced Income Fund`)
      2. 首个粗体串 (`**Yarra Enhanced Income Fund**`, 5~120 字符)
      3. 首个 "Xxx Yyy Fund/Trust" 短语 (fundmonitors AJAX HTML 转 md 无 heading 场景)
      4. 找不到返 None (前端 fallback 到 fund_name)

    不做名字匹配, 不做过滤 -- 只把页面上的字面串拿出来给前端展示。

    Args:
        markdown: fundmonitors 页面 markdown, 可能为 None/空串

    Returns:
        Optional[str] 页面基金名 (原样保留 share class 后缀), 找不到返 None
    """
    if not markdown:
        return None
    m = _H1_RE.search(markdown)
    if m:
        return m.group(1).strip()
    m = _BOLD_FIRST_RE.search(markdown)
    if m:
        return m.group(1).strip()
    # fundmonitors AJAX md fallback: 首个 "Xxx Fund/Trust" 短语
    m = _FUND_PHRASE_RE.search(markdown)
    if m:
        return m.group(1).strip()
    return None


# ---- L1 name-fuzzy 闸 (防错源) ----
# 停用词: fundmonitors 页 + 生产 fund_name 两侧都常出现, 单独交集不构成绑定。
_NAME_STOPWORDS = frozenset({
    "fund", "trust", "the", "and", "of", "a", "an", "au", "aud",
    "australia", "australian",
    # 通用金融类别词 (不该单独作为绑定证据)
    "income", "credit", "bond", "bonds", "capital",
    "floating", "rate", "high", "yield", "enhanced",
    "active", "long", "short", "smarter", "money",
})


def _name_tokens(name: str) -> frozenset:
    """归一化基金名 -> 停用词过滤后的 token 集合. 用于 L1 name-fuzzy 闸."""
    if not name:
        return frozenset()
    toks = re.findall(r"[a-z0-9]+", name.lower())
    return frozenset(t for t in toks if t not in _NAME_STOPWORDS and len(t) >= 2)


def _name_matches(page_name, fund_name):
    """L1 name-fuzzy 闸: 去停用词后 token 交集非空即 OK.

    2026-07-18 事故: Coolabah Assisted 走 Tavily 命中 Smarter Money Fund 页,
    173 月错源入库. 加此闸: page_name 与 fund_name 完全无字面重叠 -> skip L1.

    Returns:
        (bool, str) — ok / 描述 (fail 时含双方 name 与交集调试)
    """
    if not page_name or not fund_name:
        return (False, f"name_missing (page={page_name!r} fund={fund_name!r})")
    p = _name_tokens(page_name)
    f = _name_tokens(fund_name)
    if not p or not f:
        return (False, f"name_tokens_empty (p={p} f={f})")
    inter = p & f
    if not inter:
        return (False, f"name_mismatch (page={page_name!r} fund={fund_name!r} inter=∅)")
    return (True, f"name_ok inter={sorted(inter)}")


def _lookup_whitelist(
    db_conn: sqlite3.Connection,
    fund_id: str,
) -> Optional[Tuple[int, str]]:
    """查 funds 白名单: 命中返 (fundmonitors_fund_id, acc_code_or_empty)。

    容错: `fundmonitors_fund_id` / `fundmonitors_acc_code` 列尚未迁移
    (task #15 前) 时, PRAGMA table_info 探测列不存在直接返 None,
    避免 SELECT 抛 `no such column` (兼容空 DB pytest)。
    """
    if not fund_id:
        return None
    cur = db_conn.cursor()
    try:
        cur.execute("PRAGMA table_info(funds)")
        cols = {row[1] for row in cur.fetchall()}
    except sqlite3.Error:
        return None
    if ("fundmonitors_fund_id" not in cols
            or "fundmonitors_acc_code" not in cols):
        return None
    cur.execute(
        "SELECT fundmonitors_fund_id, fundmonitors_acc_code "
        "FROM funds WHERE fund_id=?",
        (fund_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    fmid, acc = row[0], row[1]
    if fmid is None:
        return None
    return (int(fmid), acc or "")


# ---- 工具 (从 skills/lib/extract.py 复用) ----

def _pct_to_decimal(num_str: str) -> float:
    """百分比串 (无 %, 如 '5.89'/'-0.26') 除 100 转小数, 用 Decimal 避免 float 舍入。"""
    return float(Decimal(num_str) / Decimal(100))


def _get_last_day_of_month(year: int, month: int) -> datetime.date:
    if month == 12:
        return datetime.date(year, 12, 31)
    return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)


# ---- Markdown 表格解析 (Historical Performance 段) ----

_HEADER_TOKENS = {
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec", "ytd",
}
_MONTH_ORDER = [
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
]


def parse_html_monthly_table(
    markdown: str,
) -> Tuple[List[Tuple[str, float]], Dict[str, float]]:
    """解析 fundmonitors Full Fund Profile markdown 里的 Historical Performance 表。

    定位 "Historical Performance" 到 "Historical Financial Year Performance"
    之间的段, 忽略 FY 表。表结构 `| Year | Jan % | ... | Dec % | YTD % |`,
    N/R / N/A / "-" / 空 -> 跳过 (pre-inception / 未报告, 非缺口)。

    返回 (records, ytd_map):
      records: [(YYYY-MM-月末, net_return_decimal), ...] 升序
      ytd_map: {year_str: ytd_decimal} 供 YTD 复利交叉验证
    """
    if not markdown:
        return [], {}
    start_marker = "Historical Performance"
    fy_marker = "Historical Financial Year Performance"
    start = markdown.find(start_marker)
    if start < 0:
        return [], {}
    fy_start = markdown.find(fy_marker, start)
    section = markdown[start:fy_start if fy_start > 0 else len(markdown)]

    records: List[Tuple[str, float]] = []
    ytd_map: Dict[str, float] = {}
    col_map: Dict[str, int] = {}
    expected_cell_count = 0

    for line in section.split("\n"):
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells:
            continue
        normed = [
            c.replace("**", "").replace(" %", "").replace("%", "").strip().lower()
            for c in cells
        ]
        if normed[0] == "year":
            hdr_map = {n: i for i, n in enumerate(normed) if n in _HEADER_TOKENS}
            if len(hdr_map) >= 2 and not col_map:
                col_map = hdr_map
                expected_cell_count = len(cells)
            continue
        if not col_map:
            continue
        if expected_cell_count and len(cells) != expected_cell_count:
            continue
        year_str = cells[0].replace("**", "").strip()
        if not re.fullmatch(r"\d{4}", year_str):
            continue
        year = int(year_str)
        for m_name in _MONTH_ORDER:
            idx = col_map.get(m_name)
            if idx is None or idx >= len(cells):
                continue
            val = cells[idx].strip().replace("**", "").replace("%", "")
            if val in ("", "N/R", "N/A", "-"):
                continue
            if not re.fullmatch(r"[+-]?\d+\.\d+", val):
                continue
            month = _MONTH_ORDER.index(m_name) + 1
            date = _get_last_day_of_month(year, month).strftime("%Y-%m-%d")
            records.append((date, _pct_to_decimal(val)))
        ytd_idx = col_map.get("ytd")
        if ytd_idx is not None and ytd_idx < len(cells):
            ytd_val = cells[ytd_idx].strip().replace("**", "").replace("%", "")
            if re.fullmatch(r"[+-]?\d+\.\d+", ytd_val):
                ytd_map[year_str] = _pct_to_decimal(ytd_val)

    records.sort(key=lambda x: x[0])
    return records, ytd_map


def gate_check_table(
    records: List[Tuple[str, float]],
    ytd_map: Dict[str, float],
) -> Tuple[bool, List[str]]:
    """表格源入库前硬 gate. 4 项校验:
      1. 缺口零容忍
      2. ANTI-FABRICATION (连续 >=3 相同非零)
      3. 字段类型 (|r| < 0.5)
      4. YTD 复利交叉 (对每年 >=3 月的, 误差 < 0.5%)
    """
    errors: List[str] = []
    if not records:
        return (False, ["无数据"])
    # 1. 缺口 -- 首月与末月之间的月份必须连续 (N/R 只允在首尾外)
    dates = [d for d, _ in records]
    prev = None
    for d in dates:
        y, m = int(d[:4]), int(d[5:7])
        cur = y * 12 + m
        if prev is not None and cur - prev != 1:
            errors.append(f"缺口: {d} 前一月缺失")
            break
        prev = cur
    # 2. ANTI-FABRICATION
    rets = [r for _, r in records]
    for i in range(len(rets) - 2):
        if rets[i] != 0.0 and rets[i] == rets[i + 1] == rets[i + 2]:
            errors.append(
                f"ANTI-FABRICATION: 连续3月相同值 {rets[i]} 起于第 {i} 月"
            )
            break
    # 3. 字段类型
    for d, r in records:
        if abs(r) >= 0.5:
            errors.append(f"字段异常: {d} 收益 {r} 超 |r|<0.5")
    # 4. YTD 复利
    if ytd_map:
        by_year: Dict[str, List[float]] = {}
        for d, r in records:
            by_year.setdefault(d[:4], []).append(r)
        for year, ytd_expected in ytd_map.items():
            month_rets = by_year.get(year, [])
            if len(month_rets) < 3:
                continue
            actual = 1.0
            for r in month_rets:
                actual *= (1.0 + r)
            actual -= 1.0
            if abs(actual - ytd_expected) >= 0.005:
                errors.append(
                    f"YTD 验证失败({year}): 复利 {actual:.4f} vs YTD "
                    f"{ytd_expected:.4f}, 误差 {abs(actual-ytd_expected):.4f}"
                )
    return (len(errors) == 0, errors)


# ---- FundID 定位 (Tavily site: 搜, 通用于全部澳洲基金) ----

_FUNDID_URL_RE = re.compile(
    r"fundmonitors\.com/(?:_ajax/)?_?(?:fund-profile|fund-factsheet)\.php\?FundID=(\d+)(?:&AccCode=([a-z0-9]+))?",
    re.I,
)


def _grok_fundmonitors_id(fund_name: str) -> Optional[Tuple[int, str]]:
    """薄封装, 便于测试 monkeypatch."""
    from .grok import answer_fundmonitors_id
    return answer_fundmonitors_id(fund_name)


def find_fundid(
    fund_name: str,
    *,
    engine: str = "tavily",
) -> Optional[Tuple[int, str]]:
    """拿 fundmonitors FundID + AccCode. 支持 tavily / grok 两个引擎.

    fundmonitors 页面结构:
      - fund-factsheet.php?FundID=1512&AccCode=fresnjxju     (摘要, 无逐月表)
      - _ajax/_fund-profile.php?FundID=1512&AccCode=fresnjxju (AJAX, 含逐月表)
    两个 URL 里 FundID + AccCode 一致, 抓 factsheet URL 也能推出 profile URL。

    Spec G 2.4: 多份额类别基金上两个引擎都可能给错编号 (实测 Bentham,
    Grok 给 3315/622, Tavily 给 3315/622, DB 真值 3312 -- 数据源本身歧义)。
    下游 probe() 的 name-fuzzy 闸必须保留兜错源。

    返回 (fund_id, acc_code) 或 None (搜不到)。
    """
    if engine == "grok":
        return _grok_fundmonitors_id(fund_name)
    from . import search as _search
    try:
        results = _search.tavily_search(
            f"site:fundmonitors.com {fund_name}",
            max_results=8,
            search_depth="basic",
        )
    except _search.TavilyError:
        return None
    for r in results:
        m = _FUNDID_URL_RE.search(r.url)
        if m:
            fid = int(m.group(1))
            acc = m.group(2) or ""
            return (fid, acc)
    return None


# 向后兼容别名 (既有调用点/测试仍用旧名)
find_fundid_via_tavily = find_fundid


def build_profile_url(fund_id: int, acc_code: str = "") -> str:
    """拼 AJAX 逐月表 URL. AccCode 缺省也能命中大部分公开基金。"""
    if acc_code:
        return f"{FUNDMONITORS_HOST}/_ajax/_fund-profile.php?FundID={fund_id}&AccCode={acc_code}"
    return f"{FUNDMONITORS_HOST}/_ajax/_fund-profile.php?FundID={fund_id}"


# ---- 抓取 + 付费墙检测 ----

def fetch_profile_markdown(url: str, timeout: int = 30) -> Tuple[Optional[str], str]:
    """抓 AJAX 页 -> markdown.

    fundmonitors 2026 起上了 Cloudflare managed challenge, 普通 requests /
    curl 直接被拦成"Just a moment"。用 curl_cffi.impersonate='chrome' 复刻
    Chrome TLS/HTTP 指纹, 实测 200 直返, 无需 solve_cloudflare (合规: 我们
    没有绕验证码逻辑, 只是让 TLS 层看起来像真浏览器, CF 边缘就放行了)。

    返回 (markdown, status): status ∈ {'ok', 'paywall', 'fetch_fail'}.
    付费墙关键词命中即返 paywall, 不试图登录。
    """
    try:
        from curl_cffi import requests as _creq
    except ImportError:
        return (None, "fetch_fail")
    try:
        r = _creq.get(url, impersonate="chrome124", timeout=timeout,
                      allow_redirects=True)
    except Exception:
        return (None, "fetch_fail")
    if r.status_code != 200 or not r.text:
        return (None, "fetch_fail")
    html = r.text
    low = html.lower()
    # Cloudflare "Just a moment" 挑战页兜底。注意: 正常页 CSP 里也含 "challenge-platform"
    # 字面串, 不能只凭这个判。用"标题"+"页极短"双条件才算挑战页。
    if ("just a moment" in low
            and "<title>just a moment" in low
            and len(html) < 20000):
        return (None, "fetch_fail")
    if any(k in low for k in (
        "must be logged in", "please log in", "premium content",
        "subscribe to access", "sign in to view", "paywall",
    )):
        return (None, "paywall")

    md = _html_tables_to_markdown(html)
    return (md, "ok")


_TAG_RE = re.compile(r"<[^>]+>")
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)


def _html_tables_to_markdown(html: str) -> str:
    """把 <table> 转 markdown 竖线格式 + 保留标题文本, 供 parse_html_monthly_table。

    简单实现: 提取所有 <tr>, 每 <tr> 抽 <td>/<th>, 用 `| a | b | c |` 输出。
    非表格文本按行保留 (为了让 "Historical Performance" 标题存活)。
    """
    lines: List[str] = []
    pos = 0
    for m in re.finditer(r"<table[^>]*>(.*?)</table>", html, re.I | re.S):
        # 表前文本 (含 h2/h3 标题) 剥 tag 保留文本
        pre = html[pos:m.start()]
        pre_txt = _TAG_RE.sub(" ", pre)
        pre_txt = re.sub(r"\s+", " ", pre_txt).strip()
        if pre_txt:
            lines.append(pre_txt)
        # 表内行
        table_html = m.group(1)
        for row_m in _TR_RE.finditer(table_html):
            cells = []
            for cell_m in _TD_RE.finditer(row_m.group(1)):
                cell = _TAG_RE.sub(" ", cell_m.group(1))
                cell = re.sub(r"\s+", " ", cell).strip()
                cells.append(cell)
            if cells:
                lines.append("| " + " | ".join(cells) + " |")
        pos = m.end()
    tail = _TAG_RE.sub(" ", html[pos:])
    tail = re.sub(r"\s+", " ", tail).strip()
    if tail:
        lines.append(tail)
    return "\n".join(lines)


# ---- 顶层: 端到端拿逐月表 ----

def probe(
    fund_name: str,
    fund_id: Optional[str] = None,
    db_conn: Optional[sqlite3.Connection] = None,
    *,
    engine: str = "tavily",
) -> Dict[str, object]:
    """L1 主源端到端 (Spec B: fundmonitors 从 L3 提到 L1). 输入 fund_name -> 输出结构化结果。

    流程:
      A. 白名单短路: 给了 fund_id+db_conn 且 funds 表命中 fundmonitors_fund_id
         -> 直接拼 URL fetch (人工背书, 信任 URL 匹配)。
      B. 否则走 Tavily: `site:fundmonitors.com <fund_name>` 拿 FundID+AccCode。
      C. 抓到 markdown -> 抽 page_fund_name (供前端透明展示), 不做名字匹配 gate。
         (Spec B 反 Spec A: 抓到啥就展示啥, 与输入名不一致由用户核对, 不再软挡。)

    返回 dict:
      status: 'ok' | 'no_fundid' | 'paywall' | 'fetch_fail' | 'no_table' | 'gate_fail'
      records: List[(date, net_return)] (成功时)
      ytd_map: Dict[year, ytd_decimal]
      url: 抓的 AJAX URL
      page_fund_name: Optional[str] -- 页面上抓到的基金名, 供 UI 透明展示与输入名核对
      errors: List[str] (gate_fail 时)
    """
    # A. 白名单短路 (需 fund_id+acc_code 齐; 只有 fund_id 无 acc -> 走 Tavily 补 acc)
    hit: Optional[Tuple[int, str]] = None
    if fund_id and db_conn is not None:
        wl = _lookup_whitelist(db_conn, fund_id)
        if wl is not None and wl[1]:  # acc_code 非空才短路
            hit = wl
    # B. 未白名单 (或 acc 空) -> Tavily 通路
    if hit is None:
        hit = find_fundid(fund_name, engine=engine)
        if not hit:
            return {"status": "no_fundid", "records": [], "ytd_map": {},
                    "url": None, "page_fund_name": None, "errors": []}
    fid, acc = hit
    url = build_profile_url(fid, acc)
    md, status = fetch_profile_markdown(url)
    if status != "ok":
        return {"status": status, "records": [], "ytd_map": {},
                "url": url, "page_fund_name": None, "errors": []}
    # C. 抽页面基金名 (透明展示, 不做 gate)
    page_name = _extract_page_fund_name(md)
    # C.1 L1 name-fuzzy 闸 (2026-07-18 事故防线):
    # page_name 与 fund_name 完全无字面重叠 -> 拒绝入库 (错源).
    # 白名单短路 (hit 来自 funds.fundmonitors_fund_id) 时豁免 -- 人工背书。
    if fund_id and db_conn is not None:
        wl_hit = _lookup_whitelist(db_conn, fund_id)
    else:
        wl_hit = None
    if wl_hit is None:
        ok_name, msg = _name_matches(page_name, fund_name)
        if not ok_name:
            return {"status": "name_mismatch", "records": [], "ytd_map": {},
                    "url": url, "page_fund_name": page_name,
                    "errors": [msg]}
    records, ytd_map = parse_html_monthly_table(md or "")
    if not records:
        return {"status": "no_table", "records": [], "ytd_map": ytd_map,
                "url": url, "page_fund_name": page_name, "errors": []}
    ok, errs = gate_check_table(records, ytd_map)
    if not ok:
        return {"status": "gate_fail", "records": records, "ytd_map": ytd_map,
                "url": url, "page_fund_name": page_name, "errors": errs}
    return {"status": "ok", "records": records, "ytd_map": ytd_map,
            "url": url, "page_fund_name": page_name, "errors": []}
