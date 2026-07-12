"""skills 数据抓取/清洗辅助层：通用纯函数集合。

本模块是独立 Claude Code 工作区（skills/）的通用工具库，**不 import**
scripts/ 或 webapp 任何代码。所需通用函数从 scripts/parse_factsheet.py 与
scripts/fetch_web.py 复制而来，已去除对 registry/yaml/项目路径的依赖，做成
纯函数。

数据完整性原则（最高优先级）：
- 缺口检测只检测、不抛错、不插值、不捏造（由调用方决定如何处理缺口）。
- 日期解析失败返回 None，绝不猜测。
"""
from __future__ import annotations

import concurrent.futures
import datetime
import os
import re
from decimal import Decimal
from typing import Optional

import requests

# PyMuPDF 可选导入：纯函数（MONTH_MAP / clean_spacing / check_gaps 等）不依赖
# fitz，仅在 parse_pdf_text 调用时才需要。缺失时模块仍可导入，parse_pdf_text
# 会抛出带清晰提示的 ImportError。
try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover - 环境缺 PyMuPDF 时
    fitz = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 月份名 -> 数字映射（复制自 parse_factsheet.py 第 23-27 行）
# 含全称与缩写，sept/sep 均映射到 9。
# ---------------------------------------------------------------------------
MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# 下载文件默认 User-Agent（复制自 fetch_web.py 第 20-22 行 HEADERS）
_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def clean_spacing(text: str) -> str:
    """压缩多余空白（含制表符/换行）为单个空格，不焊接数字与脚注。

    复制自 parse_factsheet.py clean_spacing。原实现中曾用危险 lookbehind 把单
    字符间的空格删掉导致数字与脚注粘连，此处只用 \\s+ -> ' '，安全。
    """
    return re.sub(r"\s+", " ", text)


def get_last_day_of_month(year: int, month: int) -> datetime.date:
    """返回 (year, month) 该月最后一天的 date。

    复制自 parse_factsheet.py get_last_day_of_month。12 月特殊处理（下个月
    会跨年），其余月份取下月 1 号减 1 天。自动处理闰年 2 月。
    """
    if month == 12:
        return datetime.date(year, 12, 31)
    return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)


def extract_month_prefix(filename: str) -> Optional[str]:
    """从文件名/字符串提取 YYYY-MM，复制三种正则策略。

    复制自 parse_factsheet.py extract_month_prefix 的前三种策略（去除了第 4
    种 Metrics 专用 _YYMM 模式，因其基金特定）：
      1. YYYYMMD / YYYYMMDD（如 20250131-Report.pdf）
      2. YYYYMM（如 Report-202502.pdf）
      3. 月份名 + 年（任一顺序，如 April-2025 / 2025-March）
    无法识别返回 None。
    """
    # 1. YYYYMMDD / YYYYMMD（前后需非数字边界）
    date_match = re.search(r"(\b|[^0-9])(\d{4})(\d{2})(\d{1,2})(\b|[^0-9])", filename)
    if date_match:
        year = int(date_match.group(2))
        month = int(date_match.group(3))
        return f"{year}-{month:02d}"

    # 2. YYYYMM（前后需非数字边界）
    date_match_short = re.search(r"(\b|[^0-9])(\d{4})(\d{2})(\b|[^0-9])", filename)
    if date_match_short:
        year = int(date_match_short.group(2))
        month = int(date_match_short.group(3))
        return f"{year}-{month:02d}"

    # 3. 月份名 + 年（先尝试月份名在前，再尝试年在前）
    month_names_pattern = "|".join(MONTH_MAP.keys())
    m = re.search(r"(" + month_names_pattern + r")[-_]*(\d{4})", filename, re.IGNORECASE)
    if m:
        month = MONTH_MAP[m.group(1).lower()]
        year = int(m.group(2))
        return f"{year}-{month:02d}"

    m = re.search(r"(\d{4})[-_]*(" + month_names_pattern + r")", filename, re.IGNORECASE)
    if m:
        year = int(m.group(1))
        month = MONTH_MAP[m.group(2).lower()]
        return f"{year}-{month:02d}"

    return None


