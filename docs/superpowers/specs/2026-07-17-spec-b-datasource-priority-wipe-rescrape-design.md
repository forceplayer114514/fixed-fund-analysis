# Spec B: 数据源优先级反转 + 全清重爬 + 透明展示

**日期**: 2026-07-17
**分支**: `llm-ingest-gemini`
**前置**: Spec A (skills-era 清理 + name guard + 权威源白名单) 已 merge
**性质**: 数据源架构调整 + 一次性数据洗白 + UI 透明化

---

## 一、目标

### 1.1 三条根问题

Spec A 收官后仍留下三条互相纠缠的病灶:

1. **数据源优先级倒置**: 现代码 fundmonitors 是 L3 兜底 (仅当 `len(links) < 24` 才启), PDF 通路 (L1) 主推。但实测: PDF 通路 GCI 88 份仅覆盖 2018-05 起, Yarra/KKR/Bentham 官网归档滚动删旧月 (只挂近期 3 份), 反而 fundmonitors AJAX 逐月表回溯到 inception (23 年历史). L1 主推 = 遇 PDF 缺 = 走 L2/L3 拼接, 拼接边界处易生冲突 (同月 PDF 一个值 + fundmonitors 一个值, promote_pending guard 拒绝 pending 但前端仍标红)。
2. **skills-era 遗留数据不可用**: `monthly_returns` 现存 869 月, 其中 447 月过闸 (jcb 3 + kkr 168 + yarra 276) + 422 月 skills-era (`pattern_tag=NULL` 或 `nta_net_return_label`, 未过现有闸)。Spec A 已把 skills-era 从 `_AUTHORITATIVE_TAGS` 剔除 -> pending 允许覆盖它们, 但**已入库的旧值仍在前端展示且参与 NAV 递推**。局部 pending 覆盖不能一次性洗白。
3. **name guard 太严**: `_tokenize` + `_name_match` 严格 AND 匹配, 8 类误杀 (连字符/空格/缩写/后缀词消失/share class 变体/别名/大小写/复合词整体匹配), 用户明确反对 "不合理, 太严格了", 主张 "抓到啥显示啥, 眼睛核对"。

### 1.2 Spec B 解法

- **反转 L1/L2**: fundmonitors 提为 L1 主源, 官网 PDF 降为 L2 补差 (但本期 Coolabah × 2 + Macquarie fundmonitors 覆盖不足风险暂延后 Spec C, 本期 L1 单源即入库, 无 L2 拼接)。
- **全清重爬**: `data/fund_analysis.db` 除 `funds` 表 (保白名单 `fundmonitors_fund_id` + `fundmonitors_acc_code`) 全清, 8 支基金 (Coolabah × 2 除外) 全量重跑。清前 `cp` 备份, 出错单支回滚。
- **删 name guard 换透明展示**: `funds` 表新加 `discovered_source_name` 列, 存 fundmonitors 页面实际抓到的基金名, 前端 FundManagement 表格新加一列, 输入名 vs 抓到名不一致时标红核对。删 `_tokenize` / `_name_match` / `_STOPWORDS` / `probe()` guard 分支 / `name_mismatch` 状态 / 24 个 guard 测试。

### 1.3 明确非目标 (延后 Spec C)

以下问题**本期不做**, 记在 Spec C 路线预告:

1. Coolabah × 2 (Plotly HTML NAV 序列, 非 PDF, 非 fundmonitors)
2. Macquarie CSV cum-ex drop 重建通路 (若 fundmonitors 覆盖不足)
3. fundmonitors L1 覆盖不足时的 L2 官网 PDF 补差拼接逻辑
4. 月度 cron 定时刷新 (`APScheduler` 或 systemd timer)
5. `discovered_source_name` 与 `fund_name` 的模糊相似度告警 (levenshtein/fuzz)
6. 白名单以外基金的动态 fundmonitors_fund_id 发现 (Tavily 语义搜)

---

### 1.4 本期作用范围 (基金清单)

10 支已注册基金中, 本期重跑 8 支 (Coolabah × 2 延后 Spec C):

