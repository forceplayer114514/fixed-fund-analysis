"""consistency_check A-group (1/2/3/4) tests."""
from __future__ import annotations

import json
from pathlib import Path

from lib.consistency import consistency_check

FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _to_dict(recs):
    return {d: r for d, r in recs}


def test_agroup_all_pass(db_conn):
    f = _load("pdf_multifield.json")
    # register a dummy fund row so fund_id exists (B-group queries need it)
    db_conn.execute(
        "INSERT INTO funds(fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES(?, ?, ?, ?, ?)",
        (f["fund_id"], f["fund_id"], "http://x", "pdf", "test"),
    )
    db_conn.commit()
    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn,
        gross_records=f["gross"], benchmark_records=f["benchmark"],
        excess_records=f["excess_net"], growth_records=f["growth"],
        income_records=f["income"],
    )
    assert ok, f"expected pass, block={block}"
    assert block == []


def test_check1_net_excess_mismatch_blocks(db_conn):
    f = _load("pdf_multifield.json")
    f["excess_net"][0][1] = 0.0099  # corrupt: net-benchmark=0.0030 != 0.0099
    db_conn.execute(
        "INSERT INTO funds(fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES(?, ?, ?, ?, ?)",
        (f["fund_id"], f["fund_id"], "http://x", "pdf", "test"),
    )
    db_conn.commit()
    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn,
        gross_records=f["gross"], benchmark_records=f["benchmark"],
        excess_records=f["excess_net"],
    )
    assert not ok
    assert any("Check 1" in e or "净超额" in e for e in block)


def test_check3_net_not_less_than_gross_blocks(db_conn):
    f = _load("pdf_multifield.json")
    f["net"][0][1] = 0.0099  # net > gross(0.0060) violates net < gross
    db_conn.execute(
        "INSERT INTO funds(fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES(?, ?, ?, ?, ?)",
        (f["fund_id"], f["fund_id"], "http://x", "pdf", "test"),
    )
    db_conn.commit()
    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn, gross_records=f["gross"],
    )
    assert not ok
    assert any("Check 3" in e or "net < gross" in e for e in block)


def test_check4_total_return_decomposition_blocks(db_conn):
    f = _load("pdf_multifield.json")
    f["growth"][0][1] = 0.0090  # growth+income=0.0110 != net=0.0050
    db_conn.execute(
        "INSERT INTO funds(fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES(?, ?, ?, ?, ?)",
        (f["fund_id"], f["fund_id"], "http://x", "pdf", "test"),
    )
    db_conn.commit()
    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn,
        growth_records=f["growth"], income_records=f["income"],
    )
    assert not ok
    assert any("Check 4" in e or "Total Return" in e for e in block)


def test_agroup_fields_missing_skips_not_fails(db_conn):
    f = _load("pdf_multifield.json")
    db_conn.execute(
        "INSERT INTO funds(fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES(?, ?, ?, ?, ?)",
        (f["fund_id"], f["fund_id"], "http://x", "pdf", "test"),
    )
    db_conn.commit()
    # only net provided, no gross/benchmark/excess/growth/income -> A-group skip
    ok, block, warn = consistency_check(f["fund_id"], f["net"], db_conn)
    assert ok
    assert block == []


def test_fixture_d_misaligned_blocks_on_compound_and_check6(db_conn):
    """复现 bug：AusBond 当 Institutional，compound + Check 6 拦截。"""
    f = _load("field_misaligned.json")
    # 先入库 sibling assisted（DB 兄弟，同 family）
    sib = f["sibling_assisted"]
    db_conn.execute(
        "INSERT INTO funds(fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES(?, ?, ?, ?, ?)",
        (sib["fund_id"], sib["fund_id"], "http://x", "html", "test"),
    )
    for d, r in sib["rows"]:
        db_conn.execute(
            "INSERT INTO monthly_returns(fund_id, date, net_return, nav) "
            "VALUES(?, ?, ?, 1.0)",
            (sib["fund_id"], d, r),
        )
    # 入库非同 family 外部基金（供 Check 7 相关嫌疑）
    ext = f["sibling_external"]
    db_conn.execute(
        "INSERT INTO funds(fund_id, fund_name, confirmed_url, fetch_method, url_type) "
        "VALUES(?, ?, ?, ?, ?)",
        (ext["fund_id"], ext["fund_id"], "http://x", "html", "test"),
    )
    for d, r in ext["rows"]:
        db_conn.execute(
            "INSERT INTO monthly_returns(fund_id, date, net_return, nav) "
            "VALUES(?, ?, ?, 1.0)",
            (ext["fund_id"], d, r),
        )
    db_conn.commit()

    ok, block, warn = consistency_check(
        f["fund_id"], f["net"], db_conn,
        shareclass_prefix=f["shareclass_prefix"],
        rolling=f["rolling"],
    )
    assert not ok, f"应 block，实际 block={block}"
    # Check 6：份额类月度差值超阈值
    assert any("Check 6" in e for e in block), f"Check 6 缺失，block={block}"
    # 复利验证：AusBond 24mo 复利 vs inception rolling 9.05%
    assert any("复利验证失败" in e for e in block), f"复利验证缺失，block={block}"
    # Check 7：高相关 -> warn（非 block）
    assert any("Check 7" in e for e in warn), f"Check 7 缺失，warn={warn}"
