# 对比看板图表与交互系统重构 - Phase 2 实施计划（定稿 v3）

> 依据: PDD 第 2 节。前置: Phase 1 已验收。
> 已批准（方案a + R2 + 修正A + 修正B），直接实施，不再送审。

## 0. 架构（方案a）
time-series 永远返回 full；period/anchor 切换纯前端重算不 refetch（修复滚动超额在固定窗口只剩孤点的数据架构 bug）。后端扩展 time-series 增 `returns`/`unsm_returns`/`rba`。前端 `src/lib/rebase.ts` 纯函数承揽 rebase/回撤/滚动超额/热力图。store 增 `anchorFundId`，删 `displayFundId`。smoothingMode 保留。

## 1. 状态机
### 1.1 状态（由 period + anchorFundId 决定）
| 状态 | period | anchor | rebase | x 轴 |
|---|---|---|---|---|
| A 全部区间 | full | null | 各基金自发行首月=1.0 | 全历史并集 |
| B 固定窗口 | 1y/3y/common | null | 各基金窗口内首月=1.0 | 窗口（前端裁剪 full） |
| C 锚定 | 置灰 | 某基金 | 拼接（§3） | `[t_A, max(所有已选基金末月)]`（修正2） |

### 1.2 转移表
| 当前 | 触发 | 目标 | 副作用 |
|---|---|---|---|
| A | period->1y/3y/common | B | 前端裁剪；无 time-series refetch |
| B | period->full | A | 前端重算 |
| B | period 互切 | B | 前端重算 |
| A/B | 点击曲线 X | C | anchor=X，存 prevPeriod；**compare refetch period=full（修正A）**；period 置灰 |
| C | 再点同一锚定 | 恢复 prevPeriod A/B | anchor=null；**compare refetch period=prevPeriod（修正A）** |
| C | 点另一曲线 Y | C（切换锚定） | anchor=Y；前端重算；**无 refetch（compare 已 full）** |
| C | period 选择器 | - | 不响应，tooltip"锚定模式下展示锚定基金完整历史" |
| C | 锚定基金被 chips 移除 | 恢复 prevPeriod A/B | anchor=null；compare refetch prevPeriod |
| 任意 | chips 增删基金 | 同状态 | refetch time-series + compare（选中集变） |

**refetch 汇总**：time-series 仅在 selectedFundIds 变（恒 full）；compare 在 selectedFundIds 变、period 变、进入/退出 C 时 refetch，`effectiveComparePeriod = anchorFundId ? 'full' : period`。C 态下卡片/表格/图表三者同口径=锚定基金完整历史（修正A 消除"图说全历史、卡片说近1年"的口径打架）。

### 1.3 不变量
anchor!=null ⇒ period 置灰、x 轴=锚定完整历史、卡片/热力图联动锚定、compare 用 full。anchor==null ⇒ 卡片占位"点击曲线锚定基金查看详情"，"当前展示"标签隐藏；CompareTable 始终全基金对比。状态由 (period,anchor) 唯一决定。

### 1.4 store
新增 `anchorFundId`/`prevPeriod`/`setAnchor`；删 `displayFundId`；`chartMetric:'nav'|'rolling_excess'`（删 excess_return，PDD2.7）；`fetchTimeSeries` 依赖 `[selectedFundIds]` 恒 full；`fetchCompare` 依赖 `[selectedFundIds, effectiveComparePeriod]`。

## 2. 数据流
扩展 time-series 响应（period 参数保留，前端恒传 full）：
```jsonc
{"period":"full","months":[...],"rba":[0.015,null,0.0435,...],
 "series":[{"fund_id","fund_name","dates":[...],"returns":[...],"unsm_returns":[...]|null,"is_geltner_applied"}]}
```
`returns`/`unsm_returns` 由现有 r_slice/unsm 直接返回；`rba` 按 months 查 rba_cash_rates 缺失置 null。保留 orig_nav/unsm_nav 向后兼容。

## 3. 拼接 rebase（R2 裁决）
- `buildNav(returns, base=1.0)`: nav[i]=base×∏(1+r_s)。
- A/B: `rebasePlain(fund)=buildNav(returns,1.0)`；B 窗口前端裁剪。
- C: 锚定 A，t_A=A.dates[0]。
  - A: buildNav(A.returns,1.0)。
  - 早于 A 的 Y（t_Y<t_A）: 裁剪 t_A 前，buildNav(trimmed,1.0)（同起 1.0@t_A）。
  - 晚于 A 的 X（t_X>t_A）: base_X=V_A(t_X−1)；nav=buildNav(X.returns,base_X)，首点=base_X×(1+r_X,t_X)。**拼接点**空心圆 4px 于 (t_X,nav[0])，tooltip"自{t_X}起加入对比，承接锚定基金上月累计值"（R2 改措辞）。**不加幻影点**。
  - t_X==t_A: buildNav(returns,1.0)，无拼接点。
