"""prompt 构造 + JSON 解析 -> Extraction 数据类."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from . import pdf as pdf_mod
from .client import Client, DEFAULT_MAX_TOKENS

PROMPT_PATH = Path(__file__).parent / "prompts" / "extract_month.md"


@dataclass(frozen=True)
class Extraction:
    ym: str
    net_return: Optional[float]  # 十进制, 如 0.0065
    source_quote: str
    measure: str  # 期望 "net_monthly"
    measure_label_in_pdf: str
    rolling: Dict[str, Optional[float]]  # 百分数 (0.65 = 0.65%)
    not_found: bool
    raw: Dict[str, Any] = field(default_factory=dict)  # 原始 JSON, 失败进 pending.candidates_json
    parse_error: Optional[str] = None  # 解析失败原因


class ExtractError(RuntimeError):
    pass


_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json_str(text: str) -> str:
    """text 里剥出 JSON. 优先剥去 ```json ... ``` 包裹, 兜底找第一个 {...}."""
    t = text.strip()
    # 去 markdown fence
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    m = _JSON_RE.search(t)
    if not m:
        raise ExtractError("no_json_object_in_text")
    return m.group(0)


def parse_response(text: str, expected_ym: str) -> Extraction:
    """把 sub2api 返回的文本解析成 Extraction. 严格, 失败即抛."""
    try:
        js = _extract_json_str(text)
        obj = json.loads(js)
    except (ExtractError, json.JSONDecodeError) as e:
        return Extraction(
            ym=expected_ym, net_return=None, source_quote="", measure="unknown",
            measure_label_in_pdf="", rolling={}, not_found=True,
            raw={"error": "parse_failed", "text": text[:2000]},
            parse_error=str(e),
        )
    ym = obj.get("ym") or expected_ym
    not_found = bool(obj.get("not_found"))
    pct = obj.get("net_return_pct")
    net_return: Optional[float] = None
    if pct is not None and not not_found:
        try:
            net_return = float(pct) / 100.0
        except (TypeError, ValueError):
            net_return = None

    rolling_raw = obj.get("rolling_pct") or {}
    rolling: Dict[str, Optional[float]] = {}
    for k in ("1mo", "3mo", "6mo", "12mo"):
        v = rolling_raw.get(k)
        if v is None:
            rolling[k] = None
        else:
            try:
                rolling[k] = float(v)
            except (TypeError, ValueError):
                rolling[k] = None

    return Extraction(
        ym=ym,
        net_return=net_return,
        source_quote=str(obj.get("source_quote") or ""),
        measure=str(obj.get("measure") or "unknown"),
        measure_label_in_pdf=str(obj.get("measure_label_in_pdf") or ""),
        rolling=rolling,
        not_found=not_found,
        raw=obj,
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
) -> Extraction:
    """跑一次提取. not_found 时安全网: 加载全文重试一次.

    max_pages<=0 表示直接喂全文.
    """
    if client is None:
        client = Client()
    prompt = load_prompt()
    pdf_bytes = pdf_mod.clip_pages(pdf_path, max_pages=max_pages)
    resp = client.messages_with_pdf(prompt, pdf_bytes, max_tokens=max_tokens)
    ex = parse_response(resp.text, expected_ym)
    # 安全网: not_found + 裁剪过 -> 全文重试
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


# ---------- Spec D: 通道 dispatch ----------

def _url_suffix(url_or_path: str) -> str:
    """URL/路径 → 小写后缀 (含点), 无后缀 = ''."""
    p = urlparse(url_or_path)
    return Path(p.path).suffix.lower()


def _read_local_or_none(url_or_path: str) -> Optional[bytes]:
    """file:// url 或本地路径 → bytes; http(s) → None (由通道内部去下)."""
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
) -> Extraction:
    """按 URL 后缀分派 PDF/HTML/CSV.

    PDF: 用 local_pdf_path (已由上游 _run_ingest_job 处理下载/缓存)
    HTML/CSV: 用 html_text/csv_text (已由上游 fetch), 或 file:// url 直读

    上游用法:
      # PDF 走本地路径 (已下载或本地缓存)
      extract_from_source(url, ym, local_pdf_path=Path("..."))
      # HTML/CSV: 直接传入 text 内容
      extract_from_source(url, ym, html_text=html)
      extract_from_source(url, ym, csv_text=csv)
    """
    ext = _url_suffix(url_or_path)
    if ext == ".pdf":
        if local_pdf_path is None:
            raise ValueError(
                "extract_from_source: PDF 通道需要 local_pdf_path (上游负责下载/兜底)"
            )
        return extract_from_pdf(
            local_pdf_path, expected_ym, client=client, max_pages=max_pages,
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
        return extract_from_html(html_text, expected_ym, client=client)
    if ext == ".csv":
        from .extract_csv import extract_from_csv
        if csv_text is None:
            data = _read_local_or_none(url_or_path)
            if data is None:
                raise ValueError(
                    "extract_from_source: CSV 通道需要 csv_text 或 file:// URL"
                )
            csv_text = data.decode("utf-8", errors="replace")
        return extract_from_csv(csv_text, expected_ym, client=client)
    raise ValueError(f"extract_from_source: 未支持后缀 {ext!r} (url={url_or_path!r})")
