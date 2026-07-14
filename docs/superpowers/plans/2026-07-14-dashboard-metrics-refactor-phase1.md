# 对比看板指标体系重构 - Phase 1 实施计划（定稿）

> 依据: `~/Downloads/PDD_对比看板指标与图表系统重构.md`
> 范围: **仅 Phase 1（指标层重构）**。按 PDD 第 4 节，Phase 1 验收通过后再出 Phase 2 详细方案（第 9 节给出路线图概要）。
> 状态: **已批准**（用户附四条修正意见，修正 1/2 已并入本 plan，修正 3/4 列入第 8 节实施 checklist）。

---

## 0. 关键决策记录

### 决策 1: RBA 基准缺失处理（用户确认）
PDD 1.7 要求"RBA 月份缺失 -> 剔除 + 写异常 + 继续"，与 CLAUDE.md 第一条"零容忍报错停止"冲突。
**采纳: 仅对 RBA 基准放宽，基金自身月度缺口仍零容忍。**
- 基金序列内部缺口: `_find_month_gaps` 报错停止（不变）。
- RBA 某月缺失（基金有收益、RBA 无利率）: 剔除该月于超额序列 + 写 `type='rba_missing'` 异常 + 继续。
- 这是对 CLAUDE.md 1.3 的 **scoped 放宽（仅限 RBA 基准参考量）**，在 `crud.resolve_rf_rates` 注释留痕。

### 决策 2: 数据库迁移（无 Alembic）+ 备份保险（修正 1）
`fund_metrics`/`anomalies` 为可重算派生缓存；源数据三表（`funds`/`monthly_returns`/`rba_cash_rates`）不动。**但重算依赖新代码一次跑通，若新代码有 bug 会处于"旧缓存已删、新缓存生成失败"中间态，故迁移脚本必须先备份。** 详见第 2 节。

### 决策 3: 新指标保留 orig/un 双口径
`smoothingMode`（原始/去平滑）切换要求所有指标都有 orig 与 un 变体。新增 IR、年化收益率、恢复月数各产出 orig/un 两套。

### 决策 4: 小样本门禁 n 的口径
- `history_months` = 基金自身月数（len returns，Geltner 36 月防火墙 + "X 个月历史"展示）。
- `excess_sample_months` = 超额序列有效月数（剔除 RBA 缺失后），用于 PDD 1.5 的 n<24 门禁（仅作用于 IR、超额胜率的名次）。
- 无 RBA 缺失时两者相等。

### 决策 5: 最长跑输的连续性语义（修正 2，必须）
最长跑输是**连续性敏感指标**。若把 RBA 缺失月从超额序列过滤掉得到压缩序列，被剔除月份两侧的跑输月会在压缩序列里相邻，被静默数成"连续跑输"——违反第 7 条禁止事项精神。
**处理: `compute_all_metrics` 同时构建两条序列:**
- **压缩序列** `excess_orig`（None 已过滤）: 供 IR、超额胜率、年化超额收益（均不依赖时序连续性）。
- **未压缩对齐序列** `excess_aligned_orig`（RBA 缺失位保留为 `None`）: 供最长跑输。`calculate_max_consecutive_underperform` 改为接受 `list[float|None]`，遇 `None` 视为 **streak 中断（保守）**，即 `current=0`。
- RBA 缺失极罕见，但既然为它设计机制就设计到底，不留语义含糊。单测覆盖"跑输-缺失-跑输"不被拼成连续 streak。

---

## 1. 后端改动

### 1.1 `webapp/backend/app/calculations.py`

**删除** `calculate_omega_ratio`。

**新增** `calculate_information_ratio`:
```python
def calculate_information_ratio(excess_returns: list[float], fund_name="Unknown") -> float | None:
    """IR = mean(e)/std(e,ddof=1)*sqrt(12)。n<2 或 std=0 返回 None。"""
    n = len(excess_returns)
    if n < 2: return None
    mean_e = sum(excess_returns) / n
    variance = sum((e - mean_e)**2 for e in excess_returns) / (n - 1)
    std_e = variance ** 0.5
    if std_e == 0: return None
    return mean_e / std_e * (12.0 ** 0.5)
```

**改造** `calculate_max_drawdown` -> `calculate_max_drawdown_detail`（返回回撤+恢复详情）:
```python
def calculate_max_drawdown_detail(nav_series: list[float], fund_name="Unknown") -> dict:
    """返回 {max_drawdown, recovery_months, recovered}。
    recovery_months = trough 后首个 NAV>=前峰 的索引差（索引差即月数）；未恢复则 = 末月索引差，recovered=False。
    无回撤(max_dd==0)时 recovery_months=None, recovered=True。"""
```
保留标量 `calculate_max_drawdown` 作 thin wrapper（减少测试扰动）。

