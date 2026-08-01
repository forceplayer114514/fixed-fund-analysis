"""HTML (含 JS 图表 hover-only 数据) -> PDF, 代码层硬约束通道 (非 LLM 判断).

背景 (Spec F, docs/superpowers/plans/2026-07-20-html-rendered-pdf-channel.md):
字节级文本窗口切片 (extract_html.py 旧法) 会认错表格/卡片 -- 版面结构在切片
那一刻已经丢失, 靠正则锚点/窗口大小猜, 会不断复现同类错误 (2026-07-20 Coolabah
Institutional 2026-06 年化误当月度事故)。

改法: 浏览器渲染原页 -> 读图表库 (Plotly 等) 挂在 DOM 上的原始 data 对象 (hover
tooltip 文本的来源, 不用触发 hover 事件, 直接读 JS 对象比模拟鼠标更快更稳定,
对比见 test2/REPORT.md 方案A vs 方案B) -> 拼成表格追加进页面 -> 整页打印成 PDF。
版面结构在 PDF 里天然保留, 交给现成的 PDF 提取通道 (extract.extract_from_pdf),
不再靠窗口猜。

只做字面转写 (读 DOM 上已有的 text 数组, 不触发/不计算), 转换步骤本身不引入
LLM 判断。
"""
from __future__ import annotations

import html as html_mod
from pathlib import Path
from typing import Any, Dict, List

MIN_HOVER_POINTS = 5  # 过滤单点图例标记 trace, 只留真实序列 (test2/REPORT.md 方案A 验证过的阈值)

# 视口宽度: 目标页面里 "Fund: ..." 抬头和 "Return (since ...)" 摘要行用了
# `white-space: nowrap` 的窄列表格, 视口不够宽时这两行会被内容溢出裁掉 (肉眼
# 看/打印出的 PDF 都一样, 不是 Playwright 独有的问题, 原始 HTML 里文字本身
# 是全的)。2026-07-20 端到端联调实测: 1400px 视口把 "Institutional Class"
# 裁成 "Institut", 汇总行 "9.04% pa net)" 裁成 "(9." -- LLM 逐字转写了这个
# 被裁断的抬头, 触发自动纠名 (rename_fund_id) 把 fund_id 错改成
# coolabah_floating_rate_high_yield_fund_institut, 污染了生产库。2400px 视口
# 实测这两处都能完整渲染, 留够余量 (页面里最长的基金全称也就 60 出头字符)。
REPORT_VIEWPORT = {"width": 2400, "height": 1200}
# 打印纸张: 宽度按渲染后实际内容宽度取 (见 render_html_to_pdf 里的说明), 高度
# 固定分页 -- 报告页实测近 4 万像素高, 单页会超出 PDF 页面尺寸上限。
PRINT_PAGE_HEIGHT_PX = 1600
PRINT_WIDTH_SLACK_PX = 80    # 左右各 10mm 页边距 (~76px) 的余量
_MEASURE_CONTENT_JS = """() => ({
  w: Math.max(
    document.documentElement ? document.documentElement.scrollWidth : 0,
    document.body ? document.body.scrollWidth : 0
  )
})"""

# 只读 DOM, 不做筛选/计算 -- 筛选逻辑放 Python 侧 (_filter_hover_rows), 便于不
# 依赖真实浏览器单测。
_DUMP_TRACES_JS = """
() => {
    const plots = document.querySelectorAll('.js-plotly-plot, .plotly-graph-div');
    const out = [];
    plots.forEach((gd) => {
        if (!gd.data) return;
        out.push({
            plotId: gd.id || '',
            traces: gd.data.map((t) => ({
                name: t.name || null,
                hoverinfo: t.hoverinfo || null,
                text: Array.isArray(t.text) ? t.text : (t.text ? [t.text] : []),
            })),
        });
    });
    return out;
}
"""

_INJECT_APPENDIX_JS = """
(html) => {
    const div = document.createElement('div');
    div.id = 'injected-hover-appendix';
    div.style.padding = '20px';
    div.innerHTML = '<h2>Appendix: chart data (hover-only source)</h2>' + html;
    document.body.appendChild(div);
}
"""


class HtmlToPdfError(RuntimeError):
    pass


