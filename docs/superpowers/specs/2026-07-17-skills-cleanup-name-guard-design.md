# Spec A — skills 残留清理 + name guard + guard 扩权威源设计

- 日期: 2026-07-17
- 状态: 已定案 (待实施)
- 作者: Claude Code (人机协同)
- 关联提交: `0491a1d` (L3 guard 首版)
- 后续 Spec: Spec B (反转数据源优先级 + 全量重跑 skills-era 数据) 另立

---

## 1. Background

### 1.1 库现状

现库 8 支基金 422 月数据, 全部来自 skills 时代管道 (skills/ 目录下的正则提取 + 直接入库).
skills-era 数据的典型标记:

- `monthly_returns.pattern_tag` 为 `NULL` (未经 llm_ingest 结构化标签)
- `monthly_returns.source_tag` 常为 `nta_net_return_label` 等历史标签
- 未经过 `llm_ingest` 管道的两道闸 (gate_check 前置校验 + promote_pending 冲突挡拦)

这些数据在库里是"既成事实", 但可信度未经现行两道闸复核, 且部分字段 (如 JCB 三行日期)
存在结构性 bug (llm_ingest/store.py `_ym_to_date` 修复前入库, 日期为月初而非月末).

### 1.2 KKR 事件

2026-07 中旬发生一起 pending 覆盖 L3 权威值的事件:

- 33 条 skills-era pending 记录待 approve
- 其中若干条 `source_tag` 覆盖 L3 (fundmonitors_table) 已存在的权威值
- 已通过 `0491a1d` commit 加 `promote_pending` L3 guard 挡下
- 但该 guard **仅挡 L3**, 未覆盖 `llm` 等其他权威 source_tag

### 1.3 fundmonitors L3 探针撞车

对现有 8 支基金跑 fundmonitors 探针, 结果:

| 类别 | 基金 | 说明 |
|---|---|---|
| 有效命中 (4) | bentham / jcb / macquarie / smarter_money | FundID 正确, 名称 markdown 校对通过 |
| 撞车 (2) | coolabah × 2 | 探针返 smarter_money 的 URL, 名称不匹配 |
| 无 FundID (2) | gci / stake | Tavily 返 no_fundid, 无法探测 |

撞车根因: Tavily 搜索按词面相似度返 URL, `probe` 不校验落地页基金名, 导致 Coolabah 两支
基金都被指到 smarter_money 的 fund profile.

### 1.4 本 Spec 目标

针对上述三类问题做"止损级"处理:

1. 挡住"错源 FundID"注入库 (name guard)
2. 已错的 JCB 日期 + GCI inception 一次性修回
3. `promote_pending` guard 从 L3 扩到所有权威 source_tag, 但仍允许覆盖 skills-era NULL / 旧标签

---

## 2. Scope

- **Spec A (本文档)**: 立刻能做的止损 + guard 补齐, 不动数据本体, 不重跑历史.
- **Spec B (后续另立)**: 反转数据源优先级 (llm_ingest > skills), 全量重跑 skills-era 数据.
  本 Spec 不涉及, 但设计上为 Spec B 留口子 (P4 允许 pending 覆盖 skills-era NULL).

---

## 3. 决策

本节逐项列本次讨论定案的每一条 (决策 + 原因), 供实施时对照.

### 3.1 P0 — name guard

**决策**: 严格 token AND 匹配 (剔停用词, 剔括号, 保留连字符) + `funds.fundmonitors_fund_id`
白名单 override.

**原因**:

- (a) 挡 Coolabah 撞车零成本, 误杀率低 (基金名 token 交集足够区分)
- (b) 白名单 override 允许人工背书跳 guard (人工确认过的 FundID 不再走 name match)

**决策项细节**:

- 停用词列表 (小写比较):

    ```
    the, fund, trust, pty, ltd, limited, unit, class,
    wholesale, retail, institutional, capital, management
    ```

- 括号剔除: `Fund (Retail Class)` -> `Fund` (share class 后缀不参与匹配)
- 连字符 token 保留: `floating-rate` 当作一个 token, 不拆
- 只匹配落地页 markdown **首 2000 字** (避免全文噪声)
- 白名单命中 (`funds.fundmonitors_fund_id IS NOT NULL`) 直接跳过 name guard,
  视为人工已背书

### 3.2 P3 — JCB 日期 bug 迁移

**决策**: 3 行月初日期 -> 月末日期, 迁移脚本硬修.

**原因**: `llm_ingest/store.py::_ym_to_date` 已修但 JCB 三行是修前入库, 库里存的是月初
(如 `2024-01-01`) 而不是月末 (`2024-01-31`). 一次性 UPDATE 到位.

