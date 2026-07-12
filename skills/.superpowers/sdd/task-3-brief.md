# Task 3: lib/ingest.py 全自动流水线 + CLI + 测试

**阶段 2**（优化计划阶段 2）。依赖 Task 1（`extract_pdf_links_from_archive`）+ Task 2（`download_and_extract_parallel`、`gate_check`）。

## 目标
agent 只做 MCP 抓取（JS 渲染归档页必须 MCP），其余程序化：
```
agent: stealthy_fetch 归档页 -> 存 /tmp/<fund>_archive.md
agent: python3 -m lib.ingest add --fund-id X --name Y --archive-html <path> --confirmed-url <url> --verified-at YYYY-MM-DD
ingest.py 自动: 解析归档页 -> 并发下载+提取 PDF -> gate_check -> 入库/报错
```

## Files
- Create: `lib/ingest.py`
- Test: `tests/test_ingest.py`（Create）

## Interfaces
- Consumes:
  - `lib.extract`: `extract_pdf_links_from_archive`、`download_and_extract_parallel`、`gate_check`、`get_last_day_of_month`
  - `lib.db`: `get_connection`、`ensure_tables`、`create_fund`、`upsert_monthly_return`、`get_monthly_returns`、`get_fund`
- Produces:
  - `add_fund(fund_id, name, archive_html_path, *, confirmed_url, apir=None, url_type='archive_page', fetch_method='pdf', verified_at=None, db_path=None, max_workers=None) -> dict`
  - CLI: `python3 -m lib.ingest add --fund-id --name --archive-html --confirmed-url [--apir --verified-at --max-workers]`

## 数据完整性硬约束
1. **gate_check 是硬 gate**：不通过则**不入库**（不调 create_fund/upsert），返回 errors，CLI 退出码 1
2. **commentary_truth 字段**：入库时 `commentary_truth=net_return`（Commentary 正文值，供 webapp 复核）
3. **confirmed_url 必传**（funds.confirmed_url NOT NULL），为归档页 URL
4. **序列起点=第一份真实研报日期**：不反推捏造；归档页无的月份不补
5. **APIR 可选**：Stake/MXT 无标准 APIR，apir=None
6. **36 月不足提示**：入库成功但月数 < 36 时报告 `short_history_warning=True`（不阻止入库，由 webapp 标记）

## Steps

- [ ] **Step 1: 创建 `tests/test_ingest.py`**

测试用 `db_path` 指向 `tmp_path` 临时 DB（`add_fund` 自管理 conn 并 close，测试用新 conn 验证，避免操作已关闭连接）。

