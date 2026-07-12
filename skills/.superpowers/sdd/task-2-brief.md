# Task 2: extract.py 新增并发 + 校验函数（3 个）+ 测试

**阶段 1-B**（优化计划阶段 1 后半）。依赖 Task 1 的 `extract_pdf_one`。

## Files
- Modify: `lib/extract.py`（顶层加 `import concurrent.futures`，末尾新增 3 函数，复用 `download_file`/`extract_pdf_one`/`check_gaps`）
- Test: `tests/test_extract.py`（末尾新增测试用例）

## Interfaces
- Consumes: `download_file`（已有）、`extract_pdf_one`（Task 1）、`check_gaps`（已有）
- Produces:
  - `download_and_extract_parallel(links, dest_dir, max_workers=None) -> list[tuple[str, Optional[float], dict]]`
  - `verify_monthly_vs_rolling(monthly, rolling) -> dict`
  - `gate_check(records, rolling_per_month) -> tuple[bool, list[str]]`

## 数据完整性硬约束
1. **失败隔离**：单个 PDF 下载/提取失败不中断其他 PDF，失败项返回 `(ym, None, {'parse_error':True})`
2. **复利交叉验证**：monthly 复利 vs performance 表滚动收益，绝对误差 < 0.5%，至少一个窗口通过才 pass
3. **ANTI-FABRICATION**：连续 >= 3 个相同非零浮点数视为捏造迹象（参考教训 213bdd：3 个月 0.00657 硬编码）
4. **gate_check 硬 gate**：缺口/捏造/复利失败/字段类型异常任一失败即 `pass=False`，调用方必须停止入库
5. **不捏造不插值**：rolling 缺列或 monthly 不足跳过该窗口（不补值）

## 并发设计要点
- 单一 `ThreadPoolExecutor`，`max_workers` 默认 `min(16, os.cpu_count())`（M5 满核）
- 每 worker = `download_file` + `extract_pdf_one`（IO 下载与 CPU 提取重叠，无 barrier）
- `as_completed` 收集，按 ym 排序保持时序
- 线程安全：fitz C 层释放 GIL，每 worker 独立 `fitz.open` 不共享 Document
- 不用 ProcessPool（macOS spawn 重新 import fitz 开销 > 15 个小 PDF 收益）

## Steps

- [ ] **Step 1: 在 `lib/extract.py` 顶层加 import**

在现有 `import re` 附近（顶层 import 区）加：
```python
import concurrent.futures
```
（`os` 已在顶层 import，复用）

- [ ] **Step 2: 在 `tests/test_extract.py` 末尾新增测试用例**

