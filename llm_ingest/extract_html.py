"""HTML 通道 LLM 提取 (Coolabah Plotly / 表格 / Commentary).

与 extract.py 的 PDF 通道对称: 输入原始 HTML, 输出 Extraction. 复用 parse_response.

REPORT.md §5.3 flash-lite HTML channel 0% 幻觉 (30 样本).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .client import Client
from .extract import Extraction, parse_response

PROMPT_PATH = Path(__file__).parent / "prompts" / "extract_html.md"

HTML_MAX_TOKENS = 3000
HTML_INPUT_CAP = 80_000  # 与 discover.parse_archive_page 同截断


def load_prompt() -> str:
    return PROMPT_PATH.read_text()


def extract_from_html(
    html: str,
    expected_ym: str,
    *,
    client: Optional[Client] = None,
    max_tokens: int = HTML_MAX_TOKENS,
    input_cap: int = HTML_INPUT_CAP,
) -> Extraction:
    """喂 HTML (截 input_cap) + prompt, 返 Extraction.

    与 extract.extract_from_pdf 结构对称:
      - HTML 空 → not_found
      - LLM 返 JSON 走 parse_response (与 PDF 同 schema)
    """
    if not html:
        return Extraction(
            ym=expected_ym, net_return=None, source_quote="",
            measure="unknown", measure_label_in_pdf="",
            rolling={}, not_found=True, raw={"error": "empty_html"},
        )
    if client is None:
        client = Client()
    prompt = (
        load_prompt()
        + f"\n\n目标月份: {expected_ym}\n\n---HTML---\n{html[:input_cap]}"
    )
    resp = client.messages(prompt, max_tokens=max_tokens)
    return parse_response(resp.text, expected_ym)
