#!/usr/bin/env python3
"""Task 13 端到端验证脚本 -- Grok agentic search 通路三场景.

一次性验证脚本, 跑完保留供复现 (Spec G Task 13)。

**运行前必须做 DB 隔离** (脚本本身不做, 调用方负责):

    cp data/fund_analysis.db /tmp/e2e_grok.db
    export FUND_DB_PATH=/tmp/e2e_grok.db

**绝不可在 FUND_DB_PATH 指向生产库时运行** -- E2E-1 会清空白名单字段并写入
monthly_returns, E2E-2 会往 funds 表插入新基金行。

三个场景:
  e2e1  -- L1 fundmonitors 经 Grok 重新发现 (Yarra, 有 DB 真值 FundID=1512 可对分)
  e2e2  -- L2 官网归档经 Grok + 反捏造闸 (GCI, 最重要的一条: 反捏造闸生效证明)
  e2e3  -- Tavily 通路回归 (同两个对象, 确认未破坏既有通路)

用法:
    python3 scripts/e2e_grok.py e2e1
    python3 scripts/e2e_grok.py e2e2
    python3 scripts/e2e_grok.py e2e3
    python3 scripts/e2e_grok.py all      # 依次跑三个
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

# 允许从任意 cwd 运行: 把仓库根 (本文件的上一级) 加进 sys.path, 保证
# `from llm_ingest import ...` 能解析到位于仓库根的 llm_ingest 包。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _require_db_isolation() -> str:
    """强制要求 FUND_DB_PATH 已设置且不是生产库路径, 防误跑生产库。"""
    db_path = os.environ.get("FUND_DB_PATH")
    if not db_path:
        raise SystemExit(
            "FUND_DB_PATH 未设置 -- 拒绝运行, 防止误写生产库 "
            "data/fund_analysis.db。请先 cp 一份临时副本并 export FUND_DB_PATH。"
        )
    if os.path.abspath(db_path).endswith("/data/fund_analysis.db"):
        raise SystemExit(
            f"FUND_DB_PATH={db_path} 疑似指向生产库, 拒绝运行。"
        )
    return db_path


def e2e1_yarra_grok() -> None:
    """E2E-1: L1 fundmonitors 经 Grok (Yarra Enhanced Income Fund).

    判据:
      1. Grok 独立重新发现 FundID=1512
      2. probe(..., engine="grok") 返回 status="ok", name-fuzzy 闸通过
      3. 写入月份数与区间对齐生产库的 276 行 / 2003-07 ~ 2026-06
    """
    _require_db_isolation()
    from llm_ingest import fundmonitors as fm
    from llm_ingest import store as store_mod

    print("=" * 70)
    print("E2E-1: Yarra Enhanced Income Fund -- L1 经 Grok")
    print("=" * 70)

    # 用 store.open_conn() 而非裸 sqlite3.connect: 会读 FUND_DB_PATH (resolve_db_path)
    # 并把 row_factory 设为 sqlite3.Row -- write_table_records 内部 _recompute_nav
    # 依赖 dict-like row 访问 (r["net_return"]), 裸连接会因 tuple 索引报错。
    conn = store_mod.open_conn()

    # 清白名单, 强制走搜索而非短路
    conn.execute(
        "UPDATE funds SET fundmonitors_fund_id=NULL, fundmonitors_acc_code=NULL "
        "WHERE fund_id='yarra_enhanced_income_fund'"
    )
    conn.commit()
    row = conn.execute(
        "SELECT fund_id, fundmonitors_fund_id, fundmonitors_acc_code FROM funds "
        "WHERE fund_id='yarra_enhanced_income_fund'"
    ).fetchone()
    print("白名单已清:", row)

    # 判据 1: find_fundid 独立重新发现
    got = fm.find_fundid("Yarra Enhanced Income Fund", engine="grok")
    print("FundID/AccCode:", got)
    assert got is not None, "Grok 未拿到 FundID"
    assert got[0] == 1512, f"FundID 应为 1512, 实得 {got[0]}"
    print("判据 1 通过: Grok 独立重新发现 FundID=1512")

    # 判据 2: probe(engine=grok) status=ok, name-fuzzy 闸通过
    l1 = fm.probe(
        "Yarra Enhanced Income Fund",
        fund_id="yarra_enhanced_income_fund",
        db_conn=conn,
        engine="grok",
    )
    print("probe status:", l1.get("status"))
    print("probe page_fund_name:", l1.get("page_fund_name"))
    print("probe records count:", len(l1.get("records", [])))
    print("probe url:", l1.get("url"))
    print("probe errors:", l1.get("errors"))
    assert l1.get("status") == "ok", f"probe status 应为 ok, 实得 {l1.get('status')} errors={l1.get('errors')}"
    print("判据 2 通过: probe status=ok, name-fuzzy 闸通过 (否则会返 name_mismatch)")

    # 判据 3: 写入月份数与区间对齐生产库真值 276 行 / 2003-07 ~ 2026-06
    n_written = store_mod.write_table_records(
        conn, fund_id="yarra_enhanced_income_fund",
        records=l1["records"], source_url=l1["url"],
    )
    conn.commit()
    print("写入行数 (n_written, upsert 计数):", n_written)
    cur = conn.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM monthly_returns "
        "WHERE fund_id='yarra_enhanced_income_fund'"
    )
    count, min_date, max_date = cur.fetchone()
    print(f"monthly_returns 现状: count={count}, range={min_date} ~ {max_date}")
    print("(判据 3 对照生产库真值: 276 行, 2003-07-31 ~ 2026-06-30 -- 见下方脚本外部核对)")

    conn.close()
    print("E2E-1 完成\n")


def e2e2_gci_grok() -> None:
    """E2E-2: L2 官网归档经 Grok + 反捏造闸 (GCI, 最重要的一条).

    必须先规避 L2.6 本地缓存陷阱: run_discovery 末尾在 aggregate 为空时会扫
    data/pdf_cache/{fund_id}/*.pdf 全量入库, 而 data/pdf_cache/gryphon_capital_income/
    已有 88 份 PDF。若 fund_id 撞名且 Grok 通路返空, 会静默灌入 88 个月,
    判据随即失效且失败会伪装成成功。因此本测试一律用不撞名的 fund_id
    (gci_e2e_probe, 刻意不撞 pdf_cache/gryphon_capital_income)。

    判据:
      1. archive_url 落在 gcapinvest.com
      2. evidence_log 里没有 L_local (未走本地缓存兜底)
      3. 入库的每一份 PDF 都能在抓下来的页面 <a href> 白名单里找到 (反捏造闸)
    """
    _require_db_isolation()
    from llm_ingest import discover as disc

    print("=" * 70)
    print("E2E-2: Gryphon Capital Income Trust -- L2 经 Grok + 反捏造闸")
    print("=" * 70)

    rep = disc.run_discovery(
        fund_name="Gryphon Capital Income Trust",
        issuer="Gryphon Capital Investments",
        fund_id="gci_e2e_probe",  # 刻意不撞 pdf_cache/gryphon_capital_income
        engine="grok",
    )
    archive_url = rep.archive_pointer.archive_url if rep.archive_pointer else None
    print("archive_url:", archive_url)
    print("links:", len(rep.links))
    for ym, url in rep.links:
        print("   ", ym, url)
    print("evidence_log:", json.dumps(rep.evidence_log, ensure_ascii=False, indent=2)[:2000])

    # 判据 1
    assert rep.archive_pointer and rep.archive_pointer.archive_url, "未定位到归档页"
    assert "gcapinvest.com" in rep.archive_pointer.archive_url, \
        f"归档页应在 gcapinvest.com, 实得 {rep.archive_pointer.archive_url}"
    print("判据 1 通过: archive_url 落在 gcapinvest.com")

    # 判据 2
    levels = [e.get("level") for e in rep.evidence_log]
    assert "L_local" not in levels, "走了 L2.6 本地缓存兜底, 本测试结果不可信"
    print("判据 2 通过: evidence_log 里没有 L_local")

    # 判据 3 (反捏造闸)
    html = disc._fetch(rep.archive_pointer.archive_url) or ""
    whitelist = disc._extract_href_whitelist(html, rep.archive_pointer.archive_url)
    for ym, url in rep.links:
        assert url in whitelist or url.lower() in whitelist, \
            f"{ym} 的 PDF 不在页面 href 白名单里, 疑似捏造: {url}"
    print("判据 3 通过: 每份 PDF 均可追溯到页面真实 href 链接 (反捏造闸生效)")
    print("E2E-2 全部判据通过\n")


def e2e3_tavily_regression() -> None:
    """E2E-3: Tavily 通路回归 -- 同两个对象各用 engine="tavily" 跑一次."""
    _require_db_isolation()
    assert os.environ.get("SEARCH_BACKEND", "tavily") == "tavily", \
        "必须确认默认后端已是 tavily (Task 1)"

    from llm_ingest import fundmonitors as fm
    from llm_ingest import discover as disc

    print("=" * 70)
    print("E2E-3: Tavily 通路回归 (Yarra + GCI)")
    print("=" * 70)

    got = fm.find_fundid("Yarra Enhanced Income Fund", engine="tavily")
    print("Tavily FundID:", got)
    assert got and got[0] == 1512, f"Tavily 通路回归失败: {got}"

    rep = disc.run_discovery(
        fund_name="Gryphon Capital Income Trust",
        issuer="Gryphon Capital Investments",
        fund_id="gci_e2e_probe_tavily",
        engine="tavily",
    )
    print("Tavily archive_url:",
          rep.archive_pointer.archive_url if rep.archive_pointer else None)
    print("Tavily links:", len(rep.links))
    for ym, url in rep.links:
        print("   ", ym, url)
    print("E2E-3 通过\n")


def main() -> None:
    scenarios = {
        "e2e1": e2e1_yarra_grok,
        "e2e2": e2e2_gci_grok,
        "e2e3": e2e3_tavily_regression,
    }
    if len(sys.argv) < 2 or sys.argv[1] not in {*scenarios.keys(), "all"}:
        print(__doc__)
        raise SystemExit(1)
    if sys.argv[1] == "all":
        for fn in scenarios.values():
            fn()
    else:
        scenarios[sys.argv[1]]()


if __name__ == "__main__":
    main()