**改造** `calculate_max_consecutive_underperform`（决策 5）: 签名改 `list[float|None]`，遇 `None` 中断 streak:
```python
def calculate_max_consecutive_underperform(excess_aligned: list[float|None]) -> int:
    max_run = 0; current = 0
    for e in excess_aligned:
        if e is None: current = 0; continue        # RBA缺失 -> 中断
        if e <= 0: current += 1; max_run = max(max_run, current)
        else: current = 0
    return max_run
```

**改造** `compute_all_metrics`（拆分 n_fund/n_excess，rf 允许 None，双序列）:
- 签名: `compute_all_metrics(returns: list[float], rf_rates: list[float|None], fund_name="Unknown") -> dict`
- 长度守卫 `len(returns)==len(rf_rates)`（rf 可含 None）。
- 双序列:
  ```python
  excess_aligned_orig = [r - (rf/12.0) if rf is not None else None for r, rf in zip(returns, rf_rates)]
  excess_orig = [e for e in excess_aligned_orig if e is not None]   # 压缩
  n_fund = len(returns); n_excess = len(excess_orig)
  ```
- **基金口径**（`returns` 全序列 + `n_fund`）: 年化收益率、最大回撤(+恢复月数)、年化波动率、自相关/去平滑。
- **超额口径**（`excess_orig` 压缩 + `n_excess`）: 年化超额收益、IR、超额胜率。**年化超额收益的 n 必须用 `n_excess`**（当前用 n_fund，RBA 缺失时错——PDD 未写但确实存在的口径 bug）。
- **最长跑输**: 用 `excess_aligned_orig`（未压缩，决策 5）。
- 返回 dict 键（与 FundMetric 列严格对齐）:
  - 删: `orig_omega_ratio`, `un_omega_ratio`
  - 增: `orig_information_ratio`/`un_information_ratio`(float|None), `orig_annualized_return`/`un_annualized_return`(float), `orig_recovery_months`/`un_recovery_months`(int|None), `orig_dd_recovered`/`un_dd_recovered`(int 0/1), `excess_sample_months`(int)
  - `history_months` 仍 = n_fund。
- 年化收益率: `comp_raw=∏(1+r)` -> `calculate_annualized_return(comp_raw, n_fund)`（r>-1，comp_raw 恒正）。
- IR 校验锚点: 单测 `|mean(excess_orig)*12 - orig_annualized_excess_return| < 0.003`。

### 1.2 `webapp/backend/app/crud.py`
`resolve_rf_rates` 改返回 `(list[float|None], missing_dates)`，不再抛错（scoped 例外注释留痕）。`replace_anomalies` 接收两类异常，`type`/`reason` 透传，RBA 缺失行数值字段 None。

### 1.3 `webapp/backend/app/metrics_pipeline.py`
`compute_and_store_metrics`: 基金缺口仍 `_find_month_gaps` 零容忍；`rf_rates, missing_rba_dates = resolve_rf_rates(...)`；`compute_all_metrics(returns, rf_rates, ...)`；合并异常 `mad(type='return_outlier') + rba(type='rba_missing', reason='RBA 现金利率缺失，该月已从超额序列剔除')` -> `replace_anomalies` -> `upsert_metrics`。

### 1.4 `webapp/backend/app/models.py`
- **FundMetric**: 删 `orig/un_omega_ratio`；增 `orig/un_information_ratio`(Float,nullable), `orig/un_annualized_return`(Float), `orig/un_recovery_months`(Integer,nullable), `orig/un_dd_recovered`(Integer), `excess_sample_months`(Integer)。
- **Anomaly**: 增 `type`(String, default='return_outlier'), `reason`(String,nullable)；`value`/`z_score`/`threshold_sigma`/`mean`/`stdev` 改 nullable=True。

### 1.5 `webapp/backend/app/schemas.py`
`AnomalyResponse` 增 `type`/`reason`，数值字段 Optional。`sanitize_for_json` 注释更新（Omega 已移除，保留通用 inf/NaN->None 防御）。

### 1.6 `webapp/backend/app/routers/metrics.py` & `anomalies.py` & `funds.py`
- `_recompute_for_slice`: 用新 `resolve_rf_rates`（带 None），`compute_all_metrics` 接受 None。**切片路径不写 RBA 异常**（决策见修正 4 / 第 5 节 R1）。
- `compare` full 路径读缓存含新列；布尔字段转 bool 补 `orig/un_dd_recovered`。
- `list_anomalies` 透传 `type`/`reason`，RBA 缺失行 `monthly_return_id=None`。

---

## 2. 数据库迁移（含修正 1 备份+校验）