```python
from lib.extract import (
    download_and_extract_parallel,
    verify_monthly_vs_rolling,
    gate_check,
)


# --- 15. verify_monthly_vs_rolling ---
def test_verify_monthly_vs_rolling_pass():
    """3 月窗口复利验证通过（Stake 2026-03~05 真实数据）。"""
    # 复利 = (1-0.0026)(1+0.0061)(1+0.0105)-1 ≈ 0.01400
    monthly = [
        ("2026-03-31", -0.0026),
        ("2026-04-30", 0.0061),
        ("2026-05-31", 0.0105),
    ]
    rolling = {
        "1mo": 0.0105, "3mo": 0.0140, "6mo": None,
        "12mo": None, "inception": None, "parse_error": False,
    }
    r = verify_monthly_vs_rolling(monthly, rolling)
    assert r["3mo"]["pass"] is True
    assert r["pass"] is True


def test_verify_monthly_vs_rolling_fail():
    """构造错误序列：复利不匹配 rolling。"""
    monthly = [
        ("2026-03-31", 0.05),
        ("2026-04-30", 0.05),
        ("2026-05-31", 0.05),
    ]
    rolling = {
        "1mo": 0.05, "3mo": 0.01, "6mo": None,
        "12mo": None, "inception": None, "parse_error": False,
    }
    r = verify_monthly_vs_rolling(monthly, rolling)
    # 复利 0.1576 != rolling 0.01
    assert r["3mo"]["pass"] is False
    assert r["pass"] is False


def test_verify_monthly_vs_rolling_skip_missing_window():
    """monthly 不足 N 月或 rolling 缺列 -> 跳过该窗口。"""
    monthly = [("2026-05-31", 0.0105)]
    rolling = {
        "1mo": 0.0105, "3mo": 0.0140, "6mo": None,
        "12mo": None, "inception": None, "parse_error": False,
    }
    r = verify_monthly_vs_rolling(monthly, rolling)
    # 3mo 窗口因 monthly 不足跳过，pass=False（无窗口通过）
    assert r["3mo"]["pass"] is False
    assert r["pass"] is False


# --- 16. gate_check ---
def test_gate_check_pass():
    """完整通过流程：无缺口、无捏造、复利通过、字段正常。"""
    records = [
        ("2025-01-31", 0.005),
        ("2025-02-28", 0.006),
        ("2025-03-31", 0.004),
    ]
    # 最近 3 月复利 = 1.005*1.006*1.004-1 ≈ 0.01503，rolling 3mo=0.015 误差<0.5%
    rolling = {
        "1mo": 0.004, "3mo": 0.015, "6mo": None,
        "12mo": None, "inception": None, "parse_error": False,
    }
    pass_ok, errors = gate_check(records, {"2025-03": rolling})
    assert pass_ok is True
    assert errors == []


def test_gate_check_gap_fail():
    """缺口失败。"""
    records = [
        ("2025-01-31", 0.005),
        ("2025-03-31", 0.004),
    ]
    pass_ok, errors = gate_check(records, {})
    assert pass_ok is False
    assert any("缺口" in e for e in errors)


def test_gate_check_fabrication_fail():
    """连续 3 月相同非零值（捏造迹象，参考 213bdd）失败。"""
    records = [
        ("2025-01-31", 0.00657),
        ("2025-02-28", 0.00657),
        ("2025-03-31", 0.00657),
        ("2025-04-30", 0.005),
    ]
    pass_ok, errors = gate_check(records, {})
    assert pass_ok is False
    assert any("ANTI-FABRICATION" in e for e in errors)


def test_gate_check_field_range_fail():
    """字段异常：单月 |r| >= 0.5（50%）失败。"""
    records = [
        ("2025-01-31", 0.005),
        ("2025-02-28", 0.6),  # 60%，异常
        ("2025-03-31", 0.004),
    ]
    pass_ok, errors = gate_check(records, {})
    assert pass_ok is False
    assert any("字段异常" in e for e in errors)


def test_gate_check_rolling_parse_error_skipped():
    """rolling parse_error=True 时跳过复利验证（不因此 fail）。"""
    records = [
        ("2025-01-31", 0.005),
        ("2025-02-28", 0.006),
        ("2025-03-31", 0.004),
    ]
    rolling = {"1mo": None, "3mo": None, "6mo": None,
               "12mo": None, "inception": None, "parse_error": True}
    pass_ok, errors = gate_check(records, {"2025-03": rolling})
    # 缺口无、捏造无、字段正常、复利跳过 -> pass
    assert pass_ok is True


# --- 17. download_and_extract_parallel ---
def test_download_and_extract_parallel_success(monkeypatch, tmp_path):
    """并发下载+提取成功（mock download_file + extract_pdf_one）。"""
    calls = []

    def fake_download(url, filepath, headers=None):
        calls.append(url)
        with open(filepath, "wb") as f:
            f.write(b"fake pdf")

    def fake_extract(pdf_path, max_pages=None):
        return (0.0053, {"1mo": 0.0053, "3mo": None, "6mo": None,
                         "12mo": None, "inception": None, "parse_error": False})

    monkeypatch.setattr("lib.extract.download_file", fake_download)
    monkeypatch.setattr("lib.extract.extract_pdf_one", fake_extract)

    links = [
        ("2025-03", "https://example.com/mar.pdf"),
        ("2025-01", "https://example.com/jan.pdf"),
        ("2025-02", "https://example.com/feb.pdf"),
    ]
    results = download_and_extract_parallel(links, str(tmp_path), max_workers=3)
    # 按 ym 排序
    yms = [r[0] for r in results]
    assert yms == ["2025-01", "2025-02", "2025-03"]
    # 全部成功
    for ym, commentary, rolling in results:
        assert commentary == 0.0053
        assert rolling["parse_error"] is False
    # 3 个 url 都被下载
    assert len(calls) == 3


def test_download_and_extract_parallel_failure_isolation(monkeypatch, tmp_path):
    """单 PDF 下载失败不中断其他（失败隔离）。"""
    def fake_download(url, filepath, headers=None):
        if "fail" in url:
            raise ConnectionError("boom")
        with open(filepath, "wb") as f:
            f.write(b"ok")

    def fake_extract(pdf_path, max_pages=None):
        return (0.0053, {"1mo": 0.0053, "3mo": None, "6mo": None,
                         "12mo": None, "inception": None, "parse_error": False})

    monkeypatch.setattr("lib.extract.download_file", fake_download)
    monkeypatch.setattr("lib.extract.extract_pdf_one", fake_extract)

    links = [
        ("2025-01", "https://example.com/ok.pdf"),
        ("2025-02", "https://example.com/fail.pdf"),
        ("2025-03", "https://example.com/ok2.pdf"),
    ]
    results = download_and_extract_parallel(links, str(tmp_path), max_workers=3)
    by_ym = {r[0]: r for r in results}
    # 成功项
    assert by_ym["2025-01"][1] == 0.0053
    assert by_ym["2025-03"][1] == 0.0053
    # 失败项：commentary=None, parse_error=True
    assert by_ym["2025-02"][1] is None
    assert by_ym["2025-02"][2]["parse_error"] is True
```

