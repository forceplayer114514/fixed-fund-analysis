"""两道闸.

闸 1 引用硬校验 (`check_quote_tokens`, 挡编数字):
  归一化压平空白后, LLM 返的每个 `*_text` token 必须**同时**是:
    - source_quote 的子串 (防 LLM 编 quote)
    - doc_text (PDF 全文 / HTML 原文 / CSV 文本) 的子串 (防 LLM 编数据)

闸 2 数学交叉 (`check_rolling`, 挡字段类型错):
  月度值复利对 3/6/12mo 滚动窗口, 绝对误差 < VERIFY_TOL.
  单位: net_return / rolling_decimal 全走十进制 (与 parsers.ParseResult 一致).

不含旧 pct 匹配 / NAV pair 数学复算 — Spec E 单位模糊源, 已下沉到 parsers.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

VERIFY_TOL = 0.005  # 十进制 0.5% 容差 (rolling 复利与 monthly 复算比对)


# ---------- 归一化 (PyMuPDF 空格怪癖兜底) ----------

_WS_RE = re.compile(r"\s+")

# 归一化数字/符号周边空白:
#   "0.65 %"     -> "0.65%"
#   "$ 1,023"    -> "$1,023"
#   "0. 65"      -> "0.65"   (小数点两侧空白)
#   "( 0.45 )"   -> "(0.45)"
#   "-0.65 %"    -> "-0.65%"
_TIGHT_PERCENT_RE = re.compile(r"(\d)\s+(%|‰)")
_TIGHT_DOLLAR_RE = re.compile(r"(\$)\s+(\d)")
_TIGHT_PAREN_OPEN_RE = re.compile(r"\(\s+(-?\d)")
_TIGHT_PAREN_CLOSE_RE = re.compile(r"(\d)\s+\)")
_TIGHT_DOT_RE = re.compile(r"(\d)\s*\.\s*(\d)")
_TIGHT_COMMA_RE = re.compile(r"(\d)\s*,\s*(\d)")
_TIGHT_MINUS_RE = re.compile(r"-\s+(\d)")


def _flatten(s: str) -> str:
    """压平空白 + 数字/符号周边紧贴, 兜住 PyMuPDF 抽取的空格怪癖."""
    if not s:
        return ""
    t = _WS_RE.sub(" ", s)
    t = _TIGHT_DOT_RE.sub(r"\1.\2", t)
    t = _TIGHT_COMMA_RE.sub(r"\1,\2", t)
    t = _TIGHT_PERCENT_RE.sub(r"\1\2", t)
    t = _TIGHT_DOLLAR_RE.sub(r"\1\2", t)
    t = _TIGHT_PAREN_OPEN_RE.sub(r"(\1", t)
    t = _TIGHT_PAREN_CLOSE_RE.sub(r"\1)", t)
    t = _TIGHT_MINUS_RE.sub(r"-\1", t)
    return t.strip()


# ---------- 闸 1: check_quote_tokens ----------

@dataclass(frozen=True)
class QuoteCheck:
    passed: bool
    reason: str


def check_quote_tokens(
    text_tokens: List[str],
    source_quote: str,
    doc_text: str,
) -> QuoteCheck:
    """闸 1: 每个 token 必须同时是 source_quote 和 doc_text 的子串 (归一化后).

    text_tokens: parsers.collect_text_tokens(obj) 输出, LLM 返的 value_text / prev_text /
                 curr_text / dist_text 中非空者.
    source_quote: LLM 返的 source_quote 字段.
    doc_text: 原文 (PDF 全文 / HTML / CSV).

    空 token 列表 → 视 not_found, 无需校验, pass (调用方应先判 net_return is None).
    """
    if not text_tokens:
        return QuoteCheck(True, "no_tokens_to_check")
    if not source_quote:
        return QuoteCheck(False, "empty_source_quote")
    if not doc_text:
        return QuoteCheck(False, "empty_doc_text")
    q = _flatten(source_quote)
    d = _flatten(doc_text)
    for tok in text_tokens:
        t = _flatten(tok)
        if not t:
            continue
        if t not in q:
            return QuoteCheck(False, f"token_not_in_quote:{tok[:40]!r}")
        if t not in d:
            return QuoteCheck(False, f"token_not_in_doc:{tok[:40]!r}")
    return QuoteCheck(True, "ok_tokens")


# ---------- 闸 2: check_rolling (十进制单位) ----------

@dataclass(frozen=True)
class RollingCheck:
    passed: bool
    reason: str
    windows_verified: int


def check_rolling(
    net_return: Optional[float],
    ym: str,
    monthly_history: Dict[str, float],
    rolling_decimal: Dict[str, Optional[float]],
    tol: float = VERIFY_TOL,
) -> RollingCheck:
    """闸 2: monthly 复利对 3/6/12mo 滚动值 (十进制).

    net_return: 当月十进制值.
    ym: "YYYY-MM".
    monthly_history: 已入库 {ym: net_return_decimal}.
    rolling_decimal: 十进制 {"1mo":0.0065,"3mo":0.0185,"6mo":0.0365,"12mo":0.0787}.

    对每个窗口 W in {3,6,12}: rolling_decimal[Wmo] 存在且 monthly_history 覆盖前 W-1 月,
    则 (1+net_return) * prod(1+m_prev) - 1 vs rolling_decimal[Wmo], abs diff < tol.

    rolling 缺失 (全 None / 空 dict) → pass, windows_verified=0 (rolling 是可选校验).
    """
    if net_return is None:
        return RollingCheck(True, "no_net_return", 0)  # not_found 时不校验

    reported = {}
    if isinstance(rolling_decimal, dict):
        for w in (3, 6, 12):
            v = rolling_decimal.get(f"{w}mo")
            if v is not None:
                reported[w] = v
    if not reported:
        return RollingCheck(True, "no_rolling_reported", 0)

    verified = 0
    for w, roll_dec in reported.items():
        prevs = _prev_yms(ym, w - 1)
        if not all(pm in monthly_history for pm in prevs):
            continue
        product = 1.0 + net_return
        for pm in prevs:
            product *= 1.0 + monthly_history[pm]
        implied = product - 1.0
        if abs(implied - roll_dec) < tol:
            verified += 1
        else:
            return RollingCheck(
                False,
                f"mismatch_{w}mo(implied={implied:.6f}_vs_reported={roll_dec:.6f})",
                verified,
            )
    if verified == 0:
        return RollingCheck(True, "insufficient_history", 0)
    return RollingCheck(True, "ok", verified)


def _prev_yms(ym: str, n: int) -> List[str]:
    """ym 前 n 个月的 YYYY-MM 列表 (最新在前)."""
    y, m = ym.split("-")
    y, m = int(y), int(m)
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        out.append(f"{y:04d}-{m:02d}")
    return out


# ---------- 闸 3: check_field_type ----------

def check_field_type(net_return: Optional[float]) -> Tuple[bool, str]:
    """字段类型: |net_return| < 0.5 (十进制). 超过说明多半是年化/季度滚动误当月度."""
    if net_return is None:
        return True, "ok"
    if abs(net_return) >= 0.5:
        return False, f"out_of_range_{net_return}"
    return True, "ok"


# ---------- 闸 4: check_anti_fabrication ----------

def check_anti_fabrication(
    net_return: Optional[float],
    ym: str,
    recent_history: List[Tuple[str, float]],
    max_run: int = 3,
) -> Tuple[bool, str]:
    """连续 >=3 个相同非零浮点值 -> 拒 (幻觉 backfill 特征).

    只看紧邻 ym 往前的连续月份 (用 _prev_yms 定位, 而非 recent_history 的入参顺序) ——
    回填场景下 recent_history 常是全库最新在前, 与 ym 不相邻, 若直接按入参顺序遍历,
    对不相邻的月份第一次比对就会 mismatch/break, 闸门形同虚设 (2026-07 事故教训,
    CLAUDE.md 第六条)。

    recent_history: [(ym, net_return), ...], 不要求有序, 不含当期。
    """
    if net_return is None or net_return == 0.0:
        return True, "ok"
    hist_map = dict(recent_history)
    run = 1
    y, m = ym.split("-")
    y, m = int(y), int(m)
    while run < max_run:
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        v = hist_map.get(f"{y:04d}-{m:02d}")
        if v is None or abs(v - net_return) >= 1e-9:
            break
        run += 1
    if run >= max_run:
        return False, f"identical_run_{run}_value_{net_return}"
    return True, "ok"