def parse_date_string(text: str) -> Optional[str]:
    """把各种日期文本格式统一解析成月末 YYYY-MM-DD。

    新增函数（供 LLM 提取的文本日期标准化）。复用 MONTH_MAP +
    get_last_day_of_month。支持格式（顺序匹配，首个命中即返回）：
      - ISO 数字：YYYY-MM、YYYY-MM-DD、YYYY/MM/DD（日被忽略，统一取月末）
      - 月份名 + 年（任一顺序，任意空白/短横/斜杠分隔）：
        "April 2025"、"Apr-2025"、"2025-March"、"Sept 2025" 等
    解析失败返回 None（绝不猜测年份或月份）。
    """
    if not text:
        return None

    # 1. ISO 数字格式：年-月[-日]，分隔符可为 - 或 /
    iso = re.search(r"(\d{4})[-/](\d{1,2})(?:[-/](\d{1,2}))?", text)
    if iso:
        year = int(iso.group(1))
        month = int(iso.group(2))
        if 1 <= month <= 12 and 1 <= year <= 9999:
            return get_last_day_of_month(year, month).strftime("%Y-%m-%d")

    # 2. 月份名 + 年（月份名在前）
    month_names_pattern = "|".join(MONTH_MAP.keys())
    m = re.search(
        r"(" + month_names_pattern + r")[-_\s/]*(\d{4})", text, re.IGNORECASE
    )
    if m:
        month = MONTH_MAP[m.group(1).lower()]
        year = int(m.group(2))
        if 1 <= year <= 9999:
            return get_last_day_of_month(year, month).strftime("%Y-%m-%d")

    # 3. 年 + 月份名（年在前）
    m = re.search(
        r"(\d{4})[-_\s/]*(" + month_names_pattern + r")", text, re.IGNORECASE
    )
    if m:
        year = int(m.group(1))
        month = MONTH_MAP[m.group(2).lower()]
        if 1 <= year <= 9999:
            return get_last_day_of_month(year, month).strftime("%Y-%m-%d")

    return None


def check_gaps(dates: list[str]) -> list[str]:
    """检测月末日期序列中的缺失月份，返回缺失的 YYYY-MM 列表。

    重新实现（参考 parse_factsheet.check_gaps 与 webapp metrics_pipeline
    ._find_month_gaps 的思路）。与原版差异：
      - 输入为日期字符串列表（YYYY-MM-DD，可乱序），不再是 dict 列表；
      - **只检测、不抛错**，返回首尾月份之间所有未出现的 YYYY-MM；
      - 内部排序后再走查，因此乱序输入也能正确检测；
      - 少于 2 个点返回空列表。
    """
    if len(dates) < 2:
        return []

    # 解析为 date 并排序，容忍乱序输入
    parsed: list[datetime.date] = []
    for d in dates:
        parsed.append(datetime.date.fromisoformat(d[:10]))
    parsed.sort()

    start = parsed[0].replace(day=1)
    end = parsed[-1].replace(day=1)

    expected: list[str] = []
    cur = start
    while cur <= end:
        expected.append(cur.strftime("%Y-%m"))
        # 用 32 天跨越当前月末，再归零日，得到下个月第一天
        cur = (cur.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)

    actual_set = {d[:7] for d in dates}
    return [m for m in expected if m not in actual_set]


def download_file(url: str, filepath: str, headers: Optional[dict] = None) -> None:
    """用 requests 下载文件到 filepath，失败抛异常。

    复制自 fetch_web.py download_file 的核心逻辑。默认带 User-Agent header
    （复制自 fetch_web.HEADERS）；可通过 headers 参数覆盖。自动创建父目录。
    网络错误/非 2xx 状态由 requests 抛出 raise_for_status，不吞错。
    """
    dir_path = os.path.dirname(filepath)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)

    final_headers = headers if headers is not None else _DEFAULT_HEADERS
    resp = requests.get(url, headers=final_headers, timeout=20)
    resp.raise_for_status()
    with open(filepath, "wb") as f:
        f.write(resp.content)


def parse_pdf_text(pdf_path: str, max_pages: Optional[int] = None) -> str:
    """用 PyMuPDF 打开 PDF，逐页提取文本并合并返回。

    从 parse_factsheet._process_single_bentham_pdf / _process_single_metrics_pdf
    提取通用部分（打开、逐页 get_text、合并），**去除所有基金特定逻辑**
    （日期正则、收益提取、表块坐标过滤等）。max_pages 限制读取页数；为 None
    时读全部页。返回原始合并文本（不在此处做 clean_spacing，调用方可按需调用）。
    """
    if fitz is None:  # pragma: no cover - 环境缺 PyMuPDF 时
        raise ImportError(
            "PyMuPDF (fitz) is not installed; run `pip3 install PyMuPDF`."
        )

    text = ""
    with fitz.open(pdf_path) as doc:
        pages_to_read = (
            min(max_pages, len(doc)) if max_pages is not None else len(doc)
        )
        for i in range(pages_to_read):
            text += doc[i].get_text()
    return text