```python
"""lib/ingest.py 全自动流水线测试。

mock download_and_extract_parallel（不触网），用 db_path 指向 tmp_path 临时 DB
（add_fund 自管理 conn 并 close，测试用新 conn 验证）。
"""
from __future__ import annotations

import pytest

from lib.ingest import add_fund
from lib.db import ensure_tables, get_connection, get_monthly_returns, get_fund


def _rolling(one_mo, three=None):
    """构造 extract_perf_rolling 风格的 rolling dict。"""
    return {"1mo": one_mo, "3mo": three, "6mo": None, "12mo": None,
            "inception": None, "parse_error": False}


@pytest.mark.unit
def test_add_fund_success(monkeypatch, tmp_path):
    """全自动流水线成功：3 月数据，复利验证通过，入库。"""
    db_path = str(tmp_path / "test.db")
    archive = tmp_path / "archive.md"
    archive.write_text(
        "March 2025: https://example.com/mar-2025.pdf\n"
        "April 2025: https://example.com/apr-2025.pdf\n"
        "May 2025: https://example.com/may-2025.pdf\n"
    )

    def fake_parallel(links, dest_dir, max_workers=None):
        return [
            ("2025-03", -0.0051, _rolling(-0.0051)),
            ("2025-04", 0.0068, _rolling(0.0068)),
            # 3mo 复利 = (1-0.0051)(1+0.0068)(1+0.0066)-1 ≈ 0.00827
            ("2025-05", 0.0066, _rolling(0.0066, three=0.0083)),
        ]

    monkeypatch.setattr("lib.ingest.download_and_extract_parallel", fake_parallel)

    result = add_fund(
        "test_fund", "Test Fund", str(archive),
        confirmed_url="https://example.com/archive",
        verified_at="2026-07-12", db_path=db_path,
    )
    assert result["gate_pass"] is True
    assert result["months"] == 3
    assert result["start"] == "2025-03-31"
    assert result["end"] == "2025-05-31"
    assert result["short_history_warning"] is True  # 3 < 36

    # add_fund 已 close 它的 conn，用新 conn 验证
    conn = get_connection(db_path)
    try:
        rows = get_monthly_returns(conn, "test_fund")
        assert len(rows) == 3
        assert rows[0]["net_return"] == -0.0051
        assert rows[0]["commentary_truth"] == -0.0051  # commentary_truth = net_return
        fund = get_fund(conn, "test_fund")
        assert fund["confirmed_url"] == "https://example.com/archive"
        assert fund["verified_at"] == "2026-07-12"
    finally:
        conn.close()


@pytest.mark.unit
def test_add_fund_gate_fail_not_ingested(monkeypatch, tmp_path):
    """gate 失败（缺口）不入库。"""
    db_path = str(tmp_path / "test2.db")
    archive = tmp_path / "archive.md"
    archive.write_text(
        "March 2025: https://example.com/mar.pdf\n"
        "May 2025: https://example.com/may.pdf\n"  # 缺 04
    )

    def fake_parallel(links, dest_dir, max_workers=None):
        return [
            ("2025-03", 0.005, _rolling(0.005)),
            ("2025-05", 0.005, _rolling(0.005)),
        ]

    monkeypatch.setattr("lib.ingest.download_and_extract_parallel", fake_parallel)

    result = add_fund(
        "test_fund2", "Test Fund 2", str(archive),
        confirmed_url="https://example.com/archive", db_path=db_path,
    )
    assert result["gate_pass"] is False
    assert any("缺口" in e for e in result["errors"])
    # 未入库（gate fail 时 add_fund 未建表，验证前 ensure_tables）
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)
        assert get_monthly_returns(conn, "test_fund2") == []
        assert get_fund(conn, "test_fund2") is None
    finally:
        conn.close()


@pytest.mark.unit
def test_add_fund_no_pdf_links(monkeypatch, tmp_path):
    """归档页无 PDF 链接 -> gate_fail，不入库。"""
    db_path = str(tmp_path / "test3.db")
    archive = tmp_path / "archive.md"
    archive.write_text("No links here.")

    monkeypatch.setattr(
        "lib.ingest.download_and_extract_parallel",
        lambda links, dest_dir, max_workers=None: [],
    )

    result = add_fund(
        "test_fund3", "Test Fund 3", str(archive),
        confirmed_url="https://example.com/archive", db_path=db_path,
    )
    assert result["gate_pass"] is False
    assert result["months"] == 0


@pytest.mark.unit
def test_add_fund_extraction_failure_isolation(monkeypatch, tmp_path):
    """单 PDF 提取失败（commentary=None）被排除，导致缺口 -> gate_fail。"""
    db_path = str(tmp_path / "test4.db")
    archive = tmp_path / "archive.md"
    archive.write_text(
        "March 2025: https://example.com/mar.pdf\n"
        "April 2025: https://example.com/apr.pdf\n"
        "May 2025: https://example.com/may.pdf\n"
    )

    def fake_parallel(links, dest_dir, max_workers=None):
        return [
            ("2025-03", 0.005, _rolling(0.005)),
            ("2025-04", None, {"1mo": None, "3mo": None, "6mo": None,
                                "12mo": None, "inception": None, "parse_error": True}),
            ("2025-05", 0.005, _rolling(0.005)),
        ]

    monkeypatch.setattr("lib.ingest.download_and_extract_parallel", fake_parallel)

    result = add_fund(
        "test_fund4", "Test Fund 4", str(archive),
        confirmed_url="https://example.com/archive", db_path=db_path,
    )
    # 04 月提取失败被排除 -> records 只有 03,05 -> 缺口 04
    assert result["gate_pass"] is False
    assert "2025-04" in result.get("failed_months", [])
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)  # gate fail 时 add_fund 未建表
        assert get_fund(conn, "test_fund4") is None
    finally:
        conn.close()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /Users/chong/Desktop/fixed_fund_analysis/skills && python3 -m pytest tests/test_ingest.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'lib.ingest'`）

