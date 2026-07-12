# 项目审查与修复计划

## 背景
阶段 4-5（前端 + SQLite 迁移）由低端模型（Haiku）完成，遗留多处 bug、数据完整性隐患与 UI 缺陷。三方深度审查（后端计算 / 前端代码 / UI 静态分析）共发现 **40+ 问题**。本计划按优先级分批修复，每批改完跑测试验证。

---

## P0 — 数据完整性致命 bug（立即修，触碰 CLAUDE.md 红线）

### P0-1 异常纠错用错 ID，会改写无关基金数据【CRITICAL】
- **位置**: `frontend/src/components/AnomalyTable.tsx:104`、`store/useStore.ts:122`、`api/client.ts:40`
- **问题**: `a.id` 是 `anomalies` 表主键，却被当 `monthly_returns.id` 传给 `PATCH /api/monthly-returns/{row_id}`。两表独立自增，低 ID 几乎必然都存在 → 点"纠错"命中随机基金/随机月份，写入错误值后触发 `recompute_nav` + `compute_and_store_metrics`，污染该基金全部 NAV/指标。
- **修复**: 后端 `AnomalyResponse` 增加 `monthly_return_id` 字段（`list_anomalies` 按 `(fund_id, date)` 关联查出），前端改用该字段。同步修 P0-2。

### P0-2 异常纠错输入单位错位，会写出 100 倍错误值【CRITICAL】
- **位置**: `AnomalyTable.tsx:80,97-101,116-118,25-35`
- **问题**: 异常 `value` 是小数（0.05），表格显示"5.00%"，但纠错输入框 `step=0.0001`、不预填、无单位提示，PATCH 直接当 `net_return` 发。用户看到 5.00% 自然输入"3"→ `net_return=3.0`（月收益 300%），污染整条 NAV。
- **修复**: 输入框以百分比录入（预填 `a.value*100`，标注"%"），提交时 `val/100` 转小数；加范围校验 `|val| < 1`（小数）拒绝极端值。

### P0-3 RBA 缺失月份静默用 0.0435 回填【HIGH，违反第一条】
- **位置**: `crud.py:81`、`metrics_pipeline.py:65-66`、`routers/metrics.py:42`
- **问题**: `resolve_rf_rates` 对 DB 缺失月份用硬编码 `0.0435` 填充，不报错。历史月份 RBA 缺失（如真实 0.10%）用 4.35% 扣减，超额收益被低估 ~0.35%/月（年化 ~4.2%），且完全静默。违反"数据缺失必须报错并停止"。
- **修复**: `resolve_rf_rates` 遇缺失月份抛 `ValueError`（列缺失月份），上层 compare/recompute 转 422/400；移除 `0.0435` 硬编码，fallback 仅限"当前未完整月"且显式标注。

### P0-4 fund_metrics 缓存失效，skills 摄取新月份后显示过期指标【HIGH】
- **位置**: `skills/lib/db.py:103-129`、`routers/metrics.py:71-77`、`routers/funds.py:20`
- **问题**: skills `upsert_monthly_return` 只重算 NAV 不触达 `fund_metrics`。`compare?period=full` 读缓存 → 返回旧指标；`GET /api/funds` 的 `data_cutoff_month` 显示过期月份。3y/1y 走即时重算（新鲜），full 走缓存（过期），同基金不同 period 不自洽。
- **修复**: compare full 端点比较 `fund_metrics.date_period` 与 `max(monthly_returns.date)`，不一致时回退即时重算；`data_cutoff_month` 直接取 `max(monthly_returns.date)`。

---

## P1 — 高优先级 bug

### P1-1 SmoothingCards 显示 fund_id 而非 fund_name
- **位置**: `SmoothingCards.tsx:83`（`m.fund_name ?? m.fund_id`，compare 不返回 fund_name）
- **修复**: 仿 `CompareTable.tsx` 用 `useStore(s=>s.funds)` 建 `fundNameMap`；`m: any` 改 `FundMetrics`。

### P1-2 最大回撤排名方向相反
- **位置**: `CompareTable.tsx:42-46`（`rankDD asc=true`，rank1=最差）vs `Dashboard.tsx:113-117`（`asc=false`，rank1=最好）
- **修复**: 统一 rank1=最好。回撤最好=值最大（最不负）→ 降序，`CompareTable.rankDD` 去掉 `true`。