def _filter_hover_rows(plots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从 JS dump 的原始 trace 里挑出真实 hover-only 序列 (逐字转写, 不计算).

    过滤规则 (test2/REPORT.md 方案A 实测过): hoverinfo=="text" 且 text 数组
    长度 >= MIN_HOVER_POINTS -- 排掉单点图例标记 trace。
    """
    sections: List[Dict[str, Any]] = []
    for plot in plots:
        rows: List[Dict[str, str]] = []
        for trace in plot.get("traces", []):
            if trace.get("hoverinfo") != "text":
                continue
            texts = trace.get("text") or []
            if len(texts) < MIN_HOVER_POINTS:
                continue
            name = trace.get("name") or "(unnamed)"
            for t in texts:
                if not isinstance(t, str):
                    continue
                clean = t.replace("<br/>", " | ").replace("<br />", " | ")
                rows.append({"series": name, "text": clean})
        if rows:
            sections.append({"plotId": plot.get("plotId", ""), "rows": rows})
    return sections


def _build_appendix_html(sections: List[Dict[str, Any]]) -> str:
    """把过滤后的 sections 拼成 <table> HTML (html.escape 防注入)."""
    parts = []
    for sec in sections:
        rows_html = "".join(
            f"<tr><td>{html_mod.escape(r['series'])}</td>"
            f"<td>{html_mod.escape(r['text'])}</td></tr>"
            for r in sec["rows"]
        )
        parts.append(
            f'<h3>Hover Data Appendix — chart: {html_mod.escape(sec["plotId"])}</h3>'
            '<table border="1" cellpadding="4" cellspacing="0" '
            'style="border-collapse:collapse;font-size:11px;">'
            f"<tr><th>Series</th><th>Hover text (raw)</th></tr>{rows_html}</table>"
        )
    return '<div style="page-break-before:always;"></div>'.join(parts)


def render_html_to_pdf(url: str, out_path: Path, *, timeout: int = 120) -> Path:
    """导航到 url, 抽 hover-only 序列注入附录表格, 整页打印 PDF.

    确定性: 不依赖鼠标坐标/事件时序 (test2/REPORT.md 方案B 实测 88 点 hover
    全部错位, 已否决), 直接读 graphDiv.data 底层数组。

    找不到图表 (非 Plotly / gd.data 为空 / 无满足条件的 trace) 时仍正常打印
    (附录段落为空), 不报错 -- 兼容非 Plotly 的普通 HTML 报告页。

    渲染失败 (playwright 不可用 / 超时 / 输出空文件) 一律抛 HtmlToPdfError,
    调用方按现有 record_confirmed_gap 路径处理 -- 不允许调用方悄悄退回旧的
    字节窗口方法 (那等于重新引入已判定不可靠的软约束路径)。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise HtmlToPdfError(f"playwright 不可用: {e}") from e

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport=REPORT_VIEWPORT)
                page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                plots = page.evaluate(_DUMP_TRACES_JS) or []
                sections = _filter_hover_rows(plots)
                if sections:
                    appendix_html = _build_appendix_html(sections)
                    page.evaluate(_INJECT_APPENDIX_JS, appendix_html)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                # 纸张宽度必须盖住渲染后的实际内容宽度, 否则超出的部分会被
                # **横向裁掉**(不是缩放, 也不是换行)。原来固定 format="A4":
                # 可打印宽度约 774px, 而报告页内容 1200px 宽, 于是每一行都在
                # 同一列被切断, 实测抬头变成
                #   'Fund: Coolabah Global Floating-Rate High Yield Co'
                #   'Return (since Feb. 2025): 7.65% pa gross (6.4'
                # -- 基金名被截断直接把身份闸判成兄弟基金 (identity_mismatch),
                # 数字被截断则更危险 (6.45 变 6.4 是个看起来完全合法的错值)。
                # 高度仍固定分页: 内容实测近 4 万像素, 单页会超出 PDF 页面尺寸
                # 上限; 纵向分页不会切字 (跨页的行整体挪到下一页)。
                dims = page.evaluate(_MEASURE_CONTENT_JS) or {}
                page_w = max(int(dims.get("w") or 0),
                             REPORT_VIEWPORT["width"]) + PRINT_WIDTH_SLACK_PX
                page.pdf(
                    path=str(out_path),
                    width=f"{page_w}px",
                    height=f"{PRINT_PAGE_HEIGHT_PX}px",
                    print_background=True,
                    margin={"top": "10mm", "bottom": "10mm",
                            "left": "10mm", "right": "10mm"},
                )
            finally:
                browser.close()
    except HtmlToPdfError:
        raise
    except Exception as e:  # noqa: BLE001
        raise HtmlToPdfError(f"渲染失败: {url}: {e}") from e

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise HtmlToPdfError(f"PDF 渲染失败或空文件: {out_path}")
    return out_path