| # | fund_id | fundmonitors_fund_id | acc_code | 通路 |
|---|---------|---------------------|---------|------|
| 1 | bentham_global_income | 3312 | (空) | L1 fundmonitors |
| 2 | jcb_active_bond | 1003 | (空) | L1 fundmonitors |
| 3 | macquarie_australian_fixed_interest | 2107 | (空) | L1 fundmonitors |
| 4 | smarter_money_lscf | 2332 | (空) | L1 fundmonitors |
| 5 | yarra_enhanced_income_fund | 1512 | fresnjxju | L1 fundmonitors |
| 6 | kkr_credit_income_fund | 2118 | fubp32vgu | L1 fundmonitors |
| 7 | gryphon_capital_income | (待 Y.6 探测填白名单) | - | L1 fundmonitors |
| 8 | stake_accumulate | (待 Y.6 探测填白名单) | - | L1 fundmonitors |
| - | coolabah_frhy_assisted | - | - | **本期跳过 (Spec C)** |
| - | coolabah_frhy_institutional | - | - | **本期跳过 (Spec C)** |

第 7-8 支 (GCI + Stake) fundmonitors 白名单未填 -> Y.6 之前先跑 Tavily 探测 (Spec A 通路已支持 `find_fundid_via_tavily`), 填不上则该支重跑失败, 标 confirmed_gap 转 Spec C。

---

## 二、架构 (5 处核心改动)

### 2.1 `llm_ingest/fundmonitors.py`: 删 name guard + 加页面名提取

**删除**:
- `_STOPWORDS` frozenset (11 stopwords)
- `_tokenize(name)` 函数 (~35 行, 括号剥离 + 停用词 + 短 token 过滤)
- `_name_match(query_name, markdown, head_chars=2000)` 函数 (~22 行, 严格 AND token 校验)
- `probe()` 内的 name guard 分支 (~5 行, `if not whitelisted: matched, reason = _name_match(...)`)
- `status='name_mismatch'` 状态

