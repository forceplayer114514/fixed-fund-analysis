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
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional

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
        if 1 <= month <= 12:
            return f"{year}-{month:02d}"

    # 2. YYYYMM（前后需非数字边界）
    date_match_short = re.search(r"(\b|[^0-9])(\d{4})(\d{2})(\b|[^0-9])", filename)
    if date_match_short:
        year = int(date_match_short.group(2))
        month = int(date_match_short.group(3))
        if 1 <= month <= 12:
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

    # 4. YYYY-MM(带连字符,如 perf-2023-01.pdf / 2023-01-report.pdf)
    m = re.search(r"(\d{4})-(\d{2})", filename)
    if m:
        year = int(m.group(1))
        month = int(m.group(2))
        if 1 <= month <= 12:
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
    瞬时网络错误（超时/连接失败）重试 1 次，避免并发批次里单个慢请求
    的长尾拖累整批 as_completed 收尾；HTTP 错误状态（4xx/5xx）不重试，
    立即抛出。
    """
    dir_path = os.path.dirname(filepath)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)

    final_headers = headers if headers is not None else _DEFAULT_HEADERS
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=final_headers, timeout=20)
            break
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt == 1:
                raise
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


@dataclass
class ExtractedReturn:
    """单月收益提取结果(含原文片段,供 pending_review 审计)。

    value: 月度收益小数(如 0.0053)。
    source_quote: 匹配到的原文片段(如 'returned 0.53%'),进 pending_review 时
        附在记录里供人工裁决(§5 闸门:代码提取也带原文片段,非仅 LLM 兜底)。
    ambiguous: 仅 generic 提取器(extract_commentary_return_full)置 True --
        匹配窗口内出现 benchmark/index/outperform 等词时,正则可能命中 benchmark
        收益而非基金自身收益(认错对象,非没认出)。专属提取器人写时人眼看过样本,
        永远 False。ambiguous=True 的月强制进 pending_review(review_reason=
        ambiguous_subject),不直通 monthly_returns(A4 反 benchmark 守卫)。
    """
    value: float
    source_quote: str
    ambiguous: bool = False


# 反 benchmark 守卫:generic 提取匹配点前后窗口含这些词 -> ambiguous=True。
# 基金 Commentary 常写"returned 0.72%, outperforming the benchmark which
# returned 0.35%",generic 的 returned\s+X% 可能命中 benchmark 的 0.35%。
# 宁可误伤进 pending(人工审),不漏(A4c)。
_BENCHMARK_GUARD_RE = re.compile(
    r"benchmark|outperform|underperform|\bindex\b|relative\s+to|versus|\bvs\.?\b",
    re.IGNORECASE,
)


def extract_commentary_return_full(text: str) -> Optional[ExtractedReturn]:
    """从 PDF 文本提取 Commentary 当月收益 + 原文片段,返回 ExtractedReturn。

    正则 r'returned\\s+([+-]?\\d+\\.\\d+)%'，捕获符号。取第一个匹配（当月声明，
    非后续滚动收益数字）。source_quote = m.group(0)（含 'returned X%'）。

    反 benchmark 守卫(A4c):匹配点前后各 200 字符窗口内出现 benchmark/index/
    outperform/underperform/relative to/versus/vs 等词时 ambiguous=True --
    该月强制进 pending_review(ambiguous_subject),不直通。无匹配返回 None。
    """
    if not text:
        return None
    m = re.search(r"returned\s+([+-]?\d+\.\d+)%", text)
    if not m:
        return None
    lo = max(0, m.start() - 200)
    hi = min(len(text), m.end() + 200)
    ambiguous = bool(_BENCHMARK_GUARD_RE.search(text[lo:hi]))
    return ExtractedReturn(
        value=_pct_to_decimal(m.group(1)),
        source_quote=m.group(0),
        ambiguous=ambiguous,
    )


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


# ---------------------------------------------------------------------------
# Bentham Global Income Fund 提取（多 PDF 合成逐月 total return）
# 数据完整性：Commentary "had a total return (after fees) of X%" 提 net（非
# gross）；rolling 从 "Total return (after fees)" 表行提 1mo/3mo/6mo/12mo
# （累计值，与月度复利可比）；不提 inception（p.a. 与月度复利不可比，会误触发
# consistency_check _check_compound inception 块）。
# 2026-07 回测：extract_commentary_return 的 returned\s+X% 对 Bentham 取到
# gross（before fees），字段口径错误，须用本专属提取器。
# ---------------------------------------------------------------------------

_BENTHAM_NET_RE = re.compile(
    r"had a total return\s*\(after fees\*?\)\s*of\s*([+-]?\d+\.\d+)\s*(?:%|percent)",
    re.IGNORECASE,
)


def extract_bentham_net_return_full(text: str) -> Optional[ExtractedReturn]:
    """Bentham Commentary net total return (after fees) + 原文片段,返回 ExtractedReturn。

    匹配 "had a total return (after fees[*]) of X[%|percent]"，捕获 net 值。
    区别于 extract_commentary_return（returned\\s+X% 对 Bentham 取 gross
    before-fees，字段口径错误）。source_quote = m.group(0)。无匹配返回 None。
    """
    if not text:
        return None
    m = _BENTHAM_NET_RE.search(text)
    if not m:
        return None
    return ExtractedReturn(value=_pct_to_decimal(m.group(1)), source_quote=m.group(0))


def extract_bentham_net_return(text: str) -> Optional[float]:
    """Bentham Commentary net total return (after fees)，返回小数。

    匹配 "had a total return (after fees[*]) of X[%|percent]"，捕获 net 值。
    区别于 extract_commentary_return（returned\\s+X% 对 Bentham 取 gross
    before-fees，字段口径错误）。支持 2017 旧版 "percent"+"after fees*" 与
    2022+ "%"+"after fees"。无匹配返回 None（不猜测）。
    """
    if not text:
        return None
    m = _BENTHAM_NET_RE.search(text)
    if not m:
        return None
    return _pct_to_decimal(m.group(1))


def extract_bentham_rolling(text: str) -> dict:
    """Bentham performance 表 "Total return (after fees)" 行滚动收益。

    提 1mo/3mo/6mo/12mo（累计值，与月度复利可比）。不提 inception（p.a.，
    与月度复利不可比）。定位 "Total return (after fees)" 行标签 -> 取至下一行
    标签（Benchmark/Active return）间数值 token。token 数 < 4 -> parse_error。
    旧版 PDF（2017 等无此表）-> parse_error（gate 跳过复利，不致命）。
    """
    result = {"1mo": None, "3mo": None, "6mo": None,
              "12mo": None, "inception": None, "parse_error": False}
    if not text:
        result["parse_error"] = True
        return result
    m = re.search(r"Total return\s*\(after fees\)", text)
    if not m:
        result["parse_error"] = True
        return result
    after = text[m.end():]
    end = len(after)
    for label in ("Benchmark", "Active return", "Risk Characteristics"):
        idx = after.find(label)
        if 0 < idx < end:
            end = idx
    tokens = re.findall(r"[+-]?\d+\.\d+", after[:end])
    if len(tokens) < 4:
        result["parse_error"] = True
        return result
    result["1mo"] = _pct_to_decimal(tokens[0])
    result["3mo"] = _pct_to_decimal(tokens[1])
    result["6mo"] = _pct_to_decimal(tokens[2])
    result["12mo"] = _pct_to_decimal(tokens[3])
    return result


def extract_pdf_one_bentham_full(
    pdf_path: str, max_pages: Optional[int] = None
) -> tuple[Optional[ExtractedReturn], dict]:
    """Bentham 单 PDF 提取(带 quote):parse_pdf_text -> net ExtractedReturn + rolling。

    同 extract_pdf_one_full 接口,但用 Bentham 专属提取器(net 非 gross)。
    """
    try:
        text = parse_pdf_text(pdf_path, max_pages=max_pages)
    except Exception:
        return (None, {"1mo": None, "3mo": None, "6mo": None,
                       "12mo": None, "inception": None, "parse_error": True})
    return (extract_bentham_net_return_full(text), extract_bentham_rolling(text))


def extract_pdf_one_bentham(
    pdf_path: str, max_pages: Optional[int] = None
) -> tuple[Optional[float], dict]:
    """Bentham 单 PDF 提取：parse_pdf_text -> net return + rolling。

    同 extract_pdf_one 接口，但用 Bentham 专属提取器（net 非 gross）。
    """
    try:
        text = parse_pdf_text(pdf_path, max_pages=max_pages)
    except Exception:
        return (None, {"1mo": None, "3mo": None, "6mo": None,
                       "12mo": None, "inception": None, "parse_error": True})
    return (extract_bentham_net_return(text), extract_bentham_rolling(text))


def extract_kkc_net_return_full(text: str) -> Optional[ExtractedReturn]:
    """KKR Credit Income Fund 专属提取器。

    优先从 Total Return (Net) 或 Net Return Based on NTA 表格行提取当月收益。
    无匹配时回退到 returned X% 模式。
    """
    if not text:
        return None
    t = clean_spacing(text)
    m = re.search(r"Total Returns? \(Net\)\s*([+-]?\d+\.\d+)%", t)
    if m:
        return ExtractedReturn(value=_pct_to_decimal(m.group(1)), source_quote=m.group(0), ambiguous=False)
    m = re.search(r"Net Return Based on NTA\s*\(?%?\)?\s*([+-]?\d+\.\d+)%", t)
    if m:
        return ExtractedReturn(value=_pct_to_decimal(m.group(1)), source_quote=m.group(0), ambiguous=False)
    m = re.search(r"returned\s+([+-]?\d+\.\d+)%", text)
    if m:
        return ExtractedReturn(value=_pct_to_decimal(m.group(1)), source_quote=m.group(0), ambiguous=False)
    return None


def extract_pdf_one_kkc_full(
    pdf_path: str, max_pages: Optional[int] = None
) -> tuple[Optional[ExtractedReturn], dict]:
    """KKC 单 PDF 提取(带 quote):parse_pdf_text -> kkc ExtractedReturn + rolling。"""
    try:
        text = parse_pdf_text(pdf_path, max_pages=max_pages)
    except Exception:
        return (None, {"1mo": None, "3mo": None, "6mo": None,
                       "12mo": None, "inception": None, "parse_error": True})
    return (extract_kkc_net_return_full(text), extract_perf_rolling(text))


def extract_gci_net_return_full(text: str) -> Optional[ExtractedReturn]:
    """GCI 专属 PDF 提取器：提取 NTA Net Return (%) 下的 1 Mth 净回报。"""
    if not text:
        return None
    lines = [clean_spacing(line).strip() for line in text.split("\n") if line.strip()]
    try:
        idx = [i for i, l in enumerate(lines) if "NTA Net Return" in l][0]
        val_str = lines[idx + 1]
        val = _pct_to_decimal(val_str)
        return ExtractedReturn(
            value=val,
            source_quote=f"NTA Net Return (%): {val_str}%",
            ambiguous=False,
        )
    except Exception:
        return None


def extract_gci_rolling(text: str) -> dict:
    """GCI 专属滚动收益提取器。"""
    result = {
        "1mo": None, "3mo": None, "6mo": None,
        "12mo": None, "inception": None, "parse_error": False,
    }
    if not text:
        result["parse_error"] = True
        return result
    lines = [clean_spacing(line).strip() for line in text.split("\n") if line.strip()]
    try:
        idx = [i for i, l in enumerate(lines) if "NTA Net Return" in l][0]
        vals = []
        for l in lines[idx + 1:]:
            if "Distribution" in l or "Target" in l or not re.match(r"^[+-]?\d+\.\d+%?$", l):
                break
            vals.append(float(l.replace("%", "")) / 100.0)
        if len(vals) < 7:
            result["parse_error"] = True
            return result
        result["1mo"] = vals[0]
        result["3mo"] = vals[1]
        result["6mo"] = vals[2]
        result["12mo"] = vals[3]
        result["inception"] = vals[6]
    except Exception:
        result["parse_error"] = True
    return result


def extract_pdf_one_gci_full(
    pdf_path: str, max_pages: Optional[int] = None
) -> tuple[Optional[ExtractedReturn], dict]:
    """GCI 单 PDF 提取(带 quote)。"""
    try:
        text = parse_pdf_text(pdf_path, max_pages=max_pages)
    except Exception:
        return (None, {"1mo": None, "3mo": None, "6mo": None,
                       "12mo": None, "inception": None, "parse_error": True})
    return (extract_gci_net_return_full(text), extract_gci_rolling(text))


def extract_pdf_one_full(
    pdf_path: str, max_pages: Optional[int] = None
) -> tuple[Optional[ExtractedReturn], dict]:
    """单 PDF 提取(带 quote):parse_pdf_text -> ExtractedReturn + rolling。

    顶层纯函数(可被 ThreadPool/ProcessPool 调用)。失败返回
    (None, {'parse_error':True})。需要 source_quote 的调用方(ingest 超 |r|<0.5
    进 pending_review)用本函数。
    """
    try:
        text = parse_pdf_text(pdf_path, max_pages=max_pages)
    except Exception:
        return (None, {"1mo": None, "3mo": None, "6mo": None,
                       "12mo": None, "inception": None, "parse_error": True})
    return (extract_commentary_return_full(text), extract_perf_rolling(text))


# ---------------------------------------------------------------------------
# 提取器注册表(A1):--extractor choices 从此动态取,新基金默认 generic。
# generic = extract_commentary_return_full("returned X%"),带 A4c 反 benchmark
# 守卫(ambiguous)。专属提取器(bentham 等)人写时人眼看过样本,ambiguous=False。
# 新基金先 generic,gate 失败/EXTRACTOR_MISMATCH -> 走 add_fixed_fund.md 4.5
# 固定流程加专属提取器(禁 REPL 手写入库)。
# ---------------------------------------------------------------------------
EXTRACTORS = {
    "generic": extract_pdf_one_full,
    "stake": extract_pdf_one_full,
    "bentham": extract_pdf_one_bentham_full,
    "kkc": extract_pdf_one_kkc_full,
    "gci": extract_pdf_one_gci_full,
}


def get_extractor(name: Optional[str]) -> Callable[[str], tuple[Optional[ExtractedReturn], dict]]:
    """按名取 _full 提取器(返 ExtractedReturn+rolling)。None/未知 -> generic。"""
    return EXTRACTORS.get(name or "generic", EXTRACTORS["generic"])


def extractor_names() -> list[str]:
    """--extractor choices 动态源。"""
    return list(EXTRACTORS.keys())


def is_generic_extractor(name: Optional[str]) -> bool:
    """generic/stake 走 A4 准入验证(首批进 pending + 反 benchmark 守卫);
    专属(bentham 等)人写过样本,直通。"""
    return (name or "generic") in ("generic", "stake")


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


@dataclass
class ArchiveLinks:
    """归档页解析结果:成功解析月份的链接 + 解析失败日志(不静默丢弃)。

    parsed: [(YYYY-MM, pdf_url), ...] 去重保序,供 download_and_extract_parallel。
    unparseable: [{"url","raw_text","reason"}] 解析不出月份的 PDF 链接,写入
        DiscoveryReport(M3 ★重点2),不计 obtained,不静默消失。
    月数 = len({ym for ym,_ in parsed})(去重集合大小,非链接数,修正3.2.6)。
    """
    parsed: list[tuple[str, str]]
    unparseable: list[dict]


def extract_archive_links(markdown: str) -> ArchiveLinks:
    """从归档页 markdown 提取 PDF 月报链接 + 解析失败日志(不静默丢弃)。

    成功解析月份的 -> parsed(去重保序);解析不出月份的 PDF 链接 -> unparseable
    (含 url/raw_text/reason),写入 DiscoveryReport 交人工判断,绝不静默消失
    (M3 ★重点2 / 修正3.2.6:月解析错一个 gap 错一个)。

    优先从 markdown 链接文本 [text](url.pdf) 提月份(比 URL 文件名可靠);
    链接文本无月份时回退 URL 用 extract_month_prefix;裸 url.pdf 从 URL 提。
    """
    if not markdown:
        return ArchiveLinks(parsed=[], unparseable=[])
    parsed: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    unparseable: list[dict] = []

    # 0. HTML <a href="url.pdf">text</a>(归档页 curl 抓的 HTML;计划禁 reader-mode
    #    提链接,代码抓原始 HTML 提链接,3.1 工具隔离)
    html_pattern = re.compile(
        r'<a[^>]+href=["\'](https?://[^"\'\s]+\.pdf[^"\'\s]*)["\'][^>]*>([^<]+)</a>',
        re.IGNORECASE,
    )
    for m in html_pattern.finditer(markdown):
        url, text = m.group(1), m.group(2)
        ym = _text_to_ym(text) or extract_month_prefix(url)
        if ym is None:
            unparseable.append({"url": url, "raw_text": text,
                                "reason": "month_not_parsed"})
            continue
        key = (ym, url)
        if key not in seen:
            seen.add(key)
            parsed.append(key)

    # 1. markdown 链接 [text](url.pdf):优先从 text 提月份,回退 url
    link_pattern = re.compile(
        r"\[([^\]]+)\]\((https?://[^\s\)]+\.pdf[^\s\)]*)\)", re.IGNORECASE
    )
    for m in link_pattern.finditer(markdown):
        text, url = m.group(1), m.group(2)
        ym = _text_to_ym(text) or extract_month_prefix(url)
        if ym is None:
            unparseable.append({"url": url, "raw_text": text,
                                "reason": "month_not_parsed"})
            continue
        key = (ym, url)
        if key not in seen:
            seen.add(key)
            parsed.append(key)

    # 2. 裸 url.pdf(无链接文本):从 url 提月份。排除已处理(parsed ∪ unparseable)
    # 的 url,避免同一 url 被 markdown 链接与裸 url 正则双重收集进 unparseable。
    linked_urls = {url for _, url in parsed} | {u["url"] for u in unparseable}
    for url in re.findall(r"https?://[^\s\)\"'<>]+\.pdf[^\s\)\"'>]*", markdown, re.IGNORECASE):
        if url in linked_urls:
            continue
        ym = extract_month_prefix(url)
        if ym is None:
            unparseable.append({"url": url, "raw_text": url,
                                "reason": "month_not_parsed"})
            continue
        key = (ym, url)
        if key not in seen:
            seen.add(key)
            parsed.append(key)
    return ArchiveLinks(parsed=parsed, unparseable=unparseable)


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
    for url in re.findall(r"https?://[^\s\)\"'<>]+\.pdf[^\s\)\"'>]*", markdown, re.IGNORECASE):
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
# 并发下载+提取 pipeline（ThreadPool）
# 线程安全：fitz C 层释放 GIL，每 worker 独立 fitz.open。不用 ProcessPool
# （macOS spawn 重新 import fitz 开销 > 15 个小 PDF 收益）。
# 下载是 IO 等待、提取是短促 CPU 突发，worker 数按 IO 并发（cpu_count*2）
# 定，不卡在物理核数——等网络回包的线程不占核，可以比核数开得更多。
# ---------------------------------------------------------------------------


def download_and_extract_parallel(
    links: list[tuple[str, str]],
    dest_dir: str,
    max_workers: Optional[int] = None,
    extractor: Optional[Callable] = None,
    return_full: bool = False,
) -> list:
    """ThreadPool pipeline：每 worker 下载一个 PDF 后立即提取。

    IO 下载与 CPU 提取重叠，无 barrier（比"下载并发->提取并发"两阶段更快）。
    max_workers 默认 min(24, os.cpu_count()*2)：下载阶段是 IO 等待不占核，
    按 IO 并发定而非卡在物理核数（10 核机器上默认 20，而非 10）。
    extractor 默认 extract_pdf_one（Stake 口径,返 float）；Bentham 等基金专属口径
    传 extract_pdf_one_bentham。
    return_full=True:extractor 默认 extract_pdf_one_full(返 ExtractedReturn +
    rolling),供 ingest_discovery 拿 source_quote/ambiguous(A4)。调用方据此取
    .value 或直接用 ExtractedReturn。
    返回 [(ym, commentary, rolling), ...]，按 ym 升序排序。commentary 类型=
    float(return_full=False)或 ExtractedReturn(return_full=True)。失败 ->
    (ym, None, {'parse_error':True})，不中断其他。复用 download_file。
    """
    if max_workers is None:
        max_workers = min(24, (os.cpu_count() or 8) * 2)
    if extractor is None:
        extractor = extract_pdf_one_full if return_full else extract_pdf_one

    def _failed_rolling() -> dict:
        return {"1mo": None, "3mo": None, "6mo": None,
                "12mo": None, "inception": None, "parse_error": True}

    def _worker(ym: str, url: str) -> tuple[Optional[float], dict]:
        filepath = os.path.join(dest_dir, f"{ym}.pdf")
        download_file(url, filepath)
        return extractor(filepath)

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


# ---------------------------------------------------------------------------
# HTML 表格源解析（fundmonitors Full Fund Profile 等）
# 聚合站逐月收益表（Year×Month grid + YTD 列），无 PDF 归档基金的数据源。
# 数据完整性：N/R=未报告跳过（pre-inception/future，非缺口）；提取层纯文本
# ->数字映射（_pct_to_decimal Decimal 移位），无 backfill/forward-fill。
# ---------------------------------------------------------------------------


def parse_html_monthly_table(
    markdown: str,
) -> tuple[list[tuple[str, float]], dict[str, float]]:
    """从 fundmonitors Full Fund Profile markdown 解析 Historical Performance 逐月表。

    定位 "Historical Performance" 区块（calendar year，Jan-Dec），**排除**其后的
    "Historical Financial Year Performance"（FY Jul-Jun）表，避免误解析 FY 表。
    表格结构：| Year | Jan % | ... | Dec % | YTD % |，每行一个年份。

    N/R / N/A / "-" / 空 -> 跳过（pre-inception 或未来月，非缺口）。数值（含负号
    -0.19）经 _pct_to_decimal 转小数。返回 (records, ytd_map)：
      records: [(YYYY-MM-月末, net_return), ...] 升序
      ytd_map: {year_str: ytd_decimal} 供 gate_check_table 复利交叉验证

    无法定位表或无有效数据 -> ([], {})。绝不猜测。
    """
    if not markdown:
        return [], {}

    # 定位 "Historical Performance" 区块，排除 "Historical Financial Year Performance"
    start_marker = "Historical Performance"
    fy_marker = "Historical Financial Year Performance"
    start = markdown.find(start_marker)
    if start < 0:
        return [], {}
    fy_start = markdown.find(fy_marker, start)
    section = markdown[start : fy_start if fy_start > 0 else len(markdown)]

    records: list[tuple[str, float]] = []
    ytd_map: dict[str, float] = {}

    # 表头文字 -> 列索引映射：按表头文字定位列（非位置索引），容忍列序打乱
    # （如 YTD 移到 Jan 前）。规范化：strip ** / " %" / "%" / 空白，小写。
    _HEADER_TOKENS = {
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec", "ytd",
    }
    _MONTH_ORDER = [
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec",
    ]
    col_map: dict[str, int] = {}
    expected_cell_count = 0

    for line in section.split("\n"):
        if "|" not in line:
            continue
        # markdown 表格行：| cell | cell | ... | -> 去首尾管道后 split
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells:
            continue
        # 规范化所有 cell 用于表头识别（strip ** / % / 空白，小写）
        normed = [
            c.replace("**", "").replace(" %", "").replace("%", "").strip().lower()
            for c in cells
        ]
        # 表头行：首列 "year" 且识别出 >= 2 个月名/ytd -> 建 col_map（仅首个表头）
        if normed[0] == "year":
            hdr_map = {n: i for i, n in enumerate(normed) if n in _HEADER_TOKENS}
            if len(hdr_map) >= 2 and not col_map:
                col_map = hdr_map
                expected_cell_count = len(cells)
            continue  # 表头行本身非数据行
        # 数据行需 col_map 已构建（数据行先于表头出现 -> 跳过，不猜测）
        if not col_map:
            continue
        if expected_cell_count and len(cells) != expected_cell_count:
            continue
        year_str = cells[0].replace("**", "").strip()
        if not re.fullmatch(r"\d{4}", year_str):
            continue  # 跳过分隔行等
        year = int(year_str)
        # 按表头定位取月度值（非位置索引），容忍列序打乱与 % 后缀
        for m_name in _MONTH_ORDER:
            idx = col_map.get(m_name)
            if idx is None or idx >= len(cells):
                continue
            val = cells[idx].strip().replace("**", "").replace("%", "")
            if val in ("", "N/R", "N/A", "-"):
                continue
            # 仅接受 [+-]?\d+\.\d+ 格式（% 已剥离），其他跳过（不猜测）
            if not re.fullmatch(r"[+-]?\d+\.\d+", val):
                continue
            month = _MONTH_ORDER.index(m_name) + 1
            date = get_last_day_of_month(year, month).strftime("%Y-%m-%d")
            records.append((date, _pct_to_decimal(val)))
        # YTD 列按表头定位（非固定 cells[13]），剥离 %
        ytd_idx = col_map.get("ytd")
        if ytd_idx is not None and ytd_idx < len(cells):
            ytd_val = cells[ytd_idx].strip().replace("**", "").replace("%", "")
            if re.fullmatch(r"[+-]?\d+\.\d+", ytd_val):
                ytd_map[year_str] = _pct_to_decimal(ytd_val)

    records.sort(key=lambda x: x[0])
    return records, ytd_map


def gate_check_table(
    records: list[tuple[str, float]],
    ytd_map: dict[str, float],
) -> tuple[bool, list[str]]:
    """HTML 表格源的入库前硬 gate（gate_check 的表格版变体）。

    组合校验：
    1. check_gaps（缺口零容忍；N/R 仅出现在首尾外，不影响首尾间连续性）
    2. ANTI-FABRICATION（连续 >= 3 个相同非零浮点数，参考教训 213bdd）
    3. 字段类型校验（|net_return| < 0.5 即 50%）
    4. YTD 复利验证（替代 rolling 交叉验证）：对每年 >= 3 月 reported 的，
       compound 月度复利 vs ytd_map[year]，绝对误差 < 0.5%

    与 gate_check 的差异：表格源无 per-month rolling，但有独立 YTD 列可交叉
    验证。ytd_map 为空时跳过 YTD 验证（不因此 fail）。

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

    # 2. ANTI-FABRICATION：连续 >= 3 个相同非零值
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

    # 4. YTD 复利验证：按年分组，compound 该年所有 reported 月 vs YTD
    if ytd_map:
        by_year: dict[str, list[float]] = {}
        for d, r in records:
            by_year.setdefault(d[:4], []).append(r)
        for year, ytd_expected in ytd_map.items():
            month_rets = by_year.get(year, [])
            if len(month_rets) < 3:
                continue  # 太少不验证
            actual = 1.0
            for r in month_rets:
                actual *= (1.0 + r)
            actual = actual - 1.0
            error = abs(actual - ytd_expected)
            if error >= 0.005:  # 0.5%
                errors.append(
                    f"YTD 复利验证失败（{year}）: 月度复利 {actual:.4f} "
                    f"vs YTD {ytd_expected:.4f}，误差 {error:.4f}"
                )

    return (len(errors) == 0, errors)