### P1-3 fetchFunds 每次重置 selectedFundIds，切换页面丢失选择
- **位置**: `useStore.ts:67`（每次覆盖为全部）；Dashboard/Anomalies/FundManagement mount 都调
- **修复**: `fetchFunds` 仅首次（selectedFundIds 为空）初始化全选；后续只 reconcile（保留交集 + 剔除已删除）。

### P1-4 compare 端点缺口/无数据抛 500 而非 422
- **位置**: `routers/metrics.py:75,79`（`_recompute_for_slice` 抛 ValueError 未捕获）
- **修复**: compare 循环 try/except ValueError → `HTTPException(422, ...)`，与 recompute/PATCH 一致。

### P1-5 compute_all_metrics 无长度守卫，zip 静默截断致年化错误
- **位置**: `calculations.py:149`（zip 截断）、`:189`（n=len(returns) 不变）
- **修复**: 入口断言 `len(returns)==len(rf_rates)`，不等抛 ValueError。

### P1-6 极端负收益触发复数年化超额收益
- **位置**: `calculations.py:13,152-160`（`comp<0` 时 `**`(12/n) 返回复数）
- **修复**: 年化前检查 `comp<=0`，记异常标记返回 None（sanitize_for_json 已处理 None）。

### P1-7 anomaly 把 0.0 收益当缺失忽略
- **位置**: `anomaly.py:20-21,35-36`（过滤 `!=0.0`）
- **修复**: 移除 `!=0.0` 过滤；0.0 是合法值。同步改测试 `test_detect_anomalies_zero_returns_ignored`。

### P1-8 anomaly 字段 `mean` 实为中位数
- **位置**: `anomaly.py:44`、前端 `AnomalyTable.tsx:89` 列标题"均值"
- **修复**: 字段重命名 `median`（后端模型 + schema + 前端类型 + 表头），或前端表头改"中位数"。（选后者，改动小）

---

## P2 — 中优先级

### P2-1 scheduler/refresh 抓的当前 RBA 不落库
- `scheduler.py:23-27`、`routers/rba.py:17-22`：`fetch_current_rba_rate()` 只返回不写入。修复：以当月 key upsert 进 `rba_cash_rates`。

### P2-2 布尔字段 int 0/1 vs 前端 boolean 类型不符
- `models.py:78,80,99`（Integer）+ `types/index.ts:19,21,35`（boolean）。修复：后端序列化统一转 bool（与 time-series 一致）。

### P2-3 NavChart "月超额收益"实为月总收益
- `NavChart.tsx:50-58,123`：用 NAV 环比算总收益，未扣 RBA。修复：label 改"月收益率"（后端 time-series 不含 RBA，无法算真超额）。

### P2-4 异常表 Z-Score 负向极端值不高亮
- `AnomalyTable.tsx:81-87`：`z_score>=3` 不捕获负值。修复：用 `Math.abs(a.z_score)`。

### P2-5 Omega 为 null（最优）在 CompareTable 排末名
- `CompareTable.tsx:10-18`：null 当 -Infinity。修复：null omega 排首位或显示"-"。

### P2-6 排名两套实现不一致（rankAmong vs rankBy）
- Dashboard `rankAmong`（竞争排名，null 返回 undefined）vs CompareTable `rankBy`（顺序排名，null 当 -Inf）。修复：抽共用 rank 工具，统一 null/方向/并列语义。

### P2-7 store 静默吞错，无 error 状态
- `useStore.ts:81-83,93-95,103-105`：catch 只清 loading。修复：增加 `compareError`/`timeSeriesError`/`fundsError` 状态，UI 展示；失败清空陈旧数据。

### P2-8 patchMonthlyReturn 后不刷新 metrics/time-series
- `useStore.ts:122-125`：只 fetchAnomalies。修复：补 `fetchCompare()` + `fetchTimeSeries()`。

### P2-9 取消全选后不清空陈旧数据 + 请求竞态
- `useStore.ts:76,88`（空选直接 return 不清空）；无 AbortController。修复：空选时 `set({compareData:null,timeSeriesData:null})`；引入请求序号丢弃过期响应。

