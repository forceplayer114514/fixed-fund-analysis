"""两道闸.

闸 1 引用硬校验 (挡编数字):
  归一化压平空白后, quote 是 pdf_text 的子串, 且 value 能从 quote 里字面解析.

闸 2 数学交叉 (挡字段类型错):
  月度值复利对 3/6/12mo 滚动窗口, 绝对误差 < VERIFY_TOL.
  逻辑抄自 skills/lib/extract.py:1091 verify_monthly_vs_rolling.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

VERIFY_TOL = 0.005  # 0.5%,与 skills/lib/extract.py 一致
_WS_RE = re.compile(r"\s+")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _flatten(s: str) -> str:
    """压平所有空白到单空格, 去首尾, 用于子串匹配."""
    return _WS_RE.sub(" ", s).strip()


def _parse_pct_from_quote(quote: str, target_pct: float, tol: float = 0.001) -> bool:
    """target_pct 是否能从 quote 里字面解析出来 (百分比十进制).

    quote 里的数字都是"百分数" (0.65 = 0.65%). 逐个数字 abs(n - target_pct) < tol 即匹配.

    tol=0.001 = 0.001 百分点 (即 0.00001 十进制); PDF 精度是两位小数, 该 tol 允许最后一位偏差.
    """
    for m in _NUM_RE.finditer(quote):
        try:
            n = float(m.group())
        except ValueError:
            continue
        if abs(n - target_pct) < tol:
            return True
    return False


@dataclass(frozen=True)
class QuoteCheck:
    passed: bool
    reason: str


def check_quote(quote: str, pdf_text: str, net_return: Optional[float]) -> QuoteCheck:
    """闸 1.

    - quote 空 -> 挡.
    - quote (压平) 不是 pdf_text (压平) 子串 -> 挡.
    - net_return != None 但 quote 里没有对应数字 -> 挡.

    net_return 单位: 十进制 (0.0065). quote 里数字单位: 百分数 (0.65).
    比对前 * 100.
    """
    if not quote:
        return QuoteCheck(False, "empty_quote")
    q = _flatten(quote)
    p = _flatten(pdf_text)
    if q not in p:
        return QuoteCheck(False, "quote_not_in_pdf")
    if net_return is not None:
        target_pct = net_return * 100.0
        if not _parse_pct_from_quote(q, target_pct):
            return QuoteCheck(False, f"value_{target_pct:.4f}_not_in_quote")
    return QuoteCheck(True, "ok")


@dataclass(frozen=True)
class RollingCheck:
    passed: bool
    reason: str
    windows_verified: int  # 0/1/2/3, 有几个窗口交叉过


def check_rolling(
    net_return: Optional[float],
    ym: str,
    monthly_history: Dict[str, float],
    rolling: Dict[str, Optional[float]],
    tol: float = VERIFY_TOL,
) -> RollingCheck:
    """闸 2.

    net_return: 当月十进制值.
    ym: "YYYY-MM".
    monthly_history: 该基金已入库的 ym -> net_return dict (前 11 个月的十进制值).
    rolling: 模型返回的 1mo/3mo/6mo/12mo, 单位百分数.

    对每个窗口 W in {3,6,12}: 若 rolling[Wmo] 存在且 monthly_history 覆盖前 W-1 个月,
    则 (1 + m_ym) * prod(1 + m_prev) - 1  vs  rolling[Wmo]/100, abs diff < tol.

    reason:
      no_rolling_reported - 模型没给任何 3/6/12mo
      insufficient_history - 有 rolling 但历史不够
      mismatch_XXmo - 某窗口对不上
      ok - 至少一个窗口 pass
    """
    if net_return is None:
        return RollingCheck(True, "no_net_return", 0)  # not_found=true 时不校验

    reported = {
        w: rolling.get(f"{w}mo")
        for w in (3, 6, 12)
        if rolling.get(f"{w}mo") is not None
    }
    if not reported:
        return RollingCheck(True, "no_rolling_reported", 0)  # 无 rolling 可交叉 -> pass, verify_windows=0

    verified = 0
    for w, roll_pct in reported.items():
        prevs = _prev_yms(ym, w - 1)
        if not all(pm in monthly_history for pm in prevs):
            continue
        product = 1.0 + net_return
        for pm in prevs:
            product *= 1.0 + monthly_history[pm]
        implied_pct = (product - 1.0) * 100.0
        if abs(implied_pct - roll_pct) < tol * 100.0:  # tol 十进制 -> 百分数
            verified += 1
        else:
            # 首个失配即挡 (避免"某窗口对得上另一个对不上"混过)
            return RollingCheck(False, f"mismatch_{w}mo(implied={implied_pct:.4f} vs {roll_pct:.4f})", verified)
    if verified == 0:
        return RollingCheck(True, "insufficient_history", 0)  # 有 rolling 但历史不够 -> pass, 记 0
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


# ANTI-FABRICATION 硬 gate, 抄自 skills/lib/extract.py:1135.
def check_anti_fabrication(
    net_return: Optional[float],
    ym: str,
    recent_history: List[Tuple[str, float]],
    max_run: int = 3,
) -> Tuple[bool, str]:
    """连续 >=3 个相同非零浮点值 -> 拒 (幻觉 backfill 特征).

    recent_history: [(ym, net_return), ...], 时间倒序 (最新在前), 不含当期.
    """
    if net_return is None or net_return == 0.0:
        return True, "ok"
    run = 1
    for _, v in recent_history:
        if abs(v - net_return) < 1e-9:
            run += 1
            if run >= max_run:
                return False, f"identical_run_{run}_value_{net_return}"
        else:
            break
    return True, "ok"


def check_field_type(net_return: Optional[float]) -> Tuple[bool, str]:
    """字段类型: |net_return| < 0.5 (十进制). 超过说明多半是年化/季度滚动误当月度."""
    if net_return is None:
        return True, "ok"
    if abs(net_return) >= 0.5:
        return False, f"out_of_range_{net_return}"
    return True, "ok"