### 3.3 P4 — promote_pending guard 扩权威源

**决策**: 白名单权威源集合 `{fundmonitors_table, llm}`; guard 只挡 slot 已被权威源占据的
覆盖尝试. 覆盖 NULL / `nta_net_return_label` / 空 slot 仍然允许.

**原因**:

- 只挡 llm_ingest 系新入的高信度数据, 防止后续 pending 覆盖它们
- 允许 pending 覆盖 skills-era 旧数据 (NULL / nta_net_return_label 等), 对齐 Spec B
  "洗数据"目标 — Spec B 会通过 pending 通道逐步把 skills-era 数据换成 llm_ingest 版本
- 新 action 名: `skipped_authoritative_covered` (泛化旧的 `skipped_l3_covered`,
  枚举值向前兼容: 旧代码路径可作为别名保留一版本再删)

### 3.4 P5 — GCI inception 日期

**决策**: `2019-02-28` (月末, 对齐现库惯例); `inception_assumed=0` (人工确认过, 非假设).

**原因**: 抑制 `2018-05` ~ `2019-01` 期间的 pre-inception gap 警告 (基金实际成立于
2019-02, 之前无数据是合理的, 不是缺口).

---

## 4. Architecture

### 4.1 DB schema 变更

`funds` 表:

- 新增列 `fundmonitors_fund_id INTEGER` (nullable, 白名单 FundID)
- 新增列 `fundmonitors_acc_code TEXT` (nullable, 备用二级键)
- 更新 GCI 行 `inception_date = '2019-02-28'`, `inception_assumed = 0`

`monthly_returns` 表:

- UPDATE 3 行 JCB 记录 date 字段 (月初 -> 月末)

### 4.2 代码变更 (逐文件概要)

#### `llm_ingest/fundmonitors.py`

新增两个纯函数 + probe 签名扩展:

```python
def _tokenize(name: str) -> set[str]:
    """基金名 -> token 集合. 剔括号, 剔停用词, 保留连字符 token."""

def _name_match(db_name: str, page_markdown: str) -> bool:
    """严格 token AND: db_name 所有 token 都出现在 page_markdown 首 2000 字."""

def probe(
    fund_name: str,
    fund_id: Optional[int] = None,      # 新增: 白名单 override
    db_conn: Optional[Any] = None,      # 新增: 用于 lookup fundmonitors_fund_id
) -> ProbeResult:
    """
    流程:
    1. 若 db_conn + fund_name 命中 funds.fundmonitors_fund_id 白名单, 直接返回
       该 FundID (跳过 Tavily + name guard).
    2. 否则走 Tavily 搜索 -> fetch 落地页 -> _name_match 校验 -> 通过才返 FundID.
    3. name_match 失败返 ProbeResult(status='name_mismatch', ...).
    """
```

#### `llm_ingest/store.py::promote_pending`

```python
_AUTHORITATIVE_TAGS: frozenset[str] = frozenset({"fundmonitors_table", "llm"})

def promote_pending(...) -> PromoteResult:
    """
    Guard:
    - 若目标 slot 已存在记录且 existing.source_tag in _AUTHORITATIVE_TAGS,
      返回 action='skipped_authoritative_covered' (新)
    - 否则允许覆盖 (skills-era NULL / 旧标签 slot)
    """
```

action 枚举新增 `skipped_authoritative_covered`; 旧的 `skipped_l3_covered` 保留一版本
作为别名, 下版本删.

#### `llm_ingest/migrations/spec_a_20260717.py`

幂等迁移脚本, 步骤:

1. `ALTER TABLE funds ADD COLUMN fundmonitors_fund_id INTEGER`
   (探测: `PRAGMA table_info(funds)` 若已有则跳过)
2. `ALTER TABLE funds ADD COLUMN fundmonitors_acc_code TEXT` (同上探测)
3. 种子已知白名单: bentham / jcb / macquarie / smarter_money 的 FundID
   (仅在列为 NULL 时更新, `WHERE fundmonitors_fund_id IS NULL`)
4. GCI inception: `UPDATE funds SET inception_date='2019-02-28', inception_assumed=0
   WHERE fund_code='GCI' AND (inception_date IS NULL OR inception_date != '2019-02-28')`
5. JCB 日期修正: 三行 `UPDATE monthly_returns SET date = last_day_of_month(date)
   WHERE fund_code='JCB' AND date IN (...)` (只针对已知三行月初)
6. 输出 applied / skipped 明细 JSON

#### `llm_ingest/cli.py`

新增子命令:

