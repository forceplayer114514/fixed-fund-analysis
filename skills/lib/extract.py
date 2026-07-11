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

import datetime
import os
import re
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