- [ ] **Step 3: 创建 `lib/ingest.py`**

```python
"""全自动入库流水线：归档页 -> 并发下载+提取 PDF -> gate_check -> 入库。

agent 只做 MCP 抓取（JS 渲染归档页用 stealthy_fetch），存 markdown 文件后
调本模块的 add_fund / CLI 完成剩余全流程。本模块不 import webapp 代码，
只通过共享 SQLite 与 webapp 联系。

数据完整性：gate_check 是硬 gate，不通过不入库；commentary_truth=net_return
（Commentary 正文值供 webapp 复核）；序列起点=第一份真实研报日期，不反推捏造。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from lib.db import (
    create_fund,
    ensure_tables,
    get_connection,
    upsert_monthly_return,
)
from lib.extract import (
    download_and_extract_parallel,
    extract_pdf_links_from_archive,
    gate_check,
    get_last_day_of_month,
)


def _ym_to_month_end(ym: str) -> Optional[str]:
    """'2025-03' -> '2025-03-31'（月末日期）。失败返回 None。"""
    parts = ym.split("-")
    if len(parts) != 2:
        return None
    try:
        year, month = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (1 <= month <= 12):
        return None
    return get_last_day_of_month(year, month).strftime("%Y-%m-%d")


def add_fund(
    fund_id: str,
    name: str,
    archive_html_path: str,
    *,
    confirmed_url: str,
    apir: Optional[str] = None,
    url_type: str = "archive_page",
    fetch_method: str = "pdf",
    verified_at: Optional[str] = None,
    db_path: Optional[str] = None,
    max_workers: Optional[int] = None,
) -> dict:
    """全自动流水线。

    Returns:
        {'months','start','end','gaps','gate_pass','errors',
         'failed_months','short_history_warning'}
    gate_pass=False 时未入库，errors 列出问题。
    """
    # 1. 读归档页
    with open(archive_html_path, "r", encoding="utf-8") as f:
        markdown = f.read()

    # 2. 提 PDF 链接
    links = extract_pdf_links_from_archive(markdown)
    if not links:
        return {"months": 0, "start": None, "end": None, "gaps": [],
                "gate_pass": False, "errors": ["归档页无 PDF 链接"],
                "failed_months": [], "short_history_warning": True}

    # 3. 并发下载+提取
    dest_dir = os.path.join(
        os.path.dirname(os.path.abspath(archive_html_path)), f"{fund_id}_pdfs"
    )
    results = download_and_extract_parallel(links, dest_dir, max_workers=max_workers)

    # 4. 组装 records + rolling_per_month（排除提取失败项）
    records: list[tuple[str, float]] = []
    rolling_per_month: dict = {}
    failed_months: list[str] = []
    for ym, commentary, rolling in results:
        if commentary is None:
            failed_months.append(ym)
            continue
        date = _ym_to_month_end(ym)
        if date is None:
            failed_months.append(ym)
            continue
        records.append((date, commentary))
        rolling_per_month[ym] = rolling

    # 5. gate_check（硬 gate）
    pass_ok, errors = gate_check(records, rolling_per_month)
    if not pass_ok:
        return {"months": len(records), "start": None, "end": None,
                "gaps": [], "gate_pass": False, "errors": errors,
                "failed_months": failed_months,
                "short_history_warning": len(records) < 36}

    # 6. 入库（gate 通过后才碰 DB）
    conn = get_connection(db_path)
    try:
        ensure_tables(conn)
        create_fund(
            conn, fund_id=fund_id, fund_name=name,
            confirmed_url=confirmed_url, fetch_method=fetch_method,
            url_type=url_type, apir_code=apir, verified_at=verified_at,
        )
        for date, net_return in records:
            upsert_monthly_return(
                conn, fund_id=fund_id, date=date,
                net_return=net_return, commentary_truth=net_return,
            )
    finally:
        conn.close()

    # 7. 报告
    records_sorted = sorted(records, key=lambda x: x[0])
    return {
        "months": len(records_sorted),
        "start": records_sorted[0][0] if records_sorted else None,
        "end": records_sorted[-1][0] if records_sorted else None,
        "gaps": [],
        "gate_pass": True,
        "errors": [],
        "failed_months": failed_months,
        "short_history_warning": len(records_sorted) < 36,
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(description="skills 全自动入库流水线")
    sub = parser.add_subparsers(dest="command", required=True)
    add_p = sub.add_parser("add", help="新增基金入库")
    add_p.add_argument("--fund-id", required=True)
    add_p.add_argument("--name", required=True)
    add_p.add_argument("--archive-html", required=True, help="归档页 markdown 路径")
    add_p.add_argument("--confirmed-url", required=True, help="归档页 URL（数据源）")
    add_p.add_argument("--apir", default=None)
    add_p.add_argument("--verified-at", default=None)
    add_p.add_argument("--max-workers", type=int, default=None)
    args = parser.parse_args()

    if args.command == "add":
        result = add_fund(
            args.fund_id, args.name, args.archive_html,
            confirmed_url=args.confirmed_url, apir=args.apir,
            verified_at=args.verified_at, max_workers=args.max_workers,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["gate_pass"] else 1
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd /Users/chong/Desktop/fixed_fund_analysis/skills && python3 -m pytest tests/test_ingest.py -v`
Expected: 4 passed

