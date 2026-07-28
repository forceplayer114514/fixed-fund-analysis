## Task 5: 【漏洞 10.2】Wayback 入口收窄 —— 不再按整个发行商域名无差别抓

**背景（Spec G 10.2，四个漏洞里最严重的一个）：** `probe_l2_wayback()` 的 CDX 查询范围是 `{issuer_domain}/*` 与 `{issuer_domain}/wp-content/uploads/*`，即该发行商站上曾存在过的**全部** PDF。筛选条件仅三条：`statuscode:200`、`mimetype:application/pdf`、文件名解析出的 ym 落在 `gap_set` 内。

**没有基金名筛选**，也**没有接上别处已有的 `_NON_MONTHLY_HINTS` 文档类型黑名单**（该黑名单在 `discover.py:468` 定义，只在 `probe_l1_official` 与 L1_nav 用了）。

一家发行商旗下十支基金的 PDF 同处一域，只要文件名月份落在缺口内，兄弟基金月报即被当作本基金数据填入。

**加重情节：** 此步专用于补缺口，而 `CLAUDE.md` 一.3 对缺口是零容忍、禁填补的。当前实现却用全系统最宽松的条件往缺口里塞东西。

**Files:**
- Modify: `llm_ingest/discover.py:674-716`（`probe_l2_wayback` 签名与函数体）、约 885 行调用点
- Test: `tests/test_discover.py`（新增测试类）

**Interfaces:**
- Consumes: `discover2._best_match_pdfs(pdf_urls: List[str], fund_name: str) -> List[str]`（Task 4 新增）
- Produces: `probe_l2_wayback(issuer_domain: str, gap_set: Set[str], fund_name: str) -> List[Tuple[str, str]]` —— **新增第三个必填参数 `fund_name`**

- [ ] **Step 1: 写复现测试（RED）**

在 `tests/test_discover.py` 末尾追加：

```python
class TestWaybackNarrowing:
    """Spec G 10.2: Wayback 按整个发行商域名抓, 必须筛基金名与文档类型。

    该步专用于补缺口, 而 CLAUDE.md 一.3 对缺口零容忍禁填补 --
    此处却曾用全系统最宽松的条件往缺口里塞东西。
    """

    def _cdx_payload(self, originals):
        import json
        rows = [["timestamp", "original", "statuscode"]]
        for o in originals:
            rows.append(["20260701000000", o, "200"])
        return json.dumps(rows)

    def test_sibling_fund_pdf_not_used_to_fill_gap(self, monkeypatch):
        from llm_ingest import discover as disc

        target = "https://yarracm.com/docs/yarra-enhanced-income-jun-2026.pdf"
        sibling = "https://yarracm.com/docs/yarra-australian-income-jun-2026.pdf"
        monkeypatch.setattr(
            disc, "_curl",
            lambda url, timeout=30: self._cdx_payload([sibling, target]),
        )

        hits = disc.probe_l2_wayback(
            "yarracm.com", {"2026-06"}, "Yarra Enhanced Income Fund",
        )
        urls = [u for _ym, u in hits]

        assert any(target in u for u in urls), "目标基金的 PDF 应当被采纳"
        assert not any(sibling in u for u in urls), (
            "兄弟基金 Yarra Australian Income 的 PDF 被用来填缺口了"
        )

    def test_pds_tmd_not_used_to_fill_gap(self, monkeypatch):
        from llm_ingest import discover as disc

        pds = "https://yarracm.com/docs/yarra-enhanced-income-PDS-jun-2026.pdf"
        monkeypatch.setattr(
            disc, "_curl", lambda url, timeout=30: self._cdx_payload([pds]),
        )

        hits = disc.probe_l2_wayback(
            "yarracm.com", {"2026-06"}, "Yarra Enhanced Income Fund",
        )

        assert hits == [], "PDS 不是月度业绩报告, 不得用来填缺口"
```

