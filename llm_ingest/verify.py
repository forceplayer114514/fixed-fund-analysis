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
ROLLING_1MO_SELFCHECK_TOL = 0.001  # 十进制 0.1 个百分点 (rolling["1mo"] 应等于 net_return)


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


# ---------- 独立闸: check_fund_name_token (纠名用, 不进闸1/闸2) ----------

def check_fund_name_token(fund_name_text: Optional[str], doc_text: str) -> QuoteCheck:
    """fund_name_text (若非空) 必须是 doc_text 的子串 (归一化后), 防幻觉.

    与 check_quote_tokens 不同: 不要求出现在 source_quote 里 -- 基金名通常在
    文档抬头/封面, 与数值所在的 source_quote 段落不是同一处, 不参与净值四闸
    判定 (不进 parsers.collect_text_tokens), 只用来决定是否可信到可以拿它去
    改 fund_id/fund_name (rename_fund_id)。
    """
    if not fund_name_text:
        return QuoteCheck(True, "no_fund_name_text")
    if not doc_text:
        return QuoteCheck(False, "empty_doc_text")
    t = _flatten(fund_name_text)
    d = _flatten(doc_text)
    if not t or t not in d:
        return QuoteCheck(False, f"fund_name_not_in_doc:{fund_name_text[:60]!r}")
    return QuoteCheck(True, "ok")


# ---------- 闸 5: check_fund_identity (文档属于哪支基金) ----------
#
# Spec G 10.5: 闸 1/2/3/4 检查的都是"数值是否如实抄自文档"与"数值是否自洽",
# 全都不涉及**文档身份**。历史上唯一与目标基金比对之处只跑第一份文档, 且比对
# 不通过时仅跳过自动纠名, 数据照常入库 -> 兄弟基金月报可静默入库。
#
# 判据不复用 fundmonitors._name_matches: 它的停用词表已排除 income/enhanced/
# capital/australian, "Yarra Enhanced Income Fund" 与 "Yarra Australian Income
# Fund" 去停用词后双方都只剩 {yarra}, 交集非空即放行。那个闸是为跨发行商错源
# (2026-07-18 Coolabah 事故) 设计的, 结构上分辨不了同发行商兄弟基金。
#
# 这里改为: 只剥结构性词 (fund/trust/class/wholesale...), 保留全部区分性词汇,
# 要求两侧 token 逐个都能找到近似对应。近似用 difflib 容忍用户输入拼写错误
# (真实案例: pdf_cache 存在 stake_accumlate 目录, 少一个 u), 但不容忍语义
# 不同的词 ("enhanced" 与 "australian" 比值远低于 cutoff)。

_IDENTITY_STOPWORDS = frozenset({
    "fund", "funds", "trust", "the", "of", "and", "a", "an",
    "class", "units", "unit", "au", "aud", "nz", "nzd",
    "wholesale", "retail", "ltd", "limited", "pty", "plc",
})
_IDENTITY_CUTOFF = 0.85


def _identity_tokens(name: str) -> frozenset:
    """归一化基金名 -> 只剥结构性词后的 token 集合 (区分性词汇全部保留)."""
    if not name:
        return frozenset()
    toks = re.findall(r"[a-z0-9]+", name.lower())
    return frozenset(t for t in toks if t not in _IDENTITY_STOPWORDS and len(t) >= 2)


def _tokens_correspond(a: frozenset, b: frozenset) -> bool:
    """a 的每个 token 在 b 里都有近似对应, 且反向亦然 (对称)."""
    import difflib
    for src, dst in ((a, b), (b, a)):
        dst_list = list(dst)
        for t in src:
            if t in dst:
                continue
            if not difflib.get_close_matches(t, dst_list, n=1, cutoff=_IDENTITY_CUTOFF):
                return False
    return True


def check_fund_identity(
    fund_name_text: Optional[str],
    target_fund_name: str,
) -> QuoteCheck:
    """闸 5: 文档抬头的基金名须与本次摄取的目标基金指向同一支基金.

    fund_name_text 为空 -> 放行 (模型没读到抬头, 无从判断, 不阻断; 与
    check_fund_name_token 现有行为一致)。
    """
    if not fund_name_text:
        return QuoteCheck(True, "no_fund_name_text")
    doc = _identity_tokens(fund_name_text)
    target = _identity_tokens(target_fund_name)
    if not doc or not target:
        return QuoteCheck(True, "identity_tokens_empty")
    if _tokens_correspond(doc, target):
        return QuoteCheck(True, f"identity_ok doc={sorted(doc)}")
    return QuoteCheck(
        False,
        f"identity_mismatch doc={fund_name_text[:60]!r} "
        f"target={target_fund_name[:60]!r}",
    )


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

    自洽性预检 (Stake 2026-07 教训): rolling_decimal["1mo"] 与 net_return 理应是
    同一个数 (同一份月报里"当月收益"重复报了两遍)。若报了 1mo 且与 net_return
    差距明显超出四舍五入误差, 说明 LLM 把整批 rolling 标签错位了一格 (常见成因:
    月报表格首列 "1 month" 单元格提取缺失, LLM 把 3mo/6mo/12mo 的真实值错标成
    1mo/3mo/6mo)。数值本身没错(都是文档里真实存在的数), 只是标签错位, 但拿
    错位数据去跟 net_return 复利比对必然报假 mismatch。此时整批 rolling 弃用,
    不参与闸2 (net_return 已由 quote/field_type/antifab 三闸独立把关, 不受影响)。
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

    one_mo = rolling_decimal.get("1mo") if isinstance(rolling_decimal, dict) else None
    if one_mo is not None and abs(one_mo - net_return) > ROLLING_1MO_SELFCHECK_TOL:
        return RollingCheck(
            True,
            f"rolling_labels_shifted_discarded(1mo={one_mo:.4f}_vs_net_return={net_return:.4f})",
            0,
        )

    # 窗口间重复值预检 (Stake 2026-07 二次复现: 1mo 这次读对了, 但 3mo/6mo
    # 报出完全相同的十进制值, 而 code 复算的 implied_3mo 跟这两个数都对不上 --
    # 不同时长复利结果理论上不可能位位相同, 这是同一份损坏/缺失单元格数据被
    # 分摊到多个窗口标签下的特征信号, 而非"数值本身编不出来"的真错误。跟 1mo
    # 自洽预检同源但机制不同 (那个查 1mo vs net_return 这一个已知锚点; 这个查
    # 3/6/12mo 互相之间), 两者互补, 缺一都会漏判。重复值出现的窗口整批弃用,
    # 不参与下面复利比对; 真正各不相同的窗口正常校验, 不放松整体闸门。
    dup_groups: List[List[int]] = []
    for w, v in reported.items():
        placed = False
        for grp in dup_groups:
            if abs(v - reported[grp[0]]) < ROLLING_1MO_SELFCHECK_TOL:
                grp.append(w)
                placed = True
                break
        if not placed:
            dup_groups.append([w])
    duplicated_windows = {w for grp in dup_groups if len(grp) >= 2 for w in grp}
    reliable = {w: v for w, v in reported.items() if w not in duplicated_windows}

    verified = 0
    for w, roll_dec in reliable.items():
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
    if duplicated_windows and not reliable:
        return RollingCheck(
            True,
            f"rolling_windows_duplicated_discarded({sorted(duplicated_windows)})",
            0,
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