# ---------------------------------------------------------------------------
# PDF 提取：Commentary 当月收益 + performance 表滚动收益
# 数据完整性：Commentary 正文优先（复利验证已证明 performance 表 1mo 口径
# 错误）；负号强制捕获；按列标题对应值不靠位置；不捏造不插值。
# ---------------------------------------------------------------------------


def _pct_to_decimal(num_str: str) -> float:
    """把百分比数值串（无 % 号，如 '5.89'/'-0.26'）除以 100 转为小数 float。

    用 Decimal 精确十进制除法（移位）再转 float，避免 float()/100.0 的二进制
    浮点表示误差（如 5.89/100.0 = 0.058899999... != 字面量 0.0589）。这是对
    原始提取值的最忠实十进制映射，非"合理性纠正"：输入 "5.89%" 在十进制下必然
    对应 0.0589，Decimal 移位无损还原，而 float/100.0 引入额外二进制舍入。
    """
    return float(Decimal(num_str) / Decimal(100))


def extract_commentary_return(text: str) -> Optional[float]:
    """从 PDF 文本提取 Commentary 当月收益（after fees，返回小数）。

    正则 r'returned\\s+([+-]?\\d+\\.\\d+)%'，捕获符号。正数省略正号正常
    （"0.53%"），负号必须捕获（"-0.26%"）。取第一个匹配（当月声明，非后续
    滚动收益数字）。无匹配返回 None（绝不猜测）。

    Commentary 正文优先于 performance 表 1mo：复利交叉验证已证明 performance
    表 1mo 口径错误（列错位/合并），Commentary 正文值才是当月真实收益。
    """
    if not text:
        return None
    m = re.search(r"returned\s+([+-]?\d+\.\d+)%", text)
    if not m:
        return None
    return _pct_to_decimal(m.group(1))


# performance 表列标题 -> 结果 key 映射（按 Stake 月报固定顺序）
_PERF_COL_KEYS = [
    ("1 month", "1mo"),
    ("3 months", "3mo"),
    ("6 months", "6mo"),
    ("12 months", "12mo"),
    ("since inception", "inception"),
]


def extract_perf_rolling(text: str) -> dict:
    """提取 performance 表 Class A 滚动收益。

    按列标题对应值（不靠位置，解决"5 列 4 值"错位根因）；显式处理 '-' 空列
    -> None。返回 {'1mo':..,'3mo':..,'6mo':..,'12mo':..,'inception':..,
    'parse_error':bool}。

    parse_error=True 表示表结构异常（如 Nov 2025 的 12mo=inception 合并致
    值数!=列数），此时仍按顺序部分填充已知值。parse_error 不致命：Commentary
    正文才是当月收益来源，performance 表仅用于复利交叉验证。
    """
    result = {
        "1mo": None, "3mo": None, "6mo": None,
        "12mo": None, "inception": None, "parse_error": False,
    }
    if not text:
        result["parse_error"] = True
        return result

    # 按行处理，每行压缩空白（保留行结构以分离列标题行与数据行）
    lines = [clean_spacing(ln).strip() for ln in text.split("\n")]

    # 找列标题行（同时含 "1 month" 与 "since inception"）
    header_idx = -1
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "1 month" in low and "since inception" in low:
            header_idx = i
            break
    if header_idx < 0:
        result["parse_error"] = True
        return result

    # 解析列标题在行中的出现顺序
    header_low = lines[header_idx].lower()
    col_specs = []
    for label, key in _PERF_COL_KEYS:
        pos = header_low.find(label)
        if pos >= 0:
            col_specs.append((pos, key))
    col_specs.sort()
    keys_in_order = [s[1] for s in col_specs]
    if not keys_in_order:
        result["parse_error"] = True
        return result

    # 找 Class A 数据行（标题行及之后首个含 "class a" 的行：PDF 文本提取
    # 常把表头与数据行合并到同一行，故从 header_idx 起搜索）
    data_line = None
    for ln in lines[header_idx:]:
        if re.search(r"class\s*a", ln, re.IGNORECASE):
            data_line = ln
            break
    if not data_line:
        result["parse_error"] = True
        return result

    # 从 "Class A" 标记之后提取值 token：[+-]?\d+\.\d+% 或 "-"。只取标记
    # 之后的部分，避免误捕获行首 Commentary 的 "returned X%" 或表头百分比。
    ca_match = re.search(r"class\s*a", data_line, re.IGNORECASE)
    data_after = data_line[ca_match.end():] if ca_match else data_line
    tokens = re.findall(r"[+-]?\d+\.\d+%|-", data_after)

    if len(tokens) != len(keys_in_order):
        result["parse_error"] = True

    # 按顺序对应（多余 token 忽略，不足留 None）
    for i, key in enumerate(keys_in_order):
        if i < len(tokens):
            tok = tokens[i]
            result[key] = None if tok == "-" else _pct_to_decimal(tok.rstrip("%"))
    return result