- [ ] **Step 2: 跑测试确认失败（RED）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover.py::TestWaybackNarrowing','-v']))"
```

预期：两个测试都 FAIL —— 第一个是 `TypeError: probe_l2_wayback() takes 2 positional arguments but 3 were given`。

- [ ] **Step 3: 给 `probe_l2_wayback` 加基金名与文档类型过滤**

`llm_ingest/discover.py`，把 `probe_l2_wayback` 的签名与筛选段改为：

```python
def probe_l2_wayback(
    issuer_domain: str,
    gap_set: Set[str],
    fund_name: str,
) -> List[Tuple[str, str]]:
    """L2: 用 CDX API 查 issuer_domain 快照, 从 original URL 提月份补 gap_set 中的洞.

    每月最多 CDX_SNAPSHOTS_PER_MONTH 快照 (抄自 strategies.py 补1).

    Spec G 10.2: CDX 查的是整个发行商域名下的全部 PDF。一家发行商旗下多支基金
    的文件同处一域, 若只按"文件名月份落在缺口内"筛选, 兄弟基金的月报会被当作
    本基金数据填进缺口。而本步专用于补缺口 -- CLAUDE.md 一.3 对缺口是零容忍、
    禁填补的, 这里必须是全系统最严的地方, 不是最宽的。故加两道过滤:
      (a) _NON_MONTHLY_HINTS: 排除 PDS/TMD/FSG/研究报告等非月度业绩文档
      (b) _best_match_pdfs: 只留与 fund_name 匹配分并列最高的
          -- 必须用**相对**判据。绝对判据 (_pdf_slug_match_count > 0) 与
          Spec G 10.1 的根因同病, 挡不住兄弟基金:
            目标 "Yarra Enhanced Income Fund" -> {yarra, enhanced, income}
            yarra-enhanced-income-jun-2026.pdf   交集 3
            yarra-australian-income-jun-2026.pdf 交集 2, 同样 > 0
          故改为两趟: 先收集候选, 再按最高分筛, 最后套快照数上限。
    """
    if not gap_set or not issuer_domain:
        return []
    from .discover2 import _best_match_pdfs
    patterns = [f"{issuer_domain}/*", f"{issuer_domain}/wp-content/uploads/*"]

    # ---- 第一趟: 收集通过文档类型与月份筛选的候选 ----
    cands: List[Tuple[str, str, str]] = []  # (ym, ts, original)
    for pat in patterns:
        # https 端更稳; http 通常也可, 但本机可能被拦
        api = (
            f"https://web.archive.org/cdx/search/cdx?url={pat}"
            f"&output=json&fl=timestamp,original,statuscode"
            f"&filter=statuscode:200&filter=mimetype:application/pdf"
            f"&limit=500"
        )
        out = _curl(api, timeout=30)
        if not out:
            continue
        try:
            arr = json.loads(out)
        except json.JSONDecodeError:
            continue
        for row in arr[1:]:  # 首行表头
            if len(row) < 2:
                continue
            ts, original = row[0], row[1]
            fname = original.rsplit("/", 1)[-1]
            # (a) 文档类型: PDS/TMD/FSG/研究报告等不是月度业绩报告
            if _NON_MONTHLY_HINTS.search(fname):
                continue
            ym = _parse_ym_from_text(original)
            if not ym or ym not in gap_set:
                continue
            cands.append((ym, ts, original))

    if not cands:
        return []

    # ---- 第二趟: (b) 只留与 fund_name 匹配分并列最高的 ----
    keep = set(_best_match_pdfs([o for _ym, _ts, o in cands], fund_name))
    snap_count: Dict[str, int] = {}
    hits: List[Tuple[str, str]] = []
    for ym, ts, original in cands:
        if original not in keep:
            continue
        if snap_count.get(ym, 0) >= CDX_SNAPSHOTS_PER_MONTH:
            continue
        snap_count[ym] = snap_count.get(ym, 0) + 1
        hits.append((ym, f"https://web.archive.org/web/{ts}/{original}"))
    return _dedup_links(hits)
```

- [ ] **Step 4: 更新调用点传 `fund_name`**

`llm_ingest/discover.py` 约 885 行，把

```python
            l2_links = probe_l2_wayback(dom_clean, gap_set)
```

改为

```python
            l2_links = probe_l2_wayback(dom_clean, gap_set, fund_name)
```

- [ ] **Step 5: 跑测试确认通过（GREEN）**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/test_discover.py','-v']))"
```

预期：两个新测试 PASS。若 `test_discover.py` 里已有的 wayback 测试因签名变化而失败，给它们补上 `fund_name` 实参（用与 URL slug 匹配的基金名，例如 URL 含 `yarra-enhanced` 就传 `"Yarra Enhanced Income Fund"`）。

- [ ] **Step 6: 跑全量测试**

```bash
python3 -c "import pytest,sys; sys.exit(pytest.main(['tests/','-q','--no-header']))"
```

预期：无新增失败。

- [ ] **Step 7: Commit**

```bash
git add llm_ingest/discover.py tests/test_discover.py
git commit -m "fix(discover): Wayback 补缺口加基金名与文档类型过滤 (Spec G 10.2)

CDX 查的是整个发行商域名下全部 PDF, 原本只按"文件名月份落在缺口内"筛,
一家发行商旗下多支基金文件同处一域 -> 兄弟基金月报被当本基金数据填缺口。
本步专用于补缺口, 而 CLAUDE.md 一.3 对缺口零容忍禁填补, 这里必须最严。

加两道: _NON_MONTHLY_HINTS 排 PDS/TMD/研究报告; _pdf_slug_match_count
要求文件名与基金名有实义 token 交集。probe_l2_wayback 新增 fund_name 必填参数。"
```

---

