"""Coolabah 系基金 Plotly HTML 报告 NAV 序列提取 (非 PDF 归档源).

Coolabah Capital (coolabahcapital.com) 月报以 `performance-report-<slug>` 的
pandoc HTML 页面发布 (非 PDF), 内嵌 Plotly 图表, hovertext 里带逐日 NAV。
discover2.py 的常规 PDF 归档流程对这类基金找不到任何 .pdf 链接 (无归档页概念)。

本模块只做字面提取 (正则/括号匹配, 无 LLM), 不做计算/推断:
  parse_plotly_nav_series 从 HTML 里转写 trace 的 (date, nav) 数组原文。
调用方负责把 NAV 序列换算成月度收益 (nav_t/nav_{t-1}-1) 并入库, 不在本模块做。

历史教训 (旧 skills/ 管道 2026-07-13): 探测子代理越权直接入库, 且把 AusBond
基准 trace 误当基金类入库——本模块显式排除 name 含 benchmark/index/ausbond
的 trace, 且要求 pattern 精确匹配 1 个 trace (0 个或 >1 个都 raise, 不猜)。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple


_DATA_ARRAY_START_RE = re.compile(r'(?:var\s+data\s*=\s*\[|"data"\s*:\s*\[)')


def _extract_data_array_contents(html: str) -> List[str]:
    """提取全部 Plotly data 数组的括号内内容 (不含外层方括号).

    兼容 JS 形式 `var data = [...]` 与 JSON 形式 `"data":[...]`。用括号匹配
    (跳过字符串字面量内的 `]`) 定位配对的 `]`, 避免正则跨结构误匹配。
    页面可能嵌多个 Plotly 图 (Coolabah 报告页实测: NAV 图不一定是第一个 data
    数组, 只取第一个会漏掉真正含目标 trace 的那个), 因此扫描全部出现位置。
    找不到任何 data 数组时返回空列表 (调用方回退到全文顶层对象扫描)。
    """
    out: List[str] = []
    pos = 0
    while True:
        m = _DATA_ARRAY_START_RE.search(html, pos)
        if not m:
            break
        start = m.end()
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
        if depth == 0:
            out.append(html[start:i - 1])
            pos = i
        else:
            break
    return out


def _split_top_level_objects(s: str) -> List[str]:
    """把字符串切成顶层 `{...}` 对象子串 (括号匹配, 跳过字符串字面量)."""
    objs: List[str] = []
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
            break
        i += 1
    return objs


def parse_plotly_nav_series(
    html: str,
    fund_name_pattern: str,
) -> List[Tuple[str, float]]:
    """从 pandoc HTML 报告 Plotly hovertext 提取基金类 NAV 序列 (逐字转写).

    按 fund_name_pattern 在 trace 的 name 字段过滤; name 含
    Benchmark/Index/AusBond 的 trace 自动丢弃 (结构上 benchmark 不可能混入)。
    多 trace 匹配 pattern -> raise (防误把别的份额类 trace 当目标)。零匹配 ->
    raise (防 pattern 打错时空列表被当"无数据"跳过)。返回 [(date, nav), ...] 升序。
    """
    if not html or not fund_name_pattern:
        raise ValueError("parse_plotly_nav_series: html 与 fund_name_pattern 必填")

    data_contents = _extract_data_array_contents(html)
    trace_objs: List[str] = []
    if data_contents:
        for content in data_contents:
            trace_objs.extend(_split_top_level_objects(content))
    else:
        trace_objs = _split_top_level_objects(html)

    benchmark_markers = ("benchmark", "index", "ausbond")
    matched: List[List[Tuple[str, float]]] = []
    for trace in trace_objs:
        nm = re.search(r'"name"\s*:\s*"([^"]+)"', trace)
        if not nm:
            continue
        name = nm.group(1)
        name_lower = name.lower()
        if any(m in name_lower for m in benchmark_markers):
            continue
        if fund_name_pattern.lower() in name_lower:
            tm = re.search(r'"text"\s*:\s*\[([^\]]+)\]', trace, re.DOTALL)
            if not tm:
                continue
            text_arr = tm.group(1)
            points = re.findall(
                r'"([^"]*?)<br />(\d{4}-\d{2}-\d{2}):\s*\$([\d,.]+)"',
                text_arr,
            )
            series = [
                (date, float(nav_str.replace(",", "")))
                for _trace_name, date, nav_str in points
            ]
            matched.append(series)

    if len(matched) == 0:
        raise ValueError(
            f"parse_plotly_nav_series: 零匹配 pattern={fund_name_pattern!r} "
            "(benchmark 已排除)"
        )
    if len(matched) > 1:
        raise ValueError(
            f"parse_plotly_nav_series: 多 trace 匹配 pattern={fund_name_pattern!r}, "
            f"命中 {len(matched)} 条, 需换更精确的 pattern"
        )
    return sorted(matched[0], key=lambda x: x[0])