### P2-10 异常审计页 SmoothingCards 不随选择刷新
- `Anomalies.tsx:18-22`：effect 依赖仅 `[funds.length]`。修复：加 `selectedFundIds`，或复用 Dashboard 的 compareData。

### P2-11 RBA 日频数据但测试 mock 月频
- `rba.py:45-49`（日频→月度字典覆盖取月末）+ `test_rba.py` mock 月频。修复：测试改日频 mock；显式选月内代表性利率并注释。

---

## P3 — 低优先级 + UI 改进

### 后端 LOW
- L1: `upsert_rba_rates` 返回值命名 `upserted` 实为"新增数"→ 重命名 `inserted`。
- L2: 过期注释引用不存在的 `scripts/metrics.py` → 更新或删除。
- L3: `detect_anomalies` 列表构造不防 None → 加 `is not None` 守卫。
- L4: `anomaly.py` 与检测循环守卫不一致。

### 前端 LOW
- NavChart tooltip 对 null 显示"0.0000" → 显示"-"。
- NavChart y 轴收益率模式显示小数而非百分比 → `v=>(v*100).toFixed(2)+'%'`。
- FundManagement 添加表单无 APIR 客户端校验、缺 max_pdf_pages 字段 → 加即时正则校验。
- deleteFund 不重拉 compare/time-series → 补刷新。
- Dashboard effect 读 funds.length 未列入依赖 → 补依赖。

### UI 设计改进
- U1: **MetricCard 不显示当前基金名** → 卡片组上方加"当前展示：{fund_name}"标题，或卡片内显示基金名。
- U2: **FundManagement 添加基金弹窗误导** → 表单只注册元信息不抓取数据，但 UI 让用户以为能添加完整基金。改为明确提示"仅注册元信息，数据抓取请在 skills 运行 /add_fixed_fund"，或移除该弹窗改为跳转指引。
- U3: **fetch_method 选项值不一致**（pdf/html_plotly vs skills 的 html/pdf vs DB 的 requests+BeautifulSoup）→ 统一枚举值。
- U4: **NavChart 高度 320px 偏小** → 增至 400px，趋势可读性更好。
- U5: **表格列宽无控制** → 长字段（基金名/URL）加 `max-w` + `truncate`。
- U6: **空状态文案不统一** → 统一空状态组件（图标 + 文案 + 引导动作）。
- U7: **无 loading skeleton** → 关键区域用骨架屏替代纯文字"加载中"。
- U8: **AnomalyTable 编辑态 inline span 布局跳动** → 编辑态用固定宽度容器。
- U9: **Sidebar v0.1** → 更新版本号或移除。
- U10: **顶部 controls 下拉样式简陋** → 统一 select 样式，加图标。
- U11: **无响应式适配** → Sidebar 在窄屏可折叠（非必须，看用户需求）。

---

## 执行顺序与验证

### 批次 1: P0 数据完整性（最高优先）
1. 修 P0-1 + P0-2（异常纠错 ID + 单位）— 后端 AnomalyResponse 加字段 + 前端改 ID 源 + 百分比录入
2. 修 P0-3（RBA 缺失报错）— resolve_rf_rates 抛错 + 移除硬编码
3. 修 P0-4（缓存失效）— compare full 校验新鲜度 + data_cutoff_month 取 max
4. 跑后端 pytest 全套 + 手动 curl 验证

### 批次 2: P1 高优先级
5. P1-1 ~ P1-8 逐项修复
6. 跑前后端测试 + tsc 编译

### 批次 3: P2 中优先级
7. P2-1 ~ P2-11
8. 测试验证

### 批次 4: P3 + UI
9. LOW 修复 + UI 改进 U1-U11
10. 插入测试数据截图验证 UI

### 验证标准
- 后端 pytest 全绿（修测试以反映正确行为，非掩盖）
- 前端 `tsc --noEmit` 零错误
- 插入测试基金数据，E2E 验证：添加→recompute→对比→异常纠错（用正确 ID + 单位）→图表对齐
- 重新通过 skills 添加 1 只真实基金验证全链路

---

## 不做的事
- 不重写架构，仅修 bug + UI 改进
- 不自动纠正任何已入库数据（CLAUDE.md 第五条）
- skills 端提取逻辑已是 LLM 驱动（非旧正则），Stake 类 bug 不再复现，本轮不动 skills 提取