新增 `webapp/backend/migrate_phase1.py`:
```
0. timestamp = ...; shutil.copy("data/fund_analysis.db", f"data/fund_analysis.db.bak-{timestamp}")  # 修正1: 先备份
1. engine = create_engine(DATABASE_URL)
2. DROP TABLE IF EXISTS fund_metrics, anomalies        # 仅派生缓存
3. Base.metadata.create_all(bind=engine)               # 新 schema 重建
4. for fund in get_all_funds(): compute_and_store_metrics(session, fund.fund_id)
5. 校验: count(fund_metrics) == count(funds)；不等则报错并提示从 .bak 恢复（不假设成功，验证它）
```
- 源数据三表 + `ai_reports` 保留。
- README 补"Phase 1 升级先跑 `python3 migrate_phase1.py`"。
- 测试库（conftest 内存库）每次 `create_all` 自动新 schema，无需迁移。

---

## 3. 前端改动

### 3.1 `types/index.ts`
`FundMetrics`: 删 omega；增 IR/年化收益率/恢复月数/dd_recovered/`excess_sample_months`（类型见决策 3/4）。
`Anomaly`: 增 `type`/`reason`，数值字段改 `number|null`。

### 3.2 `store/useStore.ts`
增 `displayFundId` + `setDisplayFundId`；不在 `selectedFundIds` 时回退 `selectedFundIds[0]`（修 PDD 1.6 一致性 bug，Phase 2 锚定上线后删）。不改 chartMetric/NavChart。

### 3.3 `pages/Dashboard.tsx`
- 四张卡片: 年化超额收益、信息比率、超额胜率、最大回撤（副文本恢复月数）。删 Omega 卡。
- 数据源统一 `displayFundId`。
- IR/超额胜率: `excess_sample_months<24` 名次置灰 + 角标 "样本不足(n=X)，统计指标不可靠"。
- **未恢复/无回撤显示（修正 3 统一口径）**: `max_dd==0` -> 卡片副文本与表格均显示"无回撤"；`recovered=False` -> 两处均"未恢复(已X个月)"；`recovered=True` -> 两处均"恢复X个月"。
- **IR 为 null（n<2 或 std=0）显示 "-"，不得空白或 NaN（修正 3）**。

### 3.4 `components/MetricCard.tsx`
增 `subtext?`（恢复月数）、`warn?`+`warnNote?`（小样本角标）。`rank` 小样本时父组件传 `undefined`+`warn`。

### 3.5 `components/CompareTable.tsx`（PDD 1.4 列结构）
```
基金名称 | 年化收益率 | 年化超额收益(排名) | 信息比率(排名) | 最大回撤(排名)·恢复月数 | 超额胜率(排名) | 最长跑输 | 年化波动率
```
- 年化收益率: 无名次、无角标。
- 信息比率/超额胜率: `rankBy` 增 `eligible` 谓词，仅 `excess_sample_months>=24` 参与排名 1..k；小样本显示值+置灰角标，rank=undefined。IR null 显示"-"。
- 最大回撤: `-0.53% (3) · 恢复2个月` / `· 未恢复(已5个月)` / `· 无回撤`（与卡片统一）。
- 年化超额/最长跑输/年化波动率: 排名不受 n<24 限制。

### 3.6 `components/AnomalyTable.tsx`
增"类型"徽标；RBA 缺失行数值列"-"、显示 `reason`、纠错按钮禁用。

---

## 4. 测试计划（CLAUDE.md 三、3.2: 先单测后端到端）

### 后端单测（`cd webapp/backend && python3 -c "import pytest; pytest.main(['tests/','-v'])"` 绕 RTK）
- `test_calculations.py`: 删 `test_calculate_omega_ratio`；新增 `test_calculate_information_ratio`（公式/n<2->None/std=0->None）、`test_calculate_max_drawdown_detail`（已知回撤+恢复序列）、`test_ir_validation_anchor`（mean(e)*12 vs 几何年化 <0.3pp）、`test_max_underperform_rba_missing_breaks_streak`（决策 5: 跑输-None-跑输 不拼成连续）、`test_compute_all_metrics_rba_missing`（rf 含 None -> 剔除于超额、`excess_sample_months` 减少、基金口径用全序列、年化超额 n=n_excess）；更新 `test_compute_all_metrics_*`（IR/年化收益率/恢复月数，去 omega）。
- `test_metrics_pipeline.py`: RBA 缺失月 -> `rba_missing` 异常落库 + 计算继续；基金缺口仍抛错。
- `test_anomaly.py`/`test_api_anomalies.py`: 两类异常 type/reason。
- `test_models.py`/`test_crud.py`: 更新 `_minimal_metrics`（去 omega，加新字段）。
- `test_api_metrics.py`: compare 返回字段断言。

