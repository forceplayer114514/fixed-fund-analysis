"""prompt 构造 + JSON 解析 -> Extraction 数据类.

Spec E 单一路径:
  - LLM 只返字符串 token (无数值字段), parsers.parse_extraction 做数学+单位换算
  - Extraction.rolling 全走十进制 (与 net_return 同单位, 与 parsers.ParseResult 一致)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from . import pdf as pdf_mod
from .client import Client, DEFAULT_MAX_TOKENS
from . import parsers as parsers_mod
from . import issuer_rules

PROMPT_PATH = Path(__file__).parent / "prompts" / "extract_unified.md"


@dataclass(frozen=True)
class Extraction:
    ym: str
    net_return: Optional[float]                              # 十进制, 如 0.0065 (= 0.65%)
    source_quote: str
    measure: str                                             # kind (table_value/nav_pair/...) 兼容旧 "net_monthly"
    measure_label_in_pdf: str                                # 原文标签 (extract_unified 里叫 measure_label)
    rolling: Dict[str, Optional[float]] = field(default_factory=dict)  # 十进制 (0.0185 = 1.85%)
    not_found: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)        # 原始 LLM JSON
    parse_error: Optional[str] = None                        # parse 失败原因
    fund_name_text: Optional[str] = None                      # 文档抬头处基金全称原文, 逐字转写


class ExtractError(RuntimeError):
    pass


_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json_str(text: str) -> str:
    """text 里剥出 JSON. 优先剥去 ```json ... ``` 包裹, 兜底找第一个 {...}."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    m = _JSON_RE.search(t)
    if not m:
        raise ExtractError("no_json_object_in_text")
    return m.group(0)


def parse_response(text: str, expected_ym: str) -> Extraction:
    """把 LLM 返回的文本解析成 Extraction (单一路径 → parsers.parse_extraction)."""
    try:
        js = _extract_json_str(text)
        obj = json.loads(js)
    except (ExtractError, json.JSONDecodeError) as e:
        return Extraction(
            ym=expected_ym, net_return=None, source_quote="",
            measure="unknown", measure_label_in_pdf="",
            rolling={}, not_found=True,
            raw={"error": "parse_failed", "text": text[:2000]},
            parse_error=str(e),
        )

    ym = obj.get("ym") or expected_ym
    kind = obj.get("kind") or ""
    not_found = bool(obj.get("not_found")) or kind == "not_found"
    source_quote = str(obj.get("source_quote") or "")
    measure_label = str(obj.get("measure_label") or obj.get("measure_label_in_pdf") or "")
    fund_name_text = obj.get("fund_name_text") or None
    if fund_name_text is not None:
        fund_name_text = str(fund_name_text).strip() or None

    # not_found / parse_error (含月份闸 wrong_month) 分支下, Extraction.ym 必须
    # 用 expected_ym (调用方明确要抓的目标月), 不能用 LLM 自报的 ym -- 这两个
    # 分支恰恰是"LLM 没抓对/抓错月"的场景, 此时 ym 字段本身就是不可信的 (2026-07
    # Stake 2024-02 幻影缺口事故: LLM 某次抓错月报出 ym=2024-02, wrong_month
    # 判 parse_error 后 Extraction.ym 仍留着这个错值, 下游 write_extraction 拿
    # ex.ym 记 confirmed_gaps, 把根本不存在预期范围内的月份记成了"缺口")。
    if not_found:
        return Extraction(
            ym=expected_ym, net_return=None, source_quote=source_quote,
            measure=kind or "not_found",
            measure_label_in_pdf=measure_label,
            rolling={}, not_found=True, raw=obj,
            fund_name_text=fund_name_text,
        )

    pr = parsers_mod.parse_extraction(obj, expected_ym)
    if pr.parse_error:
        return Extraction(
            ym=expected_ym, net_return=None, source_quote=source_quote,
            measure=kind or "unknown",
            measure_label_in_pdf=measure_label,
            rolling={}, not_found=True, raw=obj,
            parse_error=pr.parse_error,
            fund_name_text=fund_name_text,
        )

    return Extraction(
        ym=ym,
        net_return=pr.net_return,
        source_quote=source_quote,
        measure=kind or "unknown",
        measure_label_in_pdf=measure_label,
        rolling=pr.rolling_decimal,  # 十进制单位
        not_found=False,
        raw=obj,
        fund_name_text=fund_name_text,
    )


def load_prompt() -> str:
    return PROMPT_PATH.read_text()