- [ ] **Step 3: 运行测试验证失败**

Run: `cd /Users/chong/Desktop/fixed_fund_analysis/skills && python3 -m pytest tests/test_extract.py -v`
Expected: FAIL（`ImportError: cannot import name 'download_and_extract_parallel'`）

- [ ] **Step 4: 在 `lib/extract.py` 末尾新增 3 函数实现**

```python
# ---------------------------------------------------------------------------
# 并发下载+提取 pipeline（ThreadPool，M5 满核）
# 线程安全：fitz C 层释放 GIL，每 worker 独立 fitz.open。不用 ProcessPool
# （macOS spawn 重新 import fitz 开销 > 15 个小 PDF 收益）。
# ---------------------------------------------------------------------------


def download_and_extract_parallel(
    links: list[tuple[str, str]],
    dest_dir: str,
    max_workers: Optional[int] = None,
) -> list[tuple[str, Optional[float], dict]]:
    """ThreadPool pipeline：每 worker 下载一个 PDF 后立即提取。

    IO 下载与 CPU 提取重叠，无 barrier（比"下载并发->提取并发"两阶段更快）。
    max_workers 默认 min(16, os.cpu_count())（M5 满核 10-16）。
    返回 [(ym, commentary_return, rolling), ...]，按 ym 升序排序。
    失败隔离：单 PDF 下载/提取失败 -> (ym, None, {'parse_error':True})，不中断其他。
    复用 download_file + extract_pdf_one。
    """
    if max_workers is None:
        max_workers = min(16, os.cpu_count() or 8)

    def _failed_rolling() -> dict:
        return {"1mo": None, "3mo": None, "6mo": None,
                "12mo": None, "inception": None, "parse_error": True}

    def _worker(ym: str, url: str) -> tuple[Optional[float], dict]:
        filepath = os.path.join(dest_dir, f"{ym}.pdf")
        download_file(url, filepath)
        return extract_pdf_one(filepath)

    results: list[tuple[str, Optional[float], dict]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_ym = {ex.submit(_worker, ym, url): ym for ym, url in links}
        for fut in concurrent.futures.as_completed(future_to_ym):
            ym = future_to_ym[fut]
            try:
                commentary, rolling = fut.result()
            except Exception:
                commentary, rolling = None, _failed_rolling()
            results.append((ym, commentary, rolling))
    results.sort(key=lambda r: r[0])
    return results


def verify_monthly_vs_rolling(
    monthly: list[tuple[str, float]],
    rolling: dict,
) -> dict:
    """复利交叉验证：用 monthly 复利算最近 N 月累计，对比 rolling 同期值。

    阈值：绝对误差 < 0.5%（容忍 PDF 四舍五入）。rolling 缺列或 monthly 不足
    N 月时跳过该窗口。至少一个窗口通过 -> pass=True（rolling 数据可用）。

    Args:
        monthly: [(date_str, net_return), ...]，可乱序，内部排序。
        rolling: extract_perf_rolling 的返回 dict。

    Returns:
        {'3mo':{'expected','actual','error','pass'}, '6mo':.., '12mo':.., 'pass':bool}
    """
    result = {"3mo": None, "6mo": None, "12mo": None, "pass": False}
    if not monthly or not rolling:
        return result

    sorted_m = sorted(monthly, key=lambda x: x[0])
    returns = [r for _, r in sorted_m]

    any_pass = False
    for key, n in [("3mo", 3), ("6mo", 6), ("12mo", 12)]:
        rolling_val = rolling.get(key)
        if rolling_val is None or len(returns) < n:
            result[key] = {"expected": None, "actual": None,
                           "error": None, "pass": False}
            continue
        actual = 1.0
        for r in returns[-n:]:
            actual *= (1.0 + r)
        actual = actual - 1.0
        error = abs(actual - rolling_val)
        win_pass = error < 0.005  # 0.5%
        result[key] = {"expected": rolling_val, "actual": actual,
                       "error": error, "pass": win_pass}
        if win_pass:
            any_pass = True
    result["pass"] = any_pass
    return result


def gate_check(
    records: list[tuple[str, float]],
    rolling_per_month: dict,
) -> tuple[bool, list[str]]:
    """入库前硬 gate（数据完整性兜底）。

    组合校验：
    1. check_gaps（缺口零容忍）
    2. ANTI-FABRICATION（连续 >= 3 个相同非零浮点数，参考教训 213bdd）
    3. verify_monthly_vs_rolling（用最近月份 rolling，至少一个窗口通过；
       rolling parse_error=True 时跳过不因此 fail）
    4. 字段类型校验（|net_return| < 0.5 即 50%，超出视为字段类型错误）

    Returns:
        (pass, errors)。pass=False 时 errors 列出具体问题，调用方必须停止入库。
    """
    errors: list[str] = []
    if not records:
        return (False, ["无数据"])

    # 1. 缺口检查（缺口零容忍）
    dates = [d for d, _ in records]
    gaps = check_gaps(dates)
    if gaps:
        errors.append(f"缺口: {gaps}")

    # 2. ANTI-FABRICATION：连续 >= 3 个相同非零值（捏造迹象）
    returns = [r for _, r in records]
    for i in range(len(returns) - 2):
        if returns[i] != 0.0 and returns[i] == returns[i + 1] == returns[i + 2]:
            errors.append(
                f"ANTI-FABRICATION: 连续3月相同值 {returns[i]} 起于第 {i} 月"
            )
            break

    # 3. 字段类型校验：|r| < 0.5（月度收益 50% 上限）
    for d, r in records:
        if abs(r) >= 0.5:
            errors.append(f"字段异常: {d} 收益 {r} 超出月度合理范围 |r|<0.5")

    # 4. 复利验证：用最近月份的 rolling
    if rolling_per_month:
        latest_ym = max(d[:7] for d, _ in records)
        latest_rolling = rolling_per_month.get(latest_ym, {})
        if latest_rolling and not latest_rolling.get("parse_error", True):
            verify = verify_monthly_vs_rolling(records, latest_rolling)
            if not verify["pass"]:
                errors.append(f"复利验证失败（{latest_ym}）: {verify}")

    return (len(errors) == 0, errors)
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd /Users/chong/Desktop/fixed_fund_analysis/skills && python3 -m pytest tests/test_extract.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 运行全部测试确认无回归**

Run: `cd /Users/chong/Desktop/fixed_fund_analysis/skills && python3 -m pytest tests/ -v`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add skills/lib/extract.py skills/tests/test_extract.py
git commit -m "feat(skills): add concurrent pipeline + gate_check to extract.py

新增 3 个函数（Task 2，阶段 1-B）:
- download_and_extract_parallel: ThreadPool pipeline 下载+提取, M5 满核,
  失败隔离, fitz 释放 GIL 故用 ThreadPool 非 ProcessPool
- verify_monthly_vs_rolling: 复利交叉验证, 阈值 0.5%
- gate_check: 入库前硬 gate（缺口/ANTI-FABRICATION/复利/字段类型）

数据完整性: 连续3月相同值检测（防 213bdd 式捏造）, 缺口零容忍,
字段类型错误（|r|>=0.5）视为结构性缺陷不参与计算。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

## 验收标准
1. `tests/test_extract.py` 全部 PASS（含新增 ~10 用例）
2. `tests/` 全部 PASS 无回归
3. `lib/extract.py` 顶层有 `import concurrent.futures`，末尾 3 个新函数
4. download_and_extract_parallel 失败隔离生效（失败项 parse_error=True，其他成功）
5. download_and_extract_parallel 结果按 ym 升序
6. gate_check 的 ANTI-FABRICATION 检测连续 3 月相同非零值
7. 已提交

## 完成后
在 `skills/.superpowers/sdd/task-2-report.md` 写报告：新增函数、测试用例数、pytest 摘要、commit hash。
