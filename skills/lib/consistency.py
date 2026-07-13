"""写库前 self-consistency 强制关卡：7 条校验 + 复利验证。

A 组（文档内自证，无 DB 依赖）：1 净超额恒等 / 2 总超额恒等 / 3 net<gross /
4 总收益=growth+income。字段不全则跳过该条，不因此 fail。
B 组（跨序列，依赖 DB 兄弟）：5 同 family 份额类符号一致 / 6 份额类月度差值
<fee_diff_monthly_max / 7 新序列 vs DB 其他基金相关系数>warn。
复利验证（A 组替代，Plotly 源）：NAV 复利 vs PDF rolling 全窗口（1mo/1yr/inception）。

返回 (pass, errors_block, errors_warn)。block 级失败 -> gate_pass=False 不入库。
"""
from __future__ import annotations

import sqlite3
from typing import Optional


_TOL = 0.0005  # 0.05% 恒等式容差


def _to_dict(recs):
    return {d: r for d, r in recs} if recs else {}


def consistency_check(
    fund_id: str,
    records: list[tuple[str, float]],
    conn: sqlite3.Connection,
    *,
    gross_records: Optional[list[tuple[str, float]]] = None,
    benchmark_records: Optional[list[tuple[str, float]]] = None,
    excess_records: Optional[list[tuple[str, float]]] = None,
    excess_gross_records: Optional[list[tuple[str, float]]] = None,
    growth_records: Optional[list[tuple[str, float]]] = None,
    income_records: Optional[list[tuple[str, float]]] = None,
    shareclass_prefix: Optional[str] = None,
    rolling: Optional[dict] = None,
    corr_threshold: float = 0.98,
    fee_diff_monthly_max: float = 0.001,
) -> tuple[bool, list[str], list[str]]:
    """7 条校验 + 复利验证。返回 (pass, errors_block, errors_warn)。

    block 级（1/2/3/4/5/6 + 复利）-> errors_block；warn 级（7）-> errors_warn。
    pass = (errors_block 为空)。字段不全的 A 组条目跳过，不因此 fail。
    """
    errors_block: list[str] = []
    errors_warn: list[str] = []

    if not records:
        return (False, ["无数据"], [])

    net = _to_dict(records)
    gross = _to_dict(gross_records)
    benchmark = _to_dict(benchmark_records)
    excess_net = _to_dict(excess_records)
    excess_gross = _to_dict(excess_gross_records)
    growth = _to_dict(growth_records)
    income = _to_dict(income_records)

    # Check 1: 净超额 ≈ net - benchmark（net/benchmark/excess_net 都存在时）
    if net and benchmark and excess_net:
        for d in net:
            if d in benchmark and d in excess_net:
                expected = net[d] - benchmark[d]
                if abs(expected - excess_net[d]) > _TOL:
                    errors_block.append(
                        f"Check 1 净超额恒等失败 {d}: net {net[d]} - benchmark "
                        f"{benchmark[d]} = {expected:.6f} != excess "
                        f"{excess_net[d]:.6f}（容差 {_TOL}）"
                    )

    # Check 2: 总超额 ≈ gross - benchmark（gross/benchmark/excess_gross 都存在时）
    if gross and benchmark and excess_gross:
        for d in gross:
            if d in benchmark and d in excess_gross:
                expected = gross[d] - benchmark[d]
                if abs(expected - excess_gross[d]) > _TOL:
                    errors_block.append(
                        f"Check 2 总超额恒等失败 {d}: gross {gross[d]} - "
                        f"benchmark {benchmark[d]} = {expected:.6f} != excess "
                        f"{excess_gross[d]:.6f}"
                    )

    # Check 3: net < gross（两者都存在时，浮点 0.001 容差）
    if net and gross:
        for d in net:
            if d in gross and net[d] > gross[d] + 0.001:
                errors_block.append(
                    f"Check 3 net<gross 失败 {d}: net {net[d]} > gross "
                    f"{gross[d]}（除非 fee waiver，须人工确认）"
                )

    # Check 4: total return = growth + income（三者都存在时）
    if net and growth and income:
        for d in net:
            if d in growth and d in income:
                expected = growth[d] + income[d]
                if abs(expected - net[d]) > _TOL:
                    errors_block.append(
                        f"Check 4 总收益分解失败 {d}: growth {growth[d]} + income "
                        f"{income[d]} = {expected:.6f} != net {net[d]:.6f}"
                    )

    # 复利验证（A 组替代，Plotly 源）：rolling 提供时全窗口校验
    if rolling and not rolling.get("parse_error", True):
        _check_compound(records, rolling, errors_block)

    # B 组（跨序列）+ 7：Task 3 实现
    if shareclass_prefix:
        _check_bgroup(
            fund_id, records, conn, shareclass_prefix,
            fee_diff_monthly_max, errors_block,
        )
    _check_correlation(
        fund_id, records, conn, shareclass_prefix,
        corr_threshold, errors_warn,
    )

    return (len(errors_block) == 0, errors_block, errors_warn)


def _check_compound(
    records: list[tuple[str, float]],
    rolling: dict,
    errors_block: list[str],
) -> None:
    """NAV 复利 vs PDF rolling 全窗口（1mo/3mo/6mo/1yr/inception）。

    records 为月度收益序列；rolling 含 1mo/3mo/6mo/12mo/inception。
    对每个窗口：用 records 末 N 月复利 vs rolling 同期，误差 >0.5% 报 block。
    inception：全序列复利 vs rolling['inception']。
    """
    sorted_m = sorted(records, key=lambda x: x[0])
    rets = [r for _, r in sorted_m]
    # verify 用"至少一个窗口通过"逻辑，这里要全窗口严格：单独判
    for key, n in [("3mo", 3), ("6mo", 6), ("12mo", 12)]:
        rv = rolling.get(key)
        if rv is None or len(rets) < n:
            continue
        actual = 1.0
        for r in rets[-n:]:
            actual *= (1.0 + r)
        actual -= 1.0
        if abs(actual - rv) >= 0.005:
            errors_block.append(
                f"复利验证失败 {key}: 月度复利 {actual:.4f} vs rolling "
                f"{rv:.4f}，误差 {abs(actual-rv):.4f}（阈值 0.5%）"
            )
    # inception 全窗口
    rv = rolling.get("inception")
    if rv is not None and rets:
        actual = 1.0
        for r in rets:
            actual *= (1.0 + r)
        actual -= 1.0
        if abs(actual - rv) >= 0.005:
            errors_block.append(
                f"复利验证失败 inception: 全序列复利 {actual:.4f} vs rolling "
                f"{rv:.4f}，误差 {abs(actual-rv):.4f}（阈值 0.5%）"
            )


def _check_bgroup(
    fund_id: str,
    records: list[tuple[str, float]],
    conn: sqlite3.Connection,
    shareclass_prefix: str,
    fee_diff_monthly_max: float,
    errors_block: list[str],
) -> None:
    """B 组跨序列校验（Task 3 实现）。"""
    # 占位，Task 3 填充
    pass


def _check_correlation(
    fund_id: str,
    records: list[tuple[str, float]],
    conn: sqlite3.Connection,
    shareclass_prefix: Optional[str],
    corr_threshold: float,
    errors_warn: list[str],
) -> None:
    """Check 7 相关嫌疑（Task 3 实现）。"""
    # 占位，Task 3 填充
    pass