### 端到端
单测全绿 -> `migrate_phase1.py`（含备份+校验）-> 启动后端 -> `npm run dev` 手工核对验收清单。

---

## 5. Phase 1 验收清单映射（PDD 1.验收标准）

| 验收项 | 实现/验证位置 |
|---|---|
| Omega 卡片/表格/排序完全消失 | 全删 + grep 无残留 |
| IR 通过校验锚点 | `test_ir_validation_anchor` + 手工 |
| Bentham 回撤恢复月数人工核对 | `calculate_max_drawdown_detail` + 手工对月度数据 |
| 未恢复回撤显示"未恢复(已X个月)" | detail recovered=False + 前端统一口径 + 单测 |
| 年化收益率列无名次括号 | CompareTable 该列无 rank |
| 近1年 n=12 -> IR/胜率名次置灰+角标 | 前端 `excess_sample_months<24` 门禁 |
| 挖掉基准月份 -> 剔除+异常审计有记录 | `test_compute_all_metrics_rba_missing` + pipeline 异常落库 |

**修正 4 验收追加项**: 切到"近1年"，确认异常审计页仍能看到窗口内 `rba_missing` 记录（若构造了含 RBA 缺失月的近1年测试数据）。不变量: 切片可见的 RBA 缺失月必为 full 历史子集，full 路径已写库覆盖一切切片场景。

---

## 6. 禁止事项遵守（PDD 第 3 节）
1. 禁插值/外推/回填: RBA 缺失剔除非填充 ✓；超额序列无 fill。
2. 禁拼接序列算回撤: Phase 1 无拼接，回撤用各基金自身 NAV ✓。
3. 禁锚定模式用 NAV 绝对值: Phase 1 无锚定 ✓。
4. 禁 y 轴锚 0: Phase 1 不改 NavChart ✓。
5. 禁视觉平滑改数据 ✓。
6. 禁给年化收益率排名 ✓。
7. 异常写审计禁静默: RBA 缺失必写 `rba_missing`；最长跑输禁静默拼接（决策 5）✓。

---

## 7. 风险与待确认
- **R1 切片路径不写 RBA 异常（修正 4 接受）**: 异常仅由 full/recompute 的 `compute_and_store_metrics` 写。切片可见的 RBA 缺失月是 full 子集，已覆盖。验收追加项见第 5 节。
- **R2 `calculate_max_drawdown` wrapper**: 保留 wrapper 减少测试扰动；新逻辑用 detail 版，确认 wrapper 行为不变。
- **R3 真实 DB 迁移**: `migrate_phase1.py` 含备份（修正 1），重算后行数校验。重算非破坏性、可从 bak 恢复。
- **R4 IR 的 un 变体**: 去平滑后 std 增大，un_IR 通常 < orig_IR；std=0 边界返回 None，前端显示"-"。

---

## 8. 实施 Checklist（修正 3、4）
- [ ] **修正 3a**: max_dd==0 时卡片副文本与表格均显示"无回撤"；recovered=False 时两处均"未恢复(已X个月)"；recovered=True 时两处均"恢复X个月"。
- [ ] **修正 3b**: IR 为 null（n<2 或 std=0）显示"-"，不得空白/NaN。
- [ ] **修正 4**: 验收时切"近1年"确认异常审计可见窗口内 `rba_missing` 记录。
- [ ] 修正 1（迁移备份+行数校验）与修正 2（最长跑输未压缩序列）已并入决策 2/5，实施时核对。

---

## 9. Phase 2 路线图（概要，详细方案待 Phase 1 验收后出）
- **状态机 A/B/C** + rebase 引擎: store 增 `chartState`；rebase 纯函数化（B 窗口起点统一 1.0；C 拼接 rebase，后发基金起点 = 锚定曲线上一月值，空心圆标注拼接点）。
- **y 轴**: 永不锚 0，[min,max]±5% padding；绘图区 ≥480px。
- **回撤曲线**: NAV 图下方共享 x 轴，基于各基金自身序列（锚定下后发基金从发行月起算）。
- **滚动 12 月超额图**: 新增图表类型，缺数据->null 断开；<12 月基金缺席置灰。
- **月度超额热力图**: 锚定基金详情，发散色标，缺月灰格。
- **删除月收益率折线**: 移除 NavChart 的 `excess_return`（实为月收益率）选项。
- **缺失渲染通则**: 折线断开、tooltip"无数据"、禁平滑。
- 数据流: time-series 端点扩展返回逐月 `excess`（fund 与 RBA 对齐，缺失 null）供热力图/滚动超额；rebase/回撤/滚动超额计算放前端纯函数。
- Phase 2 详细 plan 重点审: 状态转移完备性、拼接公式实现。