def extract_pdf_one(
    pdf_path: str, max_pages: Optional[int] = None
) -> tuple[Optional[float], dict]:
    """单 PDF 提取纯函数（顶层，可被 ThreadPool/ProcessPool 调用）。

    parse_pdf_text -> extract_commentary_return + extract_perf_rolling。
    返回 (commentary_return, rolling)。失败返回 (None, {'parse_error':True})。

    顶层纯函数设计：未来可一行切 ProcessPoolExecutor 应对大批量（100+ PDF）。
    """
    try:
        text = parse_pdf_text(pdf_path, max_pages=max_pages)
    except Exception:
        return (None, {"1mo": None, "3mo": None, "6mo": None,
                       "12mo": None, "inception": None, "parse_error": True})
    return (extract_commentary_return(text), extract_perf_rolling(text))


def _text_to_ym(text: str) -> Optional[str]:
    """从链接文本（如 'March 2025'/'Aug 2025'）提 YYYY-MM。

    用 parse_date_string 解析日期文本（支持空格/短横/下划线分隔），取
    YYYY-MM 前 7 位。无法解析返回 None。
    """
    date_str = parse_date_string(text)
    return date_str[:7] if date_str else None


def extract_pdf_links_from_archive(markdown: str) -> list[tuple[str, str]]:
    """从归档页 markdown 提取 [(YYYY-MM, pdf_url), ...]。

    优先从 markdown 链接文本 [text](url.pdf) 提取月份（链接文本通常是
    "March 2025" 等规范月份名+年份，比 URL 文件名可靠--文件名命名常不统一
    如 April25/March2025 混用，且 URL hash 如 blt93dd2d 可能被误匹配为日期）。
    链接文本无月份时回退到 URL 用 extract_month_prefix。裸 url.pdf 从 URL 提取。
    去重保持顺序。无法识别月份的跳过（不猜测）。
    """
    if not markdown:
        return []
    results: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # 1. markdown 链接 [text](url.pdf)：优先从 text 提月份，回退 url
    link_pattern = re.compile(
        r"\[([^\]]+)\]\((https?://[^\s\)]+\.pdf[^\s\)]*)\)", re.IGNORECASE
    )
    for m in link_pattern.finditer(markdown):
        text, url = m.group(1), m.group(2)
        ym = _text_to_ym(text) or extract_month_prefix(url)
        if ym is None:
            continue
        key = (ym, url)
        if key not in seen:
            seen.add(key)
            results.append(key)

    # 2. 裸 url.pdf（无链接文本）：从 url 提月份
    linked_urls = {url for _, url in results}
    for url in re.findall(r"https?://[^\s\)]+\.pdf[^\s\)]*", markdown, re.IGNORECASE):
        if url in linked_urls:
            continue
        ym = extract_month_prefix(url)
        if ym is None:
            continue
        key = (ym, url)
        if key not in seen:
            seen.add(key)
            results.append(key)
    return results


# ---------------------------------------------------------------------------
# 并发下载+提取 pipeline（ThreadPool，M5 满核）
# 线程安全：fitz C 层释放 GIL，每 worker 独立 fitz.open。不用 ProcessPool
# （macOS spawn 重新 import fitz 开销 > 15 个小 PDF 收益）。
# ---------------------------------------------------------------------------