- base_X=A.nav[A.dates.indexOf(t_X−1)]（t_X−1∈[t_A,A末月]，A 无缺口）。边界 t_X−1>A末月 ⇒ X 降级 base=1.0 自 t_X 起，tooltip 注"锚定基金在此前已结束，未拼接"，log()。
- **断言**: base_X==V_A(t_X−1) 浮点级。
- 回撤永远走 rebasePlain（非拼接）；C 态后发基金从自身发行月起算。
- `monthlyBench(rba_t)=rba_t/12`（修正B，单一工具，热力图+滚动超额共用）。

## 4. 图表
- NavChart(nav): 上 NAV + 下回撤共享 x 轴（双 grid+dataZoom 或 echarts.connect）；NAV≥480px 回撤~200px；rebase 按 A/B/C；C 态非锚定 35% 透明、锚定 2.5px 其余 1.5px；拼接点空心圆；y 轴 min/max=数据±5% **永不 min=0**；connectNulls=false；移除 smooth。
- RollingExcessChart(rolling_excess): t 月，[t−11,t] 连续 12 月均有收益**且 RBA** 时 `∏(1+r_fund)−∏(1+monthlyBench(rba_t))`（修正B）；任一月缺失（含 RBA null，修正1）⇒ 含该月 12 窗口全 null（不跳过凑11）；<12月基金缺席图例置灰；y auto-scale 0 轴线；**方案a：full 序列算后按窗口裁剪显示 ⇒ 近1年曲线完整**。
- ExcessHeatmap: 仅 anchor!=null 渲染于 CompareTable 下；行=年倒序列=1..12；`e_t=r_fund−monthlyBench(rba_t)`（与 Phase 1 口径一致）；0 中心发散色标 [−m,+m]；缺月灰格 tooltip"无数据"禁插值；tooltip 年月/原始月收益/基准月收益/超额。
- 删月收益率折线（PDD2.7）：chartMetric 移除 excess_return。

## 5. 验收映射
B 近1年曲线从1.0、y贴合（rebasePlain+y±5%）｜A y 不含0｜锚定 Bentham 拼接+空心圆+tooltip｜锚定 Stake 早发统一起点1.0+裁剪｜period 置灰+取消恢复｜**base_X==V_A(t_X−1) 单测**｜回撤 x 同步+后发自起算 单测｜滚动 <12月缺席+置灰、**近1年曲线完整（方案a证据）**｜挖掉一月：折线断+热力图空格+滚动12窗 null（修正1）｜月收益率折线消失｜卡片随锚定+删旧逻辑+占位｜**锚定任一基金卡片 n == "当前展示"月数（修正A证据）**。

## 6. 测试
- rebase.ts 单测（vitest）：buildNav、rebase A/B/C（早发trim/晚发splice/同期/晚于锚定结束降级）、base_X==V_A(t_X−1)、drawdown（C后发自起算）、monthlyBench（恒定 rba=0.036 ⇒ 12月基准累计≈(1.003)^12−1≈3.66%，修正B）、rollingExcess（RBA null⇒12连续null、<12月缺席、近1年窗口曲线完整）、monthlyExcess。
- 后端：time-series 扩展字段单测（returns/unsm_returns/rba、rba 缺失 null）。
- 前端 tsc+vite build；手工验收。

## 7. 实施顺序
1. 后端 time-series 增 returns/unsm_returns/rba + 单测。
2. 前端 lib/rebase.ts 纯函数 + 单测（公式先于像素）。
3. store 状态机（anchorFundId/prevPeriod、删 displayFundId、chartMetric 改型、effectiveComparePeriod、fetchTimeSeries 恒 full）。
4. NavChart（rebase A/B/C + y 轴 + 拼接点 + 回撤双图）。
5. RollingExcessChart + ExcessHeatmap。
6. 卡片/标签联动锚定 + 占位；删月收益率折线。
7. 验收自查（重点：近1年滚动超额曲线完整、拼接点断言、RBA null 12点传染、锚定卡片 n==当前展示月数）。