- [ ] **Step 5: 运行全部测试确认无回归**

Run: `cd /Users/chong/Desktop/fixed_fund_analysis/skills && python3 -m pytest tests/ -v`
Expected: 全部 PASS（48 原有 + 4 新增 = 52）

- [ ] **Step 6: 验证 CLI 可用**

Run: `cd /Users/chong/Desktop/fixed_fund_analysis/skills && python3 -m lib.ingest add --help`
Expected: 正常显示帮助（--fund-id --name --archive-html --confirmed-url 等）

- [ ] **Step 7: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add skills/lib/ingest.py skills/tests/test_ingest.py
git commit -m "feat(skills): add ingest.py full-auto pipeline + CLI

新增 lib/ingest.py（Task 3，阶段 2）:
- add_fund: 归档页 -> 并发下载+提取 -> gate_check -> 入库 全自动
- CLI: python3 -m lib.ingest add --fund-id --name --archive-html --confirmed-url
- gate_check 硬 gate: 不通过不入库, 退出码 1
- commentary_truth=net_return（Commentary 正文值供 webapp 复核）
- short_history_warning: 月数<36 提示（不阻止入库）

agent 只做 MCP 抓取归档页, 其余程序化。预期单基金入库 27'->3-5'。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

## 验收标准
1. `tests/test_ingest.py` 4 用例全部 PASS
2. `tests/` 全部 PASS 无回归（52）
3. `python3 -m lib.ingest add --help` 正常显示
4. gate_fail 时不入库（funds/monthly_returns 表无该 fund_id）
5. 成功时 commentary_truth == net_return
6. 已提交

## 完成后
在 `skills/.superpowers/sdd/task-3-report.md` 写报告：新增文件、测试用例数、pytest 摘要、CLI 验证、commit hash。