def extract_from_pdf(
    pdf_path: Path,
    expected_ym: str,
    *,
    client: Optional[Client] = None,
    max_pages: int = 2,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    retry_on_not_found_with_full: bool = True,
    fund_name: str = "",
    issuer: str = "",
) -> Extraction:
    """跑一次提取. not_found 时安全网: 加载全文重试一次.

    max_pages<=0 表示直接喂全文.
    fund_name/issuer: 按关键词匹配注入发行商专项规则 (issuer_rules.get_issuer_rule),
    不匹配则不附加任何专项规则 -- 不再让通用 prompt 对所有基金都携带全部发行商特例。
    """
    if client is None:
        client = Client()
    prompt = (
        load_prompt()
        + issuer_rules.get_issuer_rule(fund_name, issuer)
        + f"\n\n目标月份: {expected_ym}"
    )
    pdf_bytes = pdf_mod.clip_pages(pdf_path, max_pages=max_pages)
    resp = client.messages_with_pdf(prompt, pdf_bytes, max_tokens=max_tokens)
    ex = parse_response(resp.text, expected_ym)
    if (
        retry_on_not_found_with_full
        and ex.not_found
        and max_pages > 0
        and pdf_mod.page_count(pdf_path) > max_pages
    ):
        full_bytes = pdf_mod.clip_pages(pdf_path, max_pages=0)
        resp2 = client.messages_with_pdf(prompt, full_bytes, max_tokens=max_tokens)
        ex2 = parse_response(resp2.text, expected_ym)
        if not ex2.not_found:
            return ex2
    return ex


# ---------- Spec D: 通道 dispatch (unchanged) ----------

def _url_suffix(url_or_path: str) -> str:
    p = urlparse(url_or_path)
    return Path(p.path).suffix.lower()


def _read_local_or_none(url_or_path: str) -> Optional[bytes]:
    if url_or_path.startswith("file://"):
        return Path(url_or_path[7:]).read_bytes()
    p = urlparse(url_or_path)
    if p.scheme in ("", "file"):
        return Path(url_or_path).read_bytes()
    return None


def extract_from_source(
    url_or_path: str,
    expected_ym: str,
    *,
    client: Optional[Client] = None,
    local_pdf_path: Optional[Path] = None,
    html_text: Optional[str] = None,
    csv_text: Optional[str] = None,
    max_pages: int = 2,
    fund_name: str = "",
    issuer: str = "",
) -> Extraction:
    """按 URL 后缀分派 PDF/HTML/CSV. 三通道走同一 extract_unified.md.

    Coolabah 类无扩展的 HTML performance page: 上游若已传 html_text/csv_text,
    优先按内容通道走, 忽略无扩展 URL.
    fund_name/issuer 透传给三通道, 按需注入发行商专项规则 (issuer_rules).
    """
    # 上游已明示通道 (html_text/csv_text 非空) 走内容通道, 绕过后缀检测
    if html_text is not None:
        from .extract_html import extract_from_html
        return extract_from_html(html_text, expected_ym, client=client,
                                 fund_name=fund_name, issuer=issuer)
    if csv_text is not None:
        from .extract_csv import extract_from_csv
        return extract_from_csv(csv_text, expected_ym, client=client,
                                fund_name=fund_name, issuer=issuer)
    ext = _url_suffix(url_or_path)
    if ext == ".pdf":
        if local_pdf_path is None:
            raise ValueError(
                "extract_from_source: PDF 通道需要 local_pdf_path (上游负责下载/兜底)"
            )
        return extract_from_pdf(
            local_pdf_path, expected_ym, client=client, max_pages=max_pages,
            fund_name=fund_name, issuer=issuer,
        )
    if ext in (".html", ".htm"):
        from .extract_html import extract_from_html
        if html_text is None:
            data = _read_local_or_none(url_or_path)
            if data is None:
                raise ValueError(
                    "extract_from_source: HTML 通道需要 html_text 或 file:// URL"
                )
            html_text = data.decode("utf-8", errors="replace")
        return extract_from_html(html_text, expected_ym, client=client,
                                 fund_name=fund_name, issuer=issuer)
    if ext == ".csv":
        from .extract_csv import extract_from_csv
        if csv_text is None:
            data = _read_local_or_none(url_or_path)
            if data is None:
                raise ValueError(
                    "extract_from_source: CSV 通道需要 csv_text 或 file:// URL"
                )
            csv_text = data.decode("utf-8", errors="replace")
        return extract_from_csv(csv_text, expected_ym, client=client,
                                fund_name=fund_name, issuer=issuer)
    raise ValueError(f"extract_from_source: 未支持后缀 {ext!r} (url={url_or_path!r})")