**新增**:
```python
_H1_RE = re.compile(r"^\s*#{1,3}\s+([^\n]+)", re.M)
_BOLD_FIRST_RE = re.compile(r"\*\*([^*]{5,120})\*\*")

def _extract_page_fund_name(markdown: str) -> Optional[str]:
    """从 fundmonitors Full Profile markdown 抽页面上的基金名 (供透明展示)。

    顺序:
      1. 首个 h1/h2/h3 标题 (最常见, `# Yarra Enhanced Income Fund`)
      2. 首个粗体串 (`**Yarra Enhanced Income Fund**`, 5~120 字符)
      3. 找不到返 None (走展示时前端 fallback 到 fund_name)

    不做名字匹配, 不做过滤 -- 只把页面上的字面串拿出来给前端展示。
    """
    if not markdown:
        return None
    m = _H1_RE.search(markdown)
    if m:
        return m.group(1).strip()
    m = _BOLD_FIRST_RE.search(markdown)
    if m:
        return m.group(1).strip()
    return None
```

**`probe()` 返回 dict 变更**:
- 移除: `status='name_mismatch'` 分支
- 新增: `page_fund_name: Optional[str]` 键 (成功抓到 markdown 就填)
- 保留: 白名单短路逻辑 (Spec A)

### 2.2 `llm_ingest/migrations/spec_b_20260717.py`: 建表迁移 (幂等)

新脚本, 通过 `PRAGMA table_info(funds)` 探测列存在, 不存在才 ALTER:

```python
def apply(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(funds)")
    cols = {row[1] for row in cur.fetchall()}
    if "discovered_source_name" not in cols:
        cur.execute("ALTER TABLE funds ADD COLUMN discovered_source_name TEXT")
        conn.commit()
```

放在 `webapp/backend/app/main.py` startup 事件调用 (与既有 `init_db()` 并列), 与 `llm_ingest.store.ensure_tables_if_missing` 都要能触发。

### 2.3 `webapp/backend/app/routers/ingest.py`: 反转 L1/L2 优先级

**现状** (`_run_ingest_job`): discovery -> upsert_fund -> PDF 循环 -> 若 `len(links) < 24` 触发 L3 fundmonitors -> recompute。

**Spec B**: fundmonitors 提到 PDF 循环**之前**且**无 links 阈值门槛**, 覆盖成功即视为 L1 已入库, PDF 循环整段跳过:

```python
# ---- L1: fundmonitors 主源 (Spec B 反转优先级) ----
_job_log(jid, "L1 fundmonitors: probing ...")
try:
    l1_result = fm_mod.probe(req.fund_name, fund_id=req.fund_id, db_conn=conn)
except Exception as e:
    l1_result = {"status": f"exception:{type(e).__name__}"}
_job_log(jid, f"L1 fundmonitors: status={l1_result.get('status')}, "
              f"records={len(l1_result.get('records', []))}, "
              f"page_name={l1_result.get('page_fund_name')}")

if l1_result.get("status") == "ok":
    n_written = store_mod.write_table_records(
        conn, fund_id=req.fund_id,
        records=l1_result["records"], source_url=l1_result["url"],
    )
    _job_log(jid, f"L1 fundmonitors: {n_written} 月入库")
    # 记 discovered_source_name (供前端透明展示)
    conn.execute(
        "UPDATE funds SET discovered_source_name=? WHERE fund_id=?",
        (l1_result.get("page_fund_name"), req.fund_id),
    )
    conn.commit()
    stats["monthly"] = n_written
    # L1 覆盖成功 -> 跳过 PDF 循环 (Spec B 本期不做 L2 补差)
    _job_log(jid, "L1 覆盖成功, 跳过 L2 PDF 通路")
else:
    _job_log(jid, "L1 未覆盖, 走 L2 PDF 通路")
    # (既有 PDF 循环原样保留)
```

**注**: `write_table_records` 已在 Spec A 完成, `pattern_tag='fundmonitors_table'` 已入 `_AUTHORITATIVE_TAGS`, 不需要改动。

### 2.4 `webapp/backend/app/models.py` + `schemas.py`: 加字段

**models.py** (Fund ORM):
```python
class Fund(Base):
    __tablename__ = "funds"
    # ... 既有列 ...
    discovered_source_name = Column(Text, nullable=True)
```

**schemas.py** (FundResponse Pydantic):
```python
class FundResponse(BaseModel):
    # ... 既有字段 ...
    discovered_source_name: Optional[str] = None
```

### 2.5 `webapp/frontend/src/pages/FundManagement.tsx`: 加透明列 + 标红

**types/index.ts**:
```ts
export interface Fund {
  // ... 既有字段 ...
  discovered_source_name?: string | null;
}
```

**FundManagement.tsx** 表格新增第 5 列:
```tsx
<th>数据源基金名</th>
// ...
<td className={
  fund.discovered_source_name
    && fund.discovered_source_name !== fund.fund_name
    ? "text-red-600 font-semibold" : ""
}
    title={
      fund.discovered_source_name
      && fund.discovered_source_name !== fund.fund_name
        ? `输入名: ${fund.fund_name} vs 抓到名: ${fund.discovered_source_name}, 请核对`
        : undefined
    }>
  {fund.discovered_source_name ?? "-"}
</td>
```

Coolabah × 2 (无 fundmonitors 覆盖) 该列展示"-", 前端不额外提示 (Spec C 上通路时再补)。

### 2.6 `llm_ingest/scripts/spec_b_wipe_and_rescrape.py`: 清库 + 批触发

新脚本, 7 步:

1. **前置检查**: `curl http://127.0.0.1:8000/api/health` 确认 webapp 后端起着; 检查 `.env` 有 `SUB2API_KEY`; 检查 `data/fund_analysis.db` 存在。
2. **备份**: `cp data/fund_analysis.db data/fund_analysis.db.spec_b_backup_YYYYMMDD_HHMMSS`。
3. **清表** (单事务): `DELETE FROM monthly_returns; DELETE FROM confirmed_gaps; DELETE FROM pending_review; DELETE FROM fund_metrics; DELETE FROM anomalies; DELETE FROM ai_reports;` -- funds 表不动 (保白名单 fundmonitors_fund_id / fundmonitors_acc_code)。
4. **读 funds 过滤**: `SELECT fund_id, fund_name FROM funds WHERE fund_id NOT IN ('coolabah_frhy_assisted', 'coolabah_frhy_institutional');`
5. **并发触发** (`concurrent.futures.ThreadPoolExecutor(max_workers=4)`): 对每支基金 POST `/api/ingest/funds`, body `{"fund_id": ..., "fund_name": ..., "issuer": null, "confirmed_url": null, "issuer_domain": null, "asx_code": null, "apir_code": null, "max_pdf_pages": null, "limit": null}` (让后端走 fundmonitors L1 白名单短路)。
6. **轮询进度**: 每 5 秒 GET `/api/ingest/jobs/{job_id}`, 直到所有 job `state ∈ {succeeded, failed}`。
7. **汇总**: 打印每支 monthly/pending/gap 数 + failed 原因。

**CLI args**:
- `--yes`: 跳过交互 YES 确认 (默认交互, 显示"将清 869 月, 输 YES 继续")
- `--dry-run`: 只打印将执行的 SQL 和触发列表, 不实际操作
- `--fund-id X`: 只重跑单支 (跳过清库)
- `--skip-wipe`: 复用现有 DB (调试用, 跳步 2-3)

---

## 三、Y 阶段执行序 (10 phase, 只有 Y.5 不可逆)

| Phase | 内容 | 可回退 | 依赖 |
|------|------|--------|------|
| Y.1 | 迁移脚本 `spec_b_20260717.py` + 单测 | 是 (git reset) | - |
| Y.2 | 删 name guard 三函数 + 加 `_extract_page_fund_name` + 单测 | 是 (git reset) | - |
| Y.3 | `ingest.py` 反转 L1/L2 优先级 + 集成测试 | 是 (git reset) | Y.1, Y.2 |
| Y.4 | 前端 `discovered_source_name` 列 + 类型 + 标红逻辑 | 是 (git reset) | Y.1 |
| **Y.5** | **执行 spec_b_wipe_and_rescrape.py --yes** | **仅靠 db backup 恢复** | Y.1~Y.4 全绿 |
| Y.6 | 手动核对 8 支基金重跑结果 (数值/inception/discovered_name) | 是 (单支回滚) | Y.5 |
| Y.7 | GCI inception 从 skills-era 假的 2018-05-21 重置为第一份真实 PDF 日期 | 是 (`UPDATE funds SET inception_date=... WHERE fund_id='gryphon_capital_income'`) | Y.6 |
| Y.8 | webapp 端到端手工验收 (加基金 -> 抓 -> 展示 -> 标红) | 是 (代码回退) | Y.4 |
| Y.9 | 删 `tests/test_fundmonitors_name_guard.py` (24 用例) -- Y.1~Y.4 已加新测试, Y.9 只做删除 | 是 (git reset) | Y.2 |
| Y.10 | commit + 打 tag `spec-b-done` | 是 (git reset --hard) | 全部 |

**Y.5 是唯一不可逆点**: 清库前 `cp` 备份, 万一重跑数值全错 (fundmonitors 突然改版 / TLS 指纹被封 / gate 大面积挂) 直接 `cp data/fund_analysis.db.spec_b_backup_* data/fund_analysis.db` 回退, 代码保留 Spec B 版, 重新调试 fundmonitors.py 后再跑一次 Y.5。

---

## 四、测试 (删 24 + 新 27 = +3, 总 110 -> 113)

### 4.1 删除
- `tests/test_fundmonitors_name_guard.py` (24 用例, 覆盖 `_tokenize` / `_name_match` / `probe()` guard 分支 / share class 变体豁免 / stopword 过滤 / 短 token 过滤 / 别名误杀 / name_mismatch 状态)

### 4.2 新增
| 文件 | 用例 | 覆盖 |
|-----|-----|-----|
| `tests/test_fundmonitors_page_name.py` | 8 | h1/h2/h3 提取, 粗体 fallback, 空 markdown, share class 变体, 特殊字符 |
| `tests/test_fundmonitors_probe_return.py` | 4 | probe 返 dict 含 page_fund_name, 白名单短路仍返 page_name, fetch_fail 返 None page_name, paywall 返 None page_name |
| `tests/test_ingest_priority_l1_l2.py` | 6 | L1 覆盖 skip PDF, L1 失败走 PDF, L1 成功记 discovered_source_name, 无 fundmonitors_fund_id fallback, Coolabah 跳过, exception 兜底 |
| `tests/test_migration_spec_b.py` | 4 | 空 DB 迁移, 已迁移 DB 幂等, 列已存在跳过, PRAGMA 探测容错 |
| `tests/test_spec_b_wipe_script.py` | 5 | dry-run 不改 DB, 备份成功, funds 表未清, Coolabah 排除, --fund-id 单支模式 |

### 4.3 调整
- `tests/test_promote_pending_guard.py`: 白名单 `_AUTHORITATIVE_TAGS` 未变化, 但需确认新 `discovered_source_name` 列不影响 promote 逻辑 (预期 pass 无需改)。

### 4.4 手动验收 (4 项)
1. `curl -X POST /api/ingest/funds -d '{"fund_name": "Bentham Global Income Fund", ...}'`, 轮询到 succeeded, `SELECT * FROM funds WHERE fund_id='bentham_global_income'` 应含非空 `discovered_source_name`。
2. 前端 `/funds`, Bentham 一行"数据源基金名"列有值; 若与"基金名"列不一致, 该 cell 显红字带 tooltip。
3. `SELECT COUNT(*), pattern_tag FROM monthly_returns GROUP BY pattern_tag;` 应全是 `fundmonitors_table`, 无 NULL 无 `nta_net_return_label` 无 `llm` (本期无 L2)。
4. `SELECT fund_id, MIN(date), MAX(date), COUNT(*) FROM monthly_returns GROUP BY fund_id;` 8 支 (不含 Coolabah × 2), 每支月数 >= 24 (fundmonitors 历史深度), inception 与 fundmonitors 页面上首月一致。

---

## 五、错误处理 + 回滚流程

### 5.1 清库脚本错误矩阵

| 错误场景 | 检测 | 处理 |
|---------|------|-----|
| webapp 后端未启 | `curl /api/health` 非 200 | 停, 提示 `python webapp/backend/run.py` 起后端 |
| `.env` 无 SUB2API_KEY | os.environ 读不到 | 停, 提示补 `.env` |
| DB 文件不存在 | `os.path.exists()` False | 停, 提示先跑一次 add_fixed_fund |
| 备份磁盘不足 | `cp` 抛 OSError | 停, 保原 DB 不动 |
| 事务清表失败 (schema 变了) | `conn.execute` 抛 | 回滚事务 (autocommit=False), 保原 DB, 打印 traceback |
| curl_cffi 未装 | ImportError | 该支基金 job 状态 `failed`, 汇总时列出, 不影响其他支 |
| fundmonitors 403/挑战 | probe 返 fetch_fail | 该支 pattern_tag 无, gate 失败, 手动核 (Y.6) |
| gate 大面积挂 | 8 支 >4 支 status=gate_fail | 停, Y.6 之前 Y.5 直接 backup 恢复 |
| 单支 ingest 500 | `state='failed'` | 记入汇总, 不阻塞其他支 |
| 中断 (Ctrl+C) | KeyboardInterrupt | 已触发的 job 继续跑 (BackgroundThread 独立), 未触发的不再触发 |

### 5.2 回滚流程

**Y.1~Y.4 代码阶段**:
```bash
git reset --hard <Y.N-1 commit>
```

**Y.5 数据阶段** (清库脚本失败/重跑数值不对):
```bash
# 停止所有 job (后端进程 Ctrl+C, kill uvicorn)
cp data/fund_analysis.db.spec_b_backup_YYYYMMDD_HHMMSS data/fund_analysis.db
# 代码保留 Spec B 版, 调试 fundmonitors.py, 重新跑 Y.5
```

**Y.6+ 单支基金问题**:
```bash
# 只重跑单支
python llm_ingest/scripts/spec_b_wipe_and_rescrape.py --skip-wipe --fund-id yarra_enhanced_income_fund
# 或手动清单支
sqlite3 data/fund_analysis.db "DELETE FROM monthly_returns WHERE fund_id='yarra_enhanced_income_fund'"
```

### 5.3 缺口哲学 (与 CLAUDE.md 一致)

- 单支基金 fundmonitors 缺月 -> `confirmed_gaps` 落库, **不 backfill 不 forward-fill**
- 单支基金 fundmonitors 全无 (Coolabah × 2) -> `funds.discovered_source_name` = NULL, 前端展示 "-", monthly_returns 无记录, 前端指标页面对该支显 "数据不足"
- fundmonitors 页面基金名与输入名不一致 -> 只标红, **不阻塞入库** (数据先入, 人工核对)

---

## 六、风险

| # | 风险 | 概率 | 影响 | 缓解 |
|---|-----|-----|-----|-----|
| 1 | fundmonitors 突然改版 (HTML 结构/CF 指纹) | 低 | 全部 8 支 gate_fail | Y.5 前先跑单支 probe smoke test; 备份可回退 |
| 2 | curl_cffi.impersonate='chrome124' 被 CF 边缘认出 | 低 | 全部 fetch_fail | 试 chrome131_android / safari180; 备份可回退 |
| 3 | max_workers=4 触发 sub2api 限流 (本期 L1 单源不走 sub2api, 但 issuer 探测可能走) | 低 | 部分 job pending | 降 max_workers=2 重跑 |
| 4 | fundmonitors 页面基金名与输入名字面差异过大 (缩写 vs 全称) | 中 | 前端大面积标红 | 用户核对是 feature 不是 bug; Spec C 加模糊相似度阈值可选 |
| 5 | Yarra/KKR 现有 447 月过闸数据比 fundmonitors 覆盖更长/更准 | 中 | 清后数据变少变差 | Y.6 手动核对 min(date), 若倒退超 12 月 backup 回退 |
| 6 | GCI inception 重置错 (第一份真实 PDF 也是 fundmonitors 起点前) | 低 | inception 错位 | Y.7 手动核对; 单支 UPDATE 可逆 |
| 7 | discovered_source_name 迁移在旧 schema 上失败 | 低 | ingest 500 | 迁移幂等 + PRAGMA 探测; 单测覆盖 |
| 8 | 前端标红逻辑写错标错行 | 低 | UX 问题 | React DevTools 手工核 4 支 |
| 9 | .env 忘配 SUB2API_KEY 触发 L2 时崩 | 低 | Coolabah 触发 L2 但本期不做, 8 支纯 L1 无影响 | 前置检查 |
| 10 | 磁盘空间不足备份 | 低 | 清库前停 | 前置检查 `df -h` |

---

## 七、完成标志 (6 项打钩)

- [ ] Y.1-Y.10 全跑完, git log 8 个 commit + tag `spec-b-done`
- [ ] `pytest tests/` 113 用例全绿 (删 24 + 新 27 = +3)
- [ ] 8 支基金 (不含 Coolabah × 2) `monthly_returns.pattern_tag` 全 = `fundmonitors_table`
- [ ] 8 支基金 `funds.discovered_source_name` 全非空
- [ ] 前端 `/funds` 页新增列渲染正确, 不一致标红
- [ ] `data/fund_analysis.db.spec_b_backup_*` 保留至 Spec C 完成 (回退保险)

---

## 八、交付清单

**代码文件** (11 处):
1. `llm_ingest/fundmonitors.py` (删 guard + 加 `_extract_page_fund_name`)
2. `llm_ingest/migrations/spec_b_20260717.py` (新)
3. `llm_ingest/migrations/__init__.py` (新, 空)
4. `llm_ingest/scripts/spec_b_wipe_and_rescrape.py` (新)
5. `llm_ingest/scripts/__init__.py` (若无则新)
6. `webapp/backend/app/routers/ingest.py` (反转 L1/L2)
7. `webapp/backend/app/models.py` (Fund 加列)
8. `webapp/backend/app/schemas.py` (FundResponse 加字段)
9. `webapp/backend/app/main.py` (startup 调 migrations.apply)
10. `webapp/frontend/src/types/index.ts` (Fund 加字段)
11. `webapp/frontend/src/pages/FundManagement.tsx` (加列 + 标红)

**测试文件** (7 处):
1. `tests/test_fundmonitors_name_guard.py` (删)
2. `tests/test_fundmonitors_page_name.py` (新, 8)
3. `tests/test_fundmonitors_probe_return.py` (新, 4)
4. `tests/test_ingest_priority_l1_l2.py` (新, 6)
5. `tests/test_migration_spec_b.py` (新, 4)
6. `tests/test_spec_b_wipe_script.py` (新, 5)
7. `tests/test_promote_pending_guard.py` (核对, 预期不改)

**文档** (2 处):
1. `docs/superpowers/specs/2026-07-17-spec-b-datasource-priority-wipe-rescrape-design.md` (本文档)
2. `docs/superpowers/plans/<日期>-spec-b-execution-plan.md` (writing-plans skill 产出)

**备份**:
- `data/fund_analysis.db.spec_b_backup_YYYYMMDD_HHMMSS`

---

## 九、时间估算

| 阶段 | 预估 |
|-----|-----|
| Y.1-Y.4 代码 + 单测 | 2.5 小时 |
| Y.5 清库 + 8 支重跑 (fundmonitors 平均 20 秒/支 + 4 并发) | 30 分钟 |
| Y.6-Y.10 验收 + inception + 端到端 + 测试 + commit | 30 分钟 |
| **合计** | **3-4 小时** |

---

## 十、关键决策记录

| # | 决策 | 依据 |
|---|-----|-----|
| 1 | 全清 869 月 (含 447 过闸) 而非仅清 skills-era 422 月 | 部分清会保留 pattern_tag=llm/fundmonitors_table 但入库时序错乱; 全清 + 新序 = 干净 |
| 2 | Coolabah × 2 本期不做 | 无 PDF 无 fundmonitors, 需 Plotly HTML 通路, 延后 Spec C |
| 3 | Macquarie 本期不做 CSV cum-ex, 只跑 fundmonitors | 白名单 FundID=2107 已填, 若 fundmonitors 覆盖不足 Spec C 加 |
| 4 | 彻底删 name guard 而非放宽 | 用户明确反对 "太严格"; 透明展示更符合审计需求 |
| 5 | 本期不做 L2 补差 | L1 单源已够 8 支覆盖; 补差拼接逻辑复杂延后 |
| 6 | 备份用 `cp` 而非 `sqlite3 .backup` | 简单可靠; 无并发写入; 文件级别 restore 最快 |
| 7 | max_workers=4 并发 | 保守估 sub2api / fundmonitors CF 边缘限流; 4 支同时够快够稳 |
| 8 | 清库脚本默认交互 YES 确认 | 不可逆操作前一道人工闸; `--yes` 供自动化 |

---

## 十一、Spec C 路线预告

Spec B 完成后, 遗留问题按优先级:

1. **Coolabah × 2 Plotly HTML 通路** (高优, 用户主动关注)
2. **Macquarie CSV cum-ex drop** (中优, 若 fundmonitors 月数 < 36)
3. **fundmonitors L1 覆盖不足时 L2 官网 PDF 补差** (中优, 6 支已 OK 暂搁)
4. **月度 cron 定时刷新** (低优, 目前手工触发够用)
5. **discovered_source_name 模糊相似度阈值** (低优, 现在标红即可)
6. **动态 fundmonitors_fund_id 发现** (低优, 白名单已覆盖 6 支)

---

## 十二、前置条件核对

- [x] Spec A 已 merge (`_AUTHORITATIVE_TAGS` + 白名单短路)
- [x] `pytest tests/` 110 用例全绿
- [x] `funds.fundmonitors_fund_id` 6 支已填 (bentham=3312, jcb=1003, macquarie=2107, smarter_money=2332, yarra=1512, kkr=2118); GCI + Stake 白名单 Y.6 前动态探测
- [x] `.env` 有 SUB2API_KEY (虽本期 L1 不用, 备 L2 或 discovery 探测)
- [x] webapp 后端 + 前端可启 (`webapp/backend/run.py` + `webapp/frontend/npm run dev`)
- [x] `curl_cffi` 已装 (Spec A 已装)
- [x] 磁盘空间 > 100MB (备份 ~40MB × 1)
- [x] llm-ingest-gemini 分支 clean, 无未提交改动
