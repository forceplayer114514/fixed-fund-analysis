"""复利验证全窗口 + 第一次入库 DB 空场景（fixture E）。"""
from __future__ import annotations

import json
from pathlib import Path

from lib.consistency import consistency_check, _check_compound

FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_fixture_e_first_shareclass_compound_blocks(db_conn):
    """第一次入库 DB 空，无兄弟，复利验证独立拦截（不依赖 DB 兄弟）。"""
    f = _load("first_shareclass.json")
    # DB 空：无 sibling、无其他基金
    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn,
        shareclass_prefix=f["shareclass_prefix"],
        rolling=f["rolling"],
    )
    assert not ok, f"应 block，block={block}"
    # inception 复利失败（AusBond 12mo 复利 ~0.05 vs inception 0.0905）
    assert any("inception" in e and "复利" in e for e in block), \
        f"inception 复利缺失，block={block}"
    # 无兄弟 -> Check 5/6 不触发
    assert not any("Check 5" in e for e in block)
    assert not any("Check 6" in e for e in block)


def test_compound_passes_when_consistent(db_conn):
    """net 复利与 rolling 一致 -> compound 通过。"""
    f = _load("first_shareclass.json")
    # 构造 net 使 inception 复利 = rolling.inception 0.0905
    # 12 月均匀 r 使 (1+r)^12 - 1 = 0.0905 -> r = 0.0905^(1/12)... 用近似
    # 简单：直接用 rolling 12mo=0.0507 对应 12 月序列
    import math
    r12 = (1 + 0.0507) ** (1 / 12) - 1
    net = [[d, r12] for d, _ in f["net"]]
    rolling = {"1mo": r12, "3mo": (1 + r12) ** 3 - 1,
               "6mo": (1 + r12) ** 6 - 1, "12mo": 0.0507,
               "inception": 0.0507, "parse_error": False}
    ok, block, warn = consistency_check(
        f["fund_id"], net, db_conn, rolling=rolling,
    )
    assert ok, f"应 pass，block={block}"


def test_compound_missing_rolling_skips(db_conn):
    """rolling 缺失或 parse_error -> 复利验证跳过，不 fail。"""
    f = _load("first_shareclass.json")
    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn, rolling=None,
    )
    assert ok
    assert not any("复利" in e for e in block)
