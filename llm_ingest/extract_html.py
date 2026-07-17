"""HTML 通道 LLM 提取 (Coolabah Plotly / 表格 / Commentary).

与 extract.py 的 PDF 通道对称: 输入原始 HTML, 输出 Extraction. 复用 parse_response.

REPORT.md §5.3 flash-lite HTML channel 0% 幻觉 (30 样本).

Plotly HTML 预处理: 若 HTML 含 `var data = [...]` (Coolabah Plotly 静态导出),
先抠出 data 数组段落, 只把该段发给 LLM. 避免 3.9MB HTML 前 80KB 只含 CSS/header
拿不到 NAV 序列.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .client import Client
from .extract import Extraction, parse_response

PROMPT_PATH = Path(__file__).parent / "prompts" / "extract_html.md"

HTML_MAX_TOKENS = 3000
HTML_INPUT_CAP = 80_000  # 与 discover.parse_archive_page 同截断


def load_prompt() -> str:
    return PROMPT_PATH.read_text()


_PLOTLY_HINT = re.compile(r"var\s+data\s*=\s*\[|Plotly\.newPlot", re.I)


def _shrink_plotly_html(html: str, expected_ym: str) -> str:
    """Plotly HTML 优化: 定位 `var data = [` 段, 抠含 expected_ym 的 trace.

    3.9MB Coolabah HTML 大部分是 layout config / CSS, 真 NAV 数据在
    `var data = [{"name":"...","text":[...]}, ...]` 数组里. 只保留 data
    数组前 100KB 上下文足够 LLM 定位当月.

    找不到 Plotly 标记 → 返原 HTML (让上层截 HTML_INPUT_CAP).
    """
    if not _PLOTLY_HINT.search(html):
        return html
    m = re.search(r"var\s+data\s*=\s*\[", html, re.I)
    if not m:
        return html
    start = m.start()
    # 优先切以 expected_ym 为中心的窗口 (若 ym 在 data 数组内)
    ym_hit = html.find(expected_ym, start)
    if ym_hit > 0:
        # ym 前后各 60KB 窗口, 保 trace name 与 target month
        lo = max(start, ym_hit - 60_000)
        hi = min(len(html), ym_hit + 60_000)
    else:
        # 无 ym 命中: 取 data 数组头 120KB
        lo = start
        hi = min(len(html), start + 120_000)
    header = "<!-- shrunk Plotly HTML -->\n"
    return header + html[lo:hi]


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
      - Plotly HTML → 先抠 data 段
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
    shrunk = _shrink_plotly_html(html, expected_ym)
    prompt = (
        load_prompt()
        + f"\n\n目标月份: {expected_ym}\n\n---HTML---\n{shrunk[:input_cap]}"
    )
    resp = client.messages(prompt, max_tokens=max_tokens)
    return parse_response(resp.text, expected_ym)
