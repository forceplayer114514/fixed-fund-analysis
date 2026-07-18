"""LLM 输出通用解析器 -- LLM 只抄原文 token, 这里做所有数学/单位/校验.

设计原则:
  - LLM 输出无数值字段, 全字符串 (*_text); 本模块把字符串 -> Decimal -> 十进制 float
  - 单位识别以字符串里字面符号为准 (%/‰/bps/$), label 兜底, unit_hint 最弱
  - 括号负数 (0.45) / 全角减号 -0.45 都识别
  - 千分位 `,` 剥除
  - nav_pair 相邻月硬校验 (prev_date +1 month == curr_date, 否则判废)
  - 月份闸: curr_date[:7] == expected_ym (LLM 抓错月份最大风险防线)
  - ParseResult 内部单位**全走十进制** (net_return 与 rolling_decimal 同单位)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ParseResult:
    net_return: Optional[float]                              # 十进制 (0.0065 = 0.65%)
    rolling_decimal: Dict[str, Optional[float]] = field(default_factory=dict)  # 十进制, 与 net_return 同单位
    parse_error: Optional[str] = None                        # 失败原因


# ---------- 数字/单位解析 ----------

# 匹配一个数字 token (可选负号/括号/小数). 括号包裹的算负数.
# 允许千分位逗号在数字内 (如 "1,023.40").
# 括号形式允许数字后有单位符号 (如 "(0.45%)", "(65 bps)").
_NUM_TOKEN_RE = re.compile(
    r"""
    \(\s*                              # 括号左, 表负数
    (?P<paren>-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?)
    \s*(?:%|‰|bps|bp)?                 # 括号内允许单位符号 (被外层 text unit 检测吸收)
    \s*\)                              # 括号右
    |                                  # 或
    (?P<plain>-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?)
    """,
    re.VERBOSE | re.IGNORECASE,
)

# 单位符号识别 (贴着数字或紧跟空白后)
_UNIT_PERCENT_RE = re.compile(r"%")
_UNIT_PERMILLE_RE = re.compile(r"‰")
_UNIT_BPS_RE = re.compile(r"\bbps\b|\bbp\b|\bbasis[\s\-]*point", re.I)
_UNIT_DOLLAR_RE = re.compile(r"\$")

# label 单位提示 (兜底)
_LABEL_PERCENT_RE = re.compile(r"%|\bpercent\b|\bpct\b", re.I)
_LABEL_PERMILLE_RE = re.compile(r"‰|\bpermille\b|\bper[\s\-]*mille\b", re.I)
_LABEL_BPS_RE = re.compile(r"\bbps\b|\bbp\b|\bbasis[\s\-]*point", re.I)


def _parse_number_token(
    text: str,
    *,
    unit_hint: Optional[str] = None,
    label: Optional[str] = None,
) -> Tuple[Optional[Decimal], str]:
    """字符串 -> (Decimal, 单位).

    单位识别优先级 (高→低):
      1. 字面符号 (%/‰/bps/$)
      2. label 里含 "%"/"percent"/"‰"/"bps"
      3. unit_hint (LLM 弱信号)
      4. "unknown"

    括号 (0.45) → -0.45 (会计负数标记).
    千分位 "1,023.40" → 1023.40.
    """
    if not text:
        return (None, "unknown")

    m = _NUM_TOKEN_RE.search(text)
    if not m:
        return (None, "unknown")

    is_paren = m.group("paren") is not None
    raw = (m.group("paren") or m.group("plain") or "").replace(",", "")
    try:
        val = Decimal(raw)
    except (InvalidOperation, ValueError):
        return (None, "unknown")
    if is_paren:
        # 括号只有当数字本身非负时才转负; "(-0.45)" 罕见, 保持原符号
        if val > 0:
            val = -val

    # 单位: 字面符号 (贴数字或整段 text 内)
    unit: Optional[str] = None
    if _UNIT_PERCENT_RE.search(text):
        unit = "percent"
    elif _UNIT_PERMILLE_RE.search(text):
        unit = "permille"
    elif _UNIT_BPS_RE.search(text):
        unit = "bps"
    elif _UNIT_DOLLAR_RE.search(text):
        unit = "dollar"

    # label 兜底
    if unit is None and label:
        if _LABEL_PERCENT_RE.search(label):
            unit = "percent"
        elif _LABEL_PERMILLE_RE.search(label):
            unit = "permille"
        elif _LABEL_BPS_RE.search(label):
            unit = "bps"

    # unit_hint 最弱
    if unit is None and unit_hint:
        unit = unit_hint if unit_hint in ("percent", "decimal", "permille", "bps", "dollar") else None

    if unit is None:
        unit = "unknown"

    return (val, unit)


def _to_decimal_return(dec_value: Decimal, unit: str) -> Optional[float]:
    """(Decimal, unit) -> 十进制月度收益 float.

    percent  -> value / 100
    permille -> value / 1000
    bps      -> value / 10000
    decimal  -> value (已是十进制)
    dollar   -> None (NAV/价格不是收益率)
    unknown  -> None (禁不确定值入库)
    """
    if unit == "percent":
        return float(dec_value / Decimal("100"))
    if unit == "permille":
        return float(dec_value / Decimal("1000"))
    if unit == "bps":
        return float(dec_value / Decimal("10000"))
    if unit == "decimal":
        return float(dec_value)
    return None  # dollar / unknown


# ---------- 月份/相邻性 ----------

def _prev_adjacent(prev_date: str, curr_date: str) -> bool:
    """prev_date + 1 月 == curr_date 月份 (日不校验, 只比 YYYY-MM)."""
    if not prev_date or not curr_date or len(prev_date) < 7 or len(curr_date) < 7:
        return False
    try:
        py, pm = int(prev_date[:4]), int(prev_date[5:7])
        cy, cm = int(curr_date[:4]), int(curr_date[5:7])
    except ValueError:
        return False
    # prev + 1 月
    if pm == 12:
        expected = (py + 1, 1)
    else:
        expected = (py, pm + 1)
    return (cy, cm) == expected


def _curr_matches_expected(curr_date: str, expected_ym: str) -> bool:
    """curr_date 月份 == expected_ym. 不匹配 -> LLM 抓错月, 判废."""
    if not curr_date or not expected_ym:
        return False
    return curr_date[:7] == expected_ym


# ---------- Rolling ----------

def _parse_rolling(obj: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """rolling_text {"3mo":"1.85%",...} → 十进制 {"3mo":0.0185,...}.

    - 缺失/parse 失败 → None (不视为错; rolling 是可选校验)
    - unit unknown 时按 percent 兜 (业绩表滚动列默认 %)
    """
    out: Dict[str, Optional[float]] = {"1mo": None, "3mo": None, "6mo": None, "12mo": None}
    src = obj.get("rolling_text") or obj.get("rolling_pct") or {}
    if not isinstance(src, dict):
        return out
    for k in ("1mo", "3mo", "6mo", "12mo"):
        t = src.get(k)
        if t is None:
            continue
        # rolling_pct 兼容: 若是数字直接当百分数处理
        if isinstance(t, (int, float)):
            out[k] = float(t) / 100.0
            continue
        t = str(t).strip()
        if not t or t.lower() in ("null", "none", "n/a", "-", "–", "—"):
            continue
        v, u = _parse_number_token(t, unit_hint="percent")
        if v is None:
            continue
        if u == "unknown":
            u = "percent"  # rolling 语境默认 %
        dec = _to_decimal_return(v, u)
        out[k] = dec  # None 保留代表 dollar/unknown 单位不接受
    return out


# ---------- 分派 ----------

def _parse_table(obj: Dict[str, Any]) -> ParseResult:
    """table_value / commentary_pct: value_text 单值, label 兜单位."""
    value_text = obj.get("value_text") or ""
    v, u = _parse_number_token(
        value_text,
        unit_hint=obj.get("unit_hint"),
        label=obj.get("measure_label"),
    )
    if v is None:
        return ParseResult(None, {}, "value_parse_fail")
    net = _to_decimal_return(v, u)
    if net is None:
        return ParseResult(None, {}, f"unit_ambiguous:{u}")
    return ParseResult(net, _parse_rolling(obj), None)


def _parse_nav_pair(obj: Dict[str, Any], expected_ym: str) -> ParseResult:
    """nav_pair: curr / prev - 1."""
    curr_date = obj.get("curr_date") or ""
    prev_date = obj.get("prev_date") or ""
    if not _curr_matches_expected(curr_date, expected_ym):
        return ParseResult(None, {}, f"wrong_month:curr={curr_date}")
    if not _prev_adjacent(prev_date, curr_date):
        return ParseResult(None, {}, f"prev_not_adjacent:prev={prev_date}_curr={curr_date}")
    prev_v, _ = _parse_number_token(obj.get("prev_text") or "", unit_hint="dollar")
    curr_v, _ = _parse_number_token(obj.get("curr_text") or "", unit_hint="dollar")
    if prev_v is None or curr_v is None or prev_v <= 0:
        return ParseResult(None, {}, "nav_parse_fail")
    net = float(curr_v / prev_v - Decimal("1"))
    return ParseResult(net, _parse_rolling(obj), None)


def _parse_cum_ex(obj: Dict[str, Any], expected_ym: str) -> ParseResult:
    """cum_ex_dist: (ex + dist) / prev_cum - 1."""
    curr_date = obj.get("curr_date") or ""
    prev_date = obj.get("prev_date") or ""
    if not _curr_matches_expected(curr_date, expected_ym):
        return ParseResult(None, {}, f"wrong_month:curr={curr_date}")
    if not _prev_adjacent(prev_date, curr_date):
        return ParseResult(None, {}, f"prev_not_adjacent:prev={prev_date}_curr={curr_date}")
    prev_cum, _ = _parse_number_token(obj.get("prev_text") or "", unit_hint="dollar")
    curr_ex, _ = _parse_number_token(obj.get("curr_text") or "", unit_hint="dollar")
    dist_v, _ = _parse_number_token(obj.get("dist_text") or "0", unit_hint="dollar")
    if prev_cum is None or curr_ex is None or prev_cum <= 0:
        return ParseResult(None, {}, "cum_ex_parse_fail")
    if dist_v is None:
        dist_v = Decimal("0")
    net = float((curr_ex + dist_v) / prev_cum - Decimal("1"))
    return ParseResult(net, _parse_rolling(obj), None)


def parse_extraction(obj: Dict[str, Any], expected_ym: str) -> ParseResult:
    """LLM 输出 → ParseResult.

    月份闸 (命根子): ym[:7] == expected_ym; curr_date 若给, curr_date[:7] == expected_ym.
      table_value / commentary_pct 若 LLM 未返 curr_date, 仅靠 ym 兜 — LLM 会原样回显
      expected_ym 所以这个校验对 table_value 是自证; 真正拦月份错靠上游选对文件+LLM 挑对 1M 列.
    """
    if not isinstance(obj, dict):
        return ParseResult(None, {}, "obj_not_dict")

    kind = obj.get("kind")
    if kind == "not_found" or obj.get("not_found"):
        return ParseResult(None, {}, None)

    ym = obj.get("ym") or ""
    if ym[:7] != expected_ym:
        return ParseResult(None, {}, f"wrong_month:ym={ym}_vs_expected={expected_ym}")

    curr_date = obj.get("curr_date") or ""
    if curr_date and not _curr_matches_expected(curr_date, expected_ym):
        return ParseResult(None, {}, f"wrong_month:curr={curr_date}")

    if kind in ("table_value", "commentary_pct"):
        return _parse_table(obj)
    if kind == "nav_pair":
        return _parse_nav_pair(obj, expected_ym)
    if kind == "cum_ex_dist":
        return _parse_cum_ex(obj, expected_ym)
    return ParseResult(None, {}, f"unknown_kind:{kind}")


# ---------- Token 收集 (供 verify.check_quote_tokens 用) ----------

def collect_text_tokens(obj: Dict[str, Any]) -> List[str]:
    """收集 obj 里所有非空 *_text 字段, 供 source_quote/doc_text 子串校验用."""
    tokens: List[str] = []
    for k in ("value_text", "prev_text", "curr_text", "dist_text"):
        v = obj.get(k)
        if v and isinstance(v, str):
            s = v.strip()
            if s and s.lower() not in ("null", "none", "n/a"):
                tokens.append(s)
    return tokens