# ---------------------------------------------------------------------------
# Plotly NAV 序列提取（pandoc HTML 报告嵌入的 Plotly hovertext）
# 数据完整性：按 trace name 字段过滤 fund class，benchmark trace（含
# Benchmark/Index/AusBond）自动丢弃，防 benchmark 误存为 share class。
# 零匹配/多匹配均 raise（防 pattern 打错或 agent 按 trace 顺序猜）。
# 返回 [(YYYY-MM-DD, nav), ...] 升序。
# ---------------------------------------------------------------------------


def _extract_data_array_content(html: str) -> Optional[str]:
    """提取 Plotly data 数组的括号内内容（不含外层方括号）。

    同时兼容 JS 形式 `var data = [...]` 与 JSON 形式 `"data":[...]`。用括号匹配
    （跳过字符串字面量内的 `]`）定位配对的 `]`，避免正则跨结构误匹配。找不到
    data 数组返回 None（调用方回退到全文顶层对象扫描）。
    """
    m = re.search(r'(?:var\s+data\s*=\s*\[|"data"\s*:\s*\[)', html)
    if not m:
        return None
    start = m.end()  # 紧跟在 '[' 之后
    depth = 1
    i = start
    in_str = False
    esc = False
    while i < len(html) and depth > 0:
        c = html[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
        i += 1
    if depth != 0:
        return None  # 括号不配平，结构损坏
    return html[start:i - 1]


def _split_top_level_objects(s: str) -> list[str]:
    """把字符串切成顶层 `{...}` 对象子串（括号匹配，跳过字符串字面量）。

    返回每个完整对象（含外层花括号）。用于从 data 数组内容里拆出各 trace 对象。
    """
    objs: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "{":
            depth = 1
            j = i + 1
            in_str = False
            esc = False
            while j < n and depth > 0:
                cc = s[j]
                if in_str:
                    if esc:
                        esc = False
                    elif cc == "\\":
                        esc = True
                    elif cc == '"':
                        in_str = False
                else:
                    if cc == '"':
                        in_str = True
                    elif cc == "{":
                        depth += 1
                    elif cc == "}":
                        depth -= 1
                j += 1
            if depth == 0:
                objs.append(s[i:j])
                i = j
                continue
            break  # 括号不配平，停止
        i += 1
    return objs


def parse_plotly_nav_series(
    html: str,
    fund_name_pattern: str,
) -> list[tuple[str, float]]:
    """从 pandoc HTML 报告 Plotly hovertext 提取基金类 NAV 序列。

    按 fund_name_pattern 在 trace 的 name 字段过滤；name 含
    Benchmark/Index/AusBond 的 trace 自动丢弃（结构上 benchmark 不可能混入）。
    多 trace 匹配 pattern -> raise（防 agent 按 trace 顺序猜）。零匹配 -> raise
    （防 pattern 打错时空列表被当"无数据"跳过）。返回 [(date, nav), ...] 升序。

    实现按 trace 对象作用域配对 (name, text)：先括号匹配提取 data 数组，再拆成
    各 trace 对象，在每个对象内分别取 name 与 text 数组。这样 layout 块里的
    annotation/axis/legend "name" 字段（无对应 text 数组）不会与 trace 的 text
    误配对，也无需脆弱的 count-mismatch 校验。
    """
    import re

    if not html or not fund_name_pattern:
        raise ValueError("parse_plotly_nav_series: html 与 fund_name_pattern 必填")

    # 优先从 data 数组提取 trace 对象（精确，排除 layout/config）；找不到 data
    # 数组时回退到全文顶层对象扫描（兼容无 data 包裹的极简 fixture）。
    data_content = _extract_data_array_content(html)
    trace_objs = _split_top_level_objects(
        data_content if data_content is not None else html
    )

    benchmark_markers = ("benchmark", "index", "ausbond")
    matched: list[list[tuple[str, float]]] = []
    for trace in trace_objs:
        nm = re.search(r'"name"\s*:\s*"([^"]+)"', trace)
        if not nm:
            continue
        name = nm.group(1)
        name_lower = name.lower()
        if any(m in name_lower for m in benchmark_markers):
            continue  # benchmark 自动丢弃
        if fund_name_pattern.lower() in name_lower:
            tm = re.search(r'"text"\s*:\s*\[([^\]]+)\]', trace, re.DOTALL)
            if not tm:
                continue  # 有 name 无 text 数组：非数据 trace，跳过
            text_arr = tm.group(1)
            points = re.findall(
                r'"([^"]*?)<br />(\d{4}-\d{2}-\d{2}):\s*\$([\d,.]+)"',
                text_arr,
            )
            series = [
                (date, float(navier.replace(",", "")))
                for _trace_name, date, navier in points
            ]
            matched.append(series)

    if len(matched) == 0:
        raise ValueError(
            f"parse_plotly_nav_series: 零匹配 pattern={fund_name_pattern!r}"
            "（benchmark 已排除）"
        )
    if len(matched) > 1:
        raise ValueError(
            f"parse_plotly_nav_series: 多 trace 匹配 pattern="
            f"{fund_name_pattern!r}，匹配数={len(matched)}"
        )

    series = sorted(matched[0], key=lambda x: x[0])
    return series