def download_and_extract_parallel(
    links: list[tuple[str, str]],
    dest_dir: str,
    max_workers: Optional[int] = None,
) -> list[tuple[str, Optional[float], dict]]:
    """ThreadPool pipeline：每 worker 下载一个 PDF 后立即提取。

    IO 下载与 CPU 提取重叠，无 barrier（比"下载并发->提取并发"两阶段更快）。
    max_workers 默认 min(16, os.cpu_count())（M5 满核 10-16）。
    返回 [(ym, commentary_return, rolling), ...]，按 ym 升序排序。
    失败隔离：单 PDF 下载/提取失败 -> (ym, None, {'parse_error':True})，不中断其他。
    复用 download_file + extract_pdf_one。
    """
    if max_workers is None:
        max_workers = min(16, os.cpu_count() or 8)

    def _failed_rolling() -> dict:
        return {"1mo": None, "3mo": None, "6mo": None,
                "12mo": None, "inception": None, "parse_error": True}

    def _worker(ym: str, url: str) -> tuple[Optional[float], dict]:
        filepath = os.path.join(dest_dir, f"{ym}.pdf")
        download_file(url, filepath)
        return extract_pdf_one(filepath)

    results: list[tuple[str, Optional[float], dict]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_ym = {ex.submit(_worker, ym, url): ym for ym, url in links}
        for fut in concurrent.futures.as_completed(future_to_ym):
            ym = future_to_ym[fut]
            try:
                commentary, rolling = fut.result()
            except Exception:
                commentary, rolling = None, _failed_rolling()
            results.append((ym, commentary, rolling))
    results.sort(key=lambda r: r[0])
    return results


def verify_monthly_vs_rolling(
    monthly: list[tuple[str, float]],
    rolling: dict,
) -> dict:
    """复利交叉验证：用 monthly 复利算最近 N 月累计，对比 rolling 同期值。

    阈值：绝对误差 < 0.5%（容忍 PDF 四舍五入）。rolling 缺列或 monthly 不足
    N 月时跳过该窗口。至少一个窗口通过 -> pass=True（rolling 数据可用）。

    Args:
        monthly: [(date_str, net_return), ...]，可乱序，内部排序。
        rolling: extract_perf_rolling 的返回 dict。

    Returns:
        {'3mo':{'expected','actual','error','pass'}, '6mo':.., '12mo':.., 'pass':bool}
    """
    result = {"3mo": None, "6mo": None, "12mo": None, "pass": False}
    if not monthly or not rolling:
        return result

    sorted_m = sorted(monthly, key=lambda x: x[0])
    returns = [r for _, r in sorted_m]

    any_pass = False
    for key, n in [("3mo", 3), ("6mo", 6), ("12mo", 12)]:
        rolling_val = rolling.get(key)
        if rolling_val is None or len(returns) < n:
            result[key] = {"expected": None, "actual": None,
                           "error": None, "pass": False}
            continue
        actual = 1.0
        for r in returns[-n:]:
            actual *= (1.0 + r)
        actual = actual - 1.0
        error = abs(actual - rolling_val)
        win_pass = error < 0.005  # 0.5%
        result[key] = {"expected": rolling_val, "actual": actual,
                       "error": error, "pass": win_pass}
        if win_pass:
            any_pass = True
    result["pass"] = any_pass
    return result


def gate_check(
    records: list[tuple[str, float]],
    rolling_per_month: dict,
) -> tuple[bool, list[str]]:
    """入库前硬 gate（数据完整性兜底）。

    组合校验：
    1. check_gaps（缺口零容忍）
    2. ANTI-FABRICATION（连续 >= 3 个相同非零浮点数，参考教训 213bdd）
    3. verify_monthly_vs_rolling（用最近月份 rolling，至少一个窗口通过；
       rolling parse_error=True 时跳过不因此 fail）
    4. 字段类型校验（|net_return| < 0.5 即 50%，超出视为字段类型错误）

    Returns:
        (pass, errors)。pass=False 时 errors 列出具体问题，调用方必须停止入库。
    """
    errors: list[str] = []
    if not records:
        return (False, ["无数据"])

    # 1. 缺口检查（缺口零容忍）
    dates = [d for d, _ in records]
    gaps = check_gaps(dates)
    if gaps:
        errors.append(f"缺口: {gaps}")

    # 2. ANTI-FABRICATION：连续 >= 3 个相同非零值（捏造迹象）
    returns = [r for _, r in records]
    for i in range(len(returns) - 2):
        if returns[i] != 0.0 and returns[i] == returns[i + 1] == returns[i + 2]:
            errors.append(
                f"ANTI-FABRICATION: 连续3月相同值 {returns[i]} 起于第 {i} 月"
            )
            break

    # 3. 字段类型校验：|r| < 0.5（月度收益 50% 上限）
    for d, r in records:
        if abs(r) >= 0.5:
            errors.append(f"字段异常: {d} 收益 {r} 超出月度合理范围 |r|<0.5")

    # 4. 复利验证：用最近月份的 rolling
    if rolling_per_month:
        latest_ym = max(d[:7] for d, _ in records)
        latest_rolling = rolling_per_month.get(latest_ym, {})
        if latest_rolling and not latest_rolling.get("parse_error", True):
            verify = verify_monthly_vs_rolling(records, latest_rolling)
            if not verify["pass"]:
                errors.append(f"复利验证失败（{latest_ym}）: {verify}")

    return (len(errors) == 0, errors)