```python
def migrate(argv: list[str]) -> int:
    """python3 -m llm_ingest.cli migrate — 手动跑迁移, 输出 JSON 报告."""
```

不自动挂到 uvicorn 启动路径, 手动触发.

#### `webapp/backend/app/routers/ingest.py`

- `approve_pending` handler: 对齐新 action `skipped_authoritative_covered`, 返回
  HTTP 409 + 明确 detail (是权威源覆盖被挡, 不是普通冲突)
- ingest 入口调用 `fundmonitors.probe` 时传入 `fund_id` (从 URL/body 解析) 和
  `db_conn` (依赖注入)

#### `webapp/frontend/src/pages/FundManagement.tsx`

pending 弹窗文案接住新 action:

- 旧: "已被 L3 权威值占据, 拒绝覆盖"
- 新: "已被权威源 ({source_tag}) 占据, 拒绝覆盖"

无布局/新页面变更, 仅字符串.

---

## 5. Testing Strategy

### 5.1 P0 name guard 单元测试

`tests/test_fundmonitors_name_guard.py` — 约 20 用例:

- `_tokenize`: 停用词剔除 / 括号剔除 / 连字符保留 / 大小写归一 / 空串 / 全停用词 (~8)
- `_name_match`: 精确命中 / 部分命中 (拒) / 顺序无关 / 2000 字截断 / 标点噪声 (~6)
- `probe` 通路: 白名单命中跳过 guard / name_mismatch 返回 / Tavily 无结果 (~6)

### 5.2 P4 promote_pending guard 单元测试

`tests/test_promote_pending_guard.py` — 约 5 用例:

- 已有 L3 slot 拒 pending (`skipped_authoritative_covered`)
- 已有 llm slot 拒 pending (新增覆盖)
- NULL slot 允许 pending 覆盖
- `nta_net_return_label` slot 允许 pending 覆盖
- 空 slot (无记录) 允许 pending 写入

### 5.3 迁移单元测试

`tests/test_migration_20260717.py` — 约 4 用例:

- 幂等性: 二次跑全 `skipped`, 无副作用
- JCB 三行日期从月初变月末
- GCI inception = `2019-02-28`, inception_assumed=0
- 白名单种子: bentham/jcb/macquarie/smarter_money 的 FundID 落库

### 5.4 覆盖率目标

- 单元测试: 30+ 用例
- 集成测试: 5+ 用例 (probe + promote_pending 端到端, 用 sqlite 内存库)

---

## 6. Migration Execution

- 触发方式: 手动 `python3 -m llm_ingest.cli migrate`
- 幂等性: 二次跑全部返回 `skipped`, DB 状态不变
- 输出: applied / skipped 明细 JSON (stdout), 例:

    ```json
    {
      "applied": [
        {"step": "add_col_fundmonitors_fund_id", "status": "applied"},
        {"step": "seed_bentham_fund_id", "status": "applied", "value": 12345}
      ],
      "skipped": [
        {"step": "add_col_fundmonitors_acc_code", "reason": "column_exists"}
      ]
    }
    ```

- uvicorn 启动**不**自动跑迁移, 避免生产环境意外触发.

---

## 7. Non-Goals (明确不做)

- 不动前端布局, 不加新页面 (仅改 pending 弹窗文案)
- 不动 webapp 计算 / 异常检测 / RBA 相关逻辑
- 不改 `skills/` 目录 (Phase 5 待议, 本 spec 不涉及)
- 不做 Spec B 的"反转数据源优先级 + 全量重跑 skills-era 数据"
- 不为 gci / stake 强解决 `no_fundid` (无源可探, 留白至 Spec B)

---

## 8. Risks & Mitigation

| 风险 | 缓解 |
|---|---|
| 迁移非幂等 (二次跑破坏数据) | `PRAGMA table_info` 探测 + `WHERE ... IS NULL` 守卫, 单元测试覆盖幂等场景 |
| Yarra / KKR FundID 从 `source_quote` 抠不到 | 迁移脚本记 `skipped` 不阻塞, 白名单为空即走原 probe 逻辑 |
| 白名单 override 填错 FundID | 首次运行 `gate_check` 会发现拉到的值不合理, 走 pending 通道人工审核 |
| P4 允许 pending 覆盖 skills-era 数据 -> llm_ingest 若有 bug, pending 会污染新数据 | 已有两道闸 (gate_check + promote_pending guard 对权威源) 挡, 且 skills-era 数据本就低可信度, Spec B 会全量重跑 |
| Coolabah 撞车 name guard 误杀正确源 | name guard 匹配阈值可回退到"token 覆盖率 >= 阈值"而非严格 AND (若单元测试出现误杀再迭代) |

