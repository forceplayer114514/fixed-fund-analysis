"""HTML 通道 LLM 提取 (表格 / Commentary).

与 extract.py 的 PDF 通道对称: 输入原始 HTML, 输出 Extraction. 复用 parse_response.

REPORT.md §5.3 flash-lite HTML channel 0% 幻觉 (30 样本).

Coolabah 类超大 Plotly 网页 (含逐月 NAV 走势的图表控件, 十几 MB 起) 原来在这里
靠字节窗口切片 (定位数据段附近截一小块) 压进 input_cap, 已放弃 --
真实数据上认错过版面 (把"成立以来汇总卡片"当成月度收益表), 见
html_to_pdf.py 模块说明。现改走 html_to_pdf.render_html_to_pdf 整页渲染成 PDF,
与普通 PDF 月报走同一条提取通道, 不再对 HTML 文本做窗口裁剪。本模块现只处理
不需要裁剪就能塞进 input_cap 的普通 HTML (表格/正文段落)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .client import Client
from .extract import Extraction, parse_response
from . import issuer_rules

PROMPT_PATH = Path(__file__).parent / "prompts" / "extract_unified.md"

HTML_MAX_TOKENS = 3000
HTML_INPUT_CAP = 80_000  # 本模块自身的 HTML 输入上限


def load_prompt() -> str:
    return PROMPT_PATH.read_text()


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
      - LLM 返 JSON 走 parse_response (与 PDF 同 schema)
    fund_name/issuer: 按关键词匹配注入发行商专项规则 (issuer_rules.get_issuer_rule)。
    超大网页 (Coolabah 类 Plotly 图表页) 不走这里, 见模块说明。
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
        + issuer_rules.get_issuer_rule(fund_name, issuer)
        + f"\n\n目标月份: {expected_ym}\n\n---HTML---\n{html[:input_cap]}"
    )
    resp = client.messages(prompt, max_tokens=max_tokens)
    return parse_response(resp.text, expected_ym)
