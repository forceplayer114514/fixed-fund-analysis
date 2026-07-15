"""候选提取器:通用地从 PDF 文本产出所有"带上下文的百分比数值"作为月收益候选。

设计动机(Phase 1):现有清洗层为每个发行商写专属正则提取器,新基金入库要人肉探
正则(GCI 一役 ~39min 探同一条正则)。第一性原理:PDF 每份月报自带校验钥匙 --
performance 表 3mo/6mo/12mo 滚动收益。正确的月度序列复利后必须与滚动值吻合。
用这个约束反向自动选值,只需候选池覆盖真值。

铁律(与 skills/CLAUDE.md 一致):
- 候选值必须全部来自真实提取文本,带 source_quote 可追溯;禁止生成 / 反推 /
  合理性纠正。求解器只做"选择",不做"计算生成"。
- 候选值经 _pct_to_decimal 无损转换(Decimal 移位,非二进制舍入)。
- benchmark 上下文候选强制 ambiguous_context=True,由求解器/pending 兜底。

CANDIDATE_PATTERNS 用当前已知 5 个专属正则 + generic finditer + perf 表 1mo
组成的模式池;新发行商若真值不在池中,才走加候选模式流程(add_fixed_fund.md 4.5)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from lib.extract import (
    _BENCHMARK_GUARD_RE,
    _pct_to_decimal,
    clean_spacing,
    extract_bentham_rolling,
    extract_gci_rolling,
    extract_perf_rolling,
    parse_pdf_text,
)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReturnCandidate:
    """单个月收益候选。

    value: 小数(如 0.0053),由 _pct_to_decimal 无损转换。
    source_quote: 匹配原文片段(如 'returned 0.53%')。求解器不生成期望值入库,
        chosen 候选的 source_quote 就是数据来源可追溯载体。
    pattern_tag: 候选模式标签,供 pending_review 展示与调试。取值:
        - returned_pct: r"returned\\s+X%"(现 generic,finditer 取全部匹配)
        - total_return_after_fees: r"had a total return \\(after fees\\*?\\) of X"
        - total_returns_net: r"Total Returns? \\(Net\\) X%"
        - net_return_nta_row: r"Net Return Based on NTA X%"
        - nta_net_return_label: GCI "NTA Net Return" 标签下一行数值
        - perf_table_1mo: performance 表 1mo 列(已知 Stake 口径错,靠约束淘汰)
    char_pos: 匹配起始位置,同值不同位置区分(finditer 多匹配去重时保留代表位置)。
    priority: 展示优先级(0=专属模式源 1=generic 2=表格 1mo)。仅影响 pending
        展示哪个候选为代表 net_return,不影响求解器裁决。
    ambiguous_context: 匹配点前后 200 字符窗口含 benchmark/index/outperform 等
        词 -> True。求解器不因此淘汰(约束会淘汰错值),但输出到 pending 时会
        提示该候选可能来自 benchmark 上下文。
    """
    value: float
    source_quote: str
    pattern_tag: str
    char_pos: int
    priority: int = 1
    ambiguous_context: bool = False


PatternFn = Callable[[str], list[ReturnCandidate]]


# ---------------------------------------------------------------------------
# 单模式候选提取函数
# ---------------------------------------------------------------------------

# 一份 PDF 里出现次数极多的 X.XX% token 会污染候选池(如 rolling 表本身、
# benchmark 值、asset allocation 百分比等)。generic finditer 已带 200 字符
# ambiguous_context 兜底;专属模式(bentham/kkc/gci)本身正则强,匹配次数极少。


_BENTHAM_NET_RE = re.compile(
    r"had a total return\s*\(after fees\*?\)\s*of\s*([+-]?\d+\.\d+)\s*(?:%|percent)",
    re.IGNORECASE,
)

_KKC_NET_RE = re.compile(
    r"Total Returns? \(Net\)\s*([+-]?\d+\.\d+)%",
)

_KKC_NTA_RE = re.compile(
    r"Net Return Based on NTA\s*\(?%?\)?\s*([+-]?\d+\.\d+)%",
)

_GENERIC_RETURNED_RE = re.compile(
    r"returned\s+([+-]?\d+\.\d+)%",
)


def _quote_window(text: str, start: int, end: int, span: int = 40) -> str:
    """取匹配前后各 span 字符的原文窗口(不改数据,仅供 source_quote 展示)。"""
    lo = max(0, start - span)
    hi = min(len(text), end + span)
    return text[lo:hi].strip()


def _is_ambiguous_context(text: str, start: int, end: int) -> bool:
    """匹配点前后 200 字符窗口内含 benchmark/index/outperform 等词 -> True。"""
    lo = max(0, start - 200)
    hi = min(len(text), end + 200)
    return bool(_BENCHMARK_GUARD_RE.search(text[lo:hi]))


def _finditer_candidates(
    text: str, regex: "re.Pattern[str]", pattern_tag: str, priority: int
) -> list[ReturnCandidate]:
    """跑 regex.finditer(text),每个匹配产一个候选。"""
    out: list[ReturnCandidate] = []
    for m in regex.finditer(text):
        raw = m.group(1)
        try:
            v = _pct_to_decimal(raw)
        except Exception:
            continue
        out.append(ReturnCandidate(
            value=v,
            source_quote=m.group(0),
            pattern_tag=pattern_tag,
            char_pos=m.start(),
            priority=priority,
            ambiguous_context=_is_ambiguous_context(text, m.start(), m.end()),
        ))
    return out


def pattern_bentham(text: str) -> list[ReturnCandidate]:
    """Bentham 'had a total return (after fees[*]) of X[%|percent]' -> net。"""
    return _finditer_candidates(text, _BENTHAM_NET_RE, "total_return_after_fees", 0)


def pattern_kkc_net(text: str) -> list[ReturnCandidate]:
    """KKC 'Total Returns (Net) X%' -> net (先 clean_spacing 再匹配)。"""
    t = clean_spacing(text)
    return _finditer_candidates(t, _KKC_NET_RE, "total_returns_net", 0)


def pattern_kkc_nta(text: str) -> list[ReturnCandidate]:
    """KKC 'Net Return Based on NTA X%' -> net (先 clean_spacing 再匹配)。"""
    t = clean_spacing(text)
    return _finditer_candidates(t, _KKC_NTA_RE, "net_return_nta_row", 0)


def pattern_gci_label(text: str) -> list[ReturnCandidate]:
    """GCI 'NTA Net Return' 标签行 -> 下一行数值(不用正则,行定位)。

    多次匹配同一标签时取全部;数值行格式必须是纯 X.XX% 或 X.XX。
    """
    if not text:
        return []
    lines = text.split("\n")
    out: list[ReturnCandidate] = []
    cursor = 0  # 记录字符 offset
    for i, ln in enumerate(lines):
        line_len = len(ln) + 1  # 含 \n
        if "NTA Net Return" in ln and i + 1 < len(lines):
            val_str = lines[i + 1].strip().replace("%", "")
            m = re.match(r"^[+-]?\d+\.\d+$", val_str)
            if m:
                try:
                    v = _pct_to_decimal(val_str)
                except Exception:
                    cursor += line_len
                    continue
                # source_quote 用两行拼接
                quote = f"NTA Net Return (%): {lines[i + 1].strip()}"
                out.append(ReturnCandidate(
                    value=v,
                    source_quote=quote,
                    pattern_tag="nta_net_return_label",
                    char_pos=cursor,
                    priority=0,
                    ambiguous_context=False,  # 表标签行不易受 benchmark 污染
                ))
        cursor += line_len
    return out


def pattern_returned(text: str) -> list[ReturnCandidate]:
    """generic 'returned X%' -> 全部匹配(finditer,非单匹配)。

    benchmark 上下文命中时 ambiguous_context=True,由求解器约束或 pending 兜底。
    """
    return _finditer_candidates(text, _GENERIC_RETURNED_RE, "returned_pct", 1)


def pattern_perf_table_1mo(text: str) -> list[ReturnCandidate]:
    """performance 表 1mo 列(Stake 已证口径可能错,靠求解器约束淘汰)。

    从 extract_perf_rolling 输出取 1mo(它已处理列错位/合并/空列)。仅当值非
    None 时产候选。source_quote 用 'perf_table 1mo: X.XX%' 记号。
    """
    r = extract_perf_rolling(text)
    v = r.get("1mo")
    if v is None:
        return []
    return [ReturnCandidate(
        value=float(v),
        source_quote=f"perf_table 1mo: {v * 100:.4f}%",
        pattern_tag="perf_table_1mo",
        char_pos=-1,  # 表提取路径无精确 char_pos,-1 记号
        priority=2,
        ambiguous_context=False,
    )]


# CANDIDATE_PATTERNS:(pattern_tag, priority, fn) 元组序列。求解器/展示按顺序
# 处理,priority 越小越"权威";候选池对求解器裁决平等参与,priority 只在
# pending_review 展示时用于挑代表候选。
CANDIDATE_PATTERNS: list[tuple[str, int, PatternFn]] = [
    ("total_return_after_fees", 0, pattern_bentham),
    ("total_returns_net",       0, pattern_kkc_net),
    ("net_return_nta_row",      0, pattern_kkc_nta),
    ("nta_net_return_label",    0, pattern_gci_label),
    ("returned_pct",            1, pattern_returned),
    ("perf_table_1mo",          2, pattern_perf_table_1mo),
]


# ---------------------------------------------------------------------------
# 候选池组装 + Rolling 通用探测
# ---------------------------------------------------------------------------


def generate_candidates(text: str) -> list[ReturnCandidate]:
    """跑全部候选模式,返回候选列表。

    去重规则:按 round(value, 6) 分组;同值不同来源(如 returned_pct 与
    perf_table_1mo 都命中 0.0053)保留全部候选(求解器按 distinct value 判唯一,
    展示时能列全部原文片段)。空文本返空列表。
    """
    if not text:
        return []
    out: list[ReturnCandidate] = []
    for _tag, _prio, fn in CANDIDATE_PATTERNS:
        try:
            out.extend(fn(text))
        except Exception:
            # 单模式失败不影响其他模式(fitz/正则少见异常)
            continue
    return out


ROLLING_EXTRACTORS: list[tuple[str, Callable[[str], dict]]] = [
    ("perf",    extract_perf_rolling),
    ("bentham", extract_bentham_rolling),
    ("gci",     extract_gci_rolling),
]


def _detect_precision(text: str) -> int:
    """启发式:表格数值最小小数位数。

    扫全部 X.X 或 X.XX 匹配,取最短小数位。空 -> 默认 2(与 PDF 常见 2dp 一致)。
    供求解器自适应容差(1dp 时容差放大 5 倍,宁 pending 不误选)。
    """
    if not text:
        return 2
    lens = [len(m.group(1)) for m in re.finditer(r"\d+\.(\d+)", text)]
    return min(lens) if lens else 2


def extract_rolling_any(text: str) -> dict:
    """依次尝试 perf / bentham / gci 三种 rolling 提取器,取首个 parse_error=False。

    附加字段:
      'extractor_tag': 命中的提取器名('perf'/'bentham'/'gci'/'none')。
      'precision': text 中数值最小小数位数(供求解器容差自适应)。
    全失败 -> 返 parse_error=True + precision(仍尝试探测)。
    """
    for tag, fn in ROLLING_EXTRACTORS:
        try:
            r = fn(text)
        except Exception:
            continue
        if not r.get("parse_error"):
            r["extractor_tag"] = tag
            r["precision"] = _detect_precision(text)
            return r
    # 全失败,返最后一次的 parse_error 结果 + 探到的 precision
    return {"1mo": None, "3mo": None, "6mo": None, "12mo": None,
            "inception": None, "parse_error": True,
            "extractor_tag": "none", "precision": _detect_precision(text)}


def extract_pdf_one_candidates(
    pdf_path: str, max_pages: Optional[int] = None,
) -> tuple[list[ReturnCandidate], dict]:
    """单 PDF -> (候选池, rolling dict)。

    parse_pdf_text 异常 -> ([], parse_error=True 占位 rolling)。顶层纯函数,可
    被 ThreadPool 调用;下游 solver 用候选池 + rolling 做约束求解。
    """
    try:
        text = parse_pdf_text(pdf_path, max_pages=max_pages)
    except Exception:
        return ([], {"1mo": None, "3mo": None, "6mo": None, "12mo": None,
                     "inception": None, "parse_error": True,
                     "extractor_tag": "none", "precision": 2})
    return (generate_candidates(text), extract_rolling_any(text))
