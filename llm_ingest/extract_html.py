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
from . import issuer_rules

PROMPT_PATH = Path(__file__).parent / "prompts" / "extract_unified.md"

HTML_MAX_TOKENS = 3000
HTML_INPUT_CAP = 80_000  # 与 discover.parse_archive_page 同截断


def load_prompt() -> str:
    return PROMPT_PATH.read_text()


_PLOTLY_HINT = re.compile(
    r"var\s+data\s*=\s*\[|Plotly\.newPlot|"
    r'<script[^>]*type=\"application/json\"[^>]*data-for=',
    re.I,
)


def _shrink_plotly_html(html: str, expected_ym: str) -> str:
    """Plotly HTML 优化: 定位 data 段, 抠含 expected_ym 的 trace.

    Plotly 静态导出常见 3 种数据段承载方式, 按优先级探测:
      (a) htmlwidgets: `<script type="application/json" data-for="...">
          {"x":{"data":[...]}, ...}</script>` (Coolabah 21MB 报告用这种)
      (b) 静态 `var data = [{"name":"...","text":[...]}, ...]` (旧版)
      (c) `Plotly.newPlot('divid', [{...}, {...}], layout)` inline 传参

    切窗口策略 (优先级从高到低):
      1. hovertext NAV 序列命中 `<br />{ym}` (最强锚, Coolabah 用) — 前 15KB 后 5KB
      2. data 段起点 + expected_ym 命中 — 前后各 60KB (旧兼容)
      3. data 段起点 + 无 ym — 头 120KB

    找不到 Plotly 标记 → 返原 HTML.
    """
    if not _PLOTLY_HINT.search(html):
        return html
    # (1) 最强锚: hovertext NAV 序列 `<br />{ym}` — trace 内每月一条, 密集
    hover_hit = html.find(f"<br />{expected_ym}")
    if hover_hit > 0:
        lo = max(0, hover_hit - 60_000)
        hi = min(len(html), hover_hit + 60_000)
        header = "<!-- shrunk Plotly HTML (hovertext anchor) -->\n"
        return header + html[lo:hi]
    # (2) 定位 data 段起点
    start = None
    m_json = re.search(
        r'<script[^>]*type="application/json"[^>]*>',
        html, re.I,
    )
    if m_json:
        start = m_json.end()
    if start is None:
        m_var = re.search(r"var\s+data\s*=\s*\[", html, re.I)
        if m_var:
            start = m_var.start()
    if start is None:
        m_new = re.search(
            r"Plotly\.newPlot\s*\(\s*(?:'[^']*'|\"[^\"]*\")\s*,\s*\[",
            html, re.I,
        )
        if m_new:
            start = m_new.start()
    if start is None:
        return html
    ym_hit = html.find(expected_ym, start)
    if ym_hit > 0:
        lo = max(start, ym_hit - 60_000)
        hi = min(len(html), ym_hit + 60_000)
    else:
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
    fund_name: str = "",
    issuer: str = "",
) -> Extraction:
    """喂 HTML (截 input_cap) + prompt, 返 Extraction.

    与 extract.extract_from_pdf 结构对称:
      - HTML 空 → not_found
      - Plotly HTML → 先抠 data 段
      - LLM 返 JSON 走 parse_response (与 PDF 同 schema)
    fund_name/issuer: 按关键词匹配注入发行商专项规则 (issuer_rules.get_issuer_rule)。
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
        + issuer_rules.get_issuer_rule(fund_name, issuer)
        + f"\n\n目标月份: {expected_ym}\n\n---HTML---\n{shrunk[:input_cap]}"
    )
    resp = client.messages(prompt, max_tokens=max_tokens)
    return parse_response(resp.text, expected_ym)
