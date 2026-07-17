"""CSV 通道 LLM 提取 (Macquarie unit price / 通用 return 序列).

与 extract_html 对称结构: 输入 CSV 文本, 输出 Extraction. 复用 parse_response.

REPORT.md §5.3 flash-lite CSV channel 0% 幻觉 (80 样本), acc 92.5%.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .client import Client
from .extract import Extraction, parse_response

PROMPT_PATH = Path(__file__).parent / "prompts" / "extract_csv.md"

CSV_MAX_TOKENS = 3000
CSV_INPUT_CAP = 80_000  # CSV 通常小, 但巨型历史序列也可能超, 与 HTML 通道一致


def load_prompt() -> str:
    return PROMPT_PATH.read_text()


def extract_from_csv(
    csv_text: str,
    expected_ym: str,
    *,
    client: Optional[Client] = None,
    max_tokens: int = CSV_MAX_TOKENS,
    input_cap: int = CSV_INPUT_CAP,
) -> Extraction:
    """喂 CSV 文本 + prompt, 返 Extraction.

    与 extract_html.extract_from_html 结构对称.
    """
    if not csv_text:
        return Extraction(
            ym=expected_ym, net_return=None, source_quote="",
            measure="unknown", measure_label_in_pdf="",
            rolling={}, not_found=True, raw={"error": "empty_csv"},
        )
    if client is None:
        client = Client()
    prompt = (
        load_prompt()
        + f"\n\n目标月份: {expected_ym}\n\n---CSV---\n{csv_text[:input_cap]}"
    )
    resp = client.messages(prompt, max_tokens=max_tokens)
    return parse_response(resp.text, expected_ym)
