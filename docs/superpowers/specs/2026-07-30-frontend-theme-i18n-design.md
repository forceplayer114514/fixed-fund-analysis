# 前端装修：设计 token 体系 + 深色模式 + CN/EN 双语

日期：2026-07-30
范围：`webapp/frontend` 全部展示层。不改 `webapp/backend`，不改 `llm_ingest`，不改任何计算逻辑。

---

## 1. 目标与非目标

### 目标

1. 全局 UI 美化，风格定为**专业金融数据台**：中性冷灰 + 单一强调色，数字等宽右对齐，
   卡片弱阴影强边框，信息密度偏高。
2. 深色模式，三态切换：跟随系统 / 亮 / 暗，选择持久化。
3. 全局语言切换 CN / EN。

### 非目标（明确不做）

- 不改数据、不改指标算法、不改 API 契约。
- 不引入运行时依赖（不引 shadcn/ui、Radix、react-i18next、图标库）。
  devDependencies 也不新增（不引 `@testing-library/react`，测试保持纯逻辑）。
- 后端返回的中文消息（HTTP `detail`、摄取 job 进度日志、计算侧提示）**不翻译**，
  原样透传，仅在前端外层包一个中立标签（如 `Backend message:`）。
- 不做机翻。新功能的英文文案由做那个功能时人工撰写；金融口径词
  （去平滑 / 超额胜率 / 恢复月数）译错比不译危害更大。

---

## 2. 现状事实（改造前基线）

- 栈：React 18 + Vite 5 + Tailwind 3（`theme.extend` 为空，零定制）+ zustand 4 +
  ECharts 5（`echarts-for-react/lib/core`，按需注册）。
- 3 个路由页：`/`（对比看板）、`/anomalies`（异常审计）、`/funds`（基金管理）。
- 13 个组件，`src` 合计约 3225 行。
- 硬编码颜色 26 处，分布：
  - `NavChart.tsx`：8 色序列 `COLORS`、锚定线 `#000`、基准线 `#bbb`、
    轴标签 `#999`、分割线 `#f0f0f0`、拼接点描边 `#555`
  - `RollingExcessChart.tsx`：同一份 `COLORS` 副本 + `#000` / `#bbb` / `#999` / `#f0f0f0`
  - `ExcessHeatmap.tsx`：发散色对 `rgba(42,120,214,a)` / `rgba(227,73,72,a)`、
    缺月灰格 `#f0f0f0`
  - `Sidebar.tsx`：`bg-[#1a1a2e]`（永远深墨，浅色模式下也深）
  - `FundManagement.tsx`：3 处 `bg-[#1a1a2e]` 主按钮
- UI 中文文案散落 13 个 `.tsx`/`.ts` 文件，`FundManagement.tsx` 最密
  （742 行 / 19 个 `useState` / 5 块面板）。
- 后端 `webapp/backend/app/*.py` 中面向用户的中文消息确实存在并会透传到前端
  （如 `RBA 现金利率缺失，该月已从超额序列剔除`、摄取 job 逐步骤日志）。
- 现有前端测试仅 `src/lib/rebase.test.ts`（纯计算，本次不动）。

---

## 3. 设计 token 体系

### 3.1 单一真源

`src/index.css` 持有两个 CSS 变量块，是**颜色的唯一真源**：

```css
:root { /* 浅色 */ }
:root.dark { /* 深色 */ }
```

Tailwind 语义色全部映射到变量，组件里只写语义类名，**不写任何 `dark:` 双份类名**。

| token | 浅色 | 深色 | 用途 |
|---|---|---|---|
| `--bg` | `#f6f7f9` | `#0d1117` | 应用画布 |
| `--surface` | `#ffffff` | `#161b22` | 卡片 / 表格底 |
| `--sunken` | `#f1f3f5` | `#1c232c` | 表头 / 空态 / 斑马纹 / 热力图缺月格 |
| `--border` | `#e3e6ea` | `#273040` | 常规边框 |
| `--border-strong` | `#cfd4da` | `#3a4552` | 输入框 / 分组边框 |
| `--fg` | `#14181d` | `#e6edf3` | 主文本 |
| `--fg-muted` | `#6b7480` | `#9aa5b1` | 副文本 |
| `--fg-subtle` | `#9aa3ad` | `#6e7a87` | 轴标签 / 占位 / 禁用 |
| `--accent` | `#0e7490` | `#22b8cf` | 选中 / 激活 / 主按钮 |
| `--accent-soft` | `#e0f2f7` | `rgba(34,184,207,0.14)` | 激活 chip 底 |
| `--pos` | `#157f57` | `#3fb984` | 正超额 |
| `--neg` | `#c0392f` | `#f2685f` | 负超额 / 回撤 |
| `--warn` | `#b45309` | `#e0a03a` | 小样本 / 数据缺口告警 |
| `--grid` | `#eef0f3` | `#21272f` | 图表分割线 |
| `--anchor` | `#14181d` | `#f0f4f8` | 锚定基金曲线（替代硬编码 `#000`） |
| `--series-1..8` | 见 3.2 | 见 3.2 | 多基金曲线 |
| `--heat-pos` | `42 120 214` | `77 155 233` | 热力图正超额基色（RGB 三元组，供 alpha 合成） |
| `--heat-neg` | `227 73 72` | `240 105 100` | 热力图负超额基色 |

热力图基色存成空格分隔 RGB 三元组，因为该组件按超额绝对值算 alpha 后用
`rgba(r,g,b,a)` 合成，需要拿到分量而非完整颜色。

### 3.2 序列色（8 色，两套）

- 浅色：`#2478c4 #6f42c1 #e8590c #2f9e44 #c99a06 #d6336c #0c8599 #9c36b5`
- 深色：`#4dabf7 #a78bfa #ff922b #51cf66 #ffd43b #f783ac #22b8cf #da77f2`

同一序号在两套里保持同一色相，切主题时同一支基金的曲线不换色相，只换明度。

### 3.3 Tailwind 配置

`tailwind.config.js`：

```js
darkMode: 'class',
theme: { extend: { colors: {
  bg: 'var(--bg)', surface: 'var(--surface)', sunken: 'var(--sunken)',
  border: 'var(--border)', 'border-strong': 'var(--border-strong)',
  fg: 'var(--fg)', 'fg-muted': 'var(--fg-muted)', 'fg-subtle': 'var(--fg-subtle)',
  accent: 'var(--accent)', 'accent-soft': 'var(--accent-soft)',
  pos: 'var(--pos)', neg: 'var(--neg)', warn: 'var(--warn)',
} } }
```

注意 Tailwind 内置 `border` 既是颜色名也是工具类前缀；`border-border` 可用，
但类名可读性差，因此组件里统一写 `border border-border`，保持与现有写法一致。

### 3.4 排版与形状

- 数字一律 `font-mono tabular-nums` 且右对齐（现状：比例字体导致表格数字宽度跳动）。
  字体栈 `ui-monospace, SFMono-Regular, Menlo, monospace`，不引 web font。
- 卡片：`shadow-sm` 改为 `1px` 边框；浅色下保留极弱阴影，深色下无阴影（深色下阴影不可见反而糊）。
- 圆角统一：小元素 6px（`rounded-md`），卡片/面板 8px（`rounded-lg`）。
- 表头：11px、大写、`tracking-wide`、`sunken` 底、`sticky top-0`。

---

## 4. 主题机制

### 4.1 状态

zustand store 新增 UI 切片：

```ts
themeMode: 'system' | 'light' | 'dark'   // 用户意图，持久化
resolvedTheme: 'light' | 'dark'          // 派生，供图表读取
setThemeMode(mode): void
```

- `resolvedTheme` = `themeMode === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : themeMode`
- `themeMode === 'system'` 时注册 `matchMedia` 的 `change` 监听，系统切换实时跟随；
  切到 light/dark 时移除监听。
- 持久化：`localStorage['ff.theme']`，读到非法值回退 `'system'`。
  不引 `zustand/middleware/persist`（避免行为耦合），在 setter 里显式写。

### 4.2 应用方式

切换动作只做一件事：`document.documentElement.classList.toggle('dark', resolved === 'dark')`。
颜色全部由 CSS 变量块接管，无需重渲染任何组件（图表除外，见 4.4）。

### 4.3 首帧防白闪

`index.html` `<head>` 内联脚本（在样式表之后、body 之前）：读 `localStorage['ff.theme']`，
非法或 `system` 时查 `matchMedia`，据此给 `documentElement` 加 `dark` class。
脚本不引用任何模块，纯内联，约 6 行。

### 4.4 ECharts 主题接入

ECharts 画布不解析 CSS 变量，需要具体色值。新增：

- `src/theme/chartTheme.ts`：导出 `chartPalettes: Record<'light'|'dark', ChartPalette>`，
  字段涵盖 `series[8]`、`anchor`、`axisLabel`、`splitLine`、`baseline`、`spliceBorder`、
  `tooltipBg`、`tooltipBorder`、`tooltipFg`、`heatPos`、`heatNeg`。
- `src/theme/useChartTheme.ts`：`useChartTheme()` 按 `resolvedTheme` 返回对应调色板。
- `NavChart` / `RollingExcessChart` / `ExcessHeatmap` 的 `option` `useMemo` 依赖数组
  必须加入 `resolvedTheme`（否则切主题图表不重绘）。
- ECharts `tooltip` 默认白底黑字，深色下需显式设 `backgroundColor` / `borderColor` /
  `textStyle.color`；`NavChart` 的 tooltip formatter 返回 HTML 字符串，其内联 style
  也要改用主题色。

**漂移风险与闸门**：此处 CSS 与 TS 存在两份色值。用单测锁死——解析 `src/index.css`
的两个变量块，断言 `chartPalettes` 每个色值都能在对应变量块中找到同名 token
且值一致。任一侧改了另一侧没跟上，测试报红。

---

## 5. i18n 机制

### 5.1 字典与类型闸

- `src/i18n/cn.ts`：`export const cn = { 'nav.dashboard': '对比看板', ... } as const`
- `src/i18n/en.ts`：`export const en: Record<keyof typeof cn, string> = { ... }`
  —— 漏一个 key，`tsc -b` 直接失败。
- key 采用扁平点分命名空间：`nav.*` / `dashboard.*` / `metric.*` / `table.*` /
  `chart.*` / `anomaly.*` / `funds.*` / `common.*` / `error.*`。

### 5.2 取文

- `src/i18n/index.ts`：`translate(lang, key, params?)`，`{name}` 花括号插值。
  未提供的占位符保留原样并在 `import.meta.env.DEV` 下 `console.warn`（不静默）。
- `src/i18n/useT.ts`：`useT()` 从 store 读 `lang`，返回 `t(key, params?)`。
- store 新增 `lang: 'cn' | 'en'`、`setLang()`，持久化 `localStorage['ff.lang']`，
  非法值回退 `'cn'`；切换时同步 `document.documentElement.lang`（`zh-CN` / `en`）。
- 图表内部文案（`起点`、`无数据`、拼接点说明）也走 `t()`，因此 chart option 的
  `useMemo` 依赖需同时含 `lang` 与 `resolvedTheme`。

### 5.3 不翻译的内容

基金名、APIR 代码、基金短码、月份码（`2025-03`）、数值与百分号格式两语言一致。
百分比与小数位不做 locale 化（金融口径固定，避免千分位/小数点歧义）。

### 5.4 中文字面量闸

新增测试 `src/i18n/no-hardcoded-cn.test.ts`：

- 扫描 `src/components/**/*.tsx`、`src/pages/**/*.tsx`
- 剥除注释（`//` 与 `/* */`）后，若仍出现 `[一-龥]` 则失败，
  报出文件与行号
- 豁免：`src/i18n/**`、注释、`src/lib/**`（纯计算模块，注释含中文属正常）
- 目的：保证后续新增专栏/页面天生双语，不靠记性靠 CI 报红

此闸只保证不漏 key，不保证译文质量。

---

## 6. 组件改造清单

| 文件 | 行数 | 改动 |
|---|---|---|
| `index.css` | 8 | 加两个 CSS 变量块 + `body` 基础色 |
| `tailwind.config.js` | 8 | `darkMode: 'class'` + 语义色映射 |
| `index.html` | — | 加防白闪内联脚本 |
| `Layout.tsx` | 13 | `bg-gray-50` → `bg-bg`；主区加最大宽度容器 |
| `Sidebar.tsx` | 36 | 去掉硬编码深墨底，改跟随主题（浅色 `surface` + 右边框，深色更深一档）；导航项加内联 SVG 图标；底部 `v0.1` 行换成控制簇（三态主题分段器 + `CN｜EN`）；文案入字典 |
| `MetricCard.tsx` | 34 | 边框化；数字 `font-mono tabular-nums`；名次灰括号改 badge；`warn` 角标走 `warn` token |
| `FundChips.tsx` | 28 | `cyan-*` → `accent` / `accent-soft` |
| `WarnBadge.tsx` | 20 | token + 文案入字典 |
| `ErrorBoundary.tsx` | 32 | token + 文案入字典 |
| `CompareTable.tsx` | 182 | sticky 表头 + `sunken` 底 + 斑马纹 + 行 hover；数字右对齐等宽；正负值 `pos`/`neg` 着色；锚定行左侧强调边；表头与恢复月数标签入字典 |
| `ExcessHeatmap.tsx` | 107 | 发散色对改两套（读 `--heat-pos`/`--heat-neg`）；缺月灰格 `#f0f0f0` → `sunken`（深色下原值会变亮斑，比有数据的格子更抢眼，与"缺口不得美化"的口径冲突）；图例文案入字典 |
| `RollingExcessChart.tsx` | 108 | 接 `useChartTheme()`；删本地 `COLORS` 副本改共享；文案入字典 |
| `NavChart.tsx` | 227 | 接 `useChartTheme()`；替换 6 类硬编码色；tooltip 深色适配；`起点`/`无数据`/拼接点说明入字典 |
| `SmoothingCards.tsx` | 157 | token + 文案入字典 |
| `AnomalyTable.tsx` | 183 | token + 表头/状态文案入字典 |
| `Anomalies.tsx` | 35 | token + 文案入字典 |
| `Dashboard.tsx` | 165 | token + 文案入字典；空态卡片重做；下拉选项（全部区间/近3年/近1年/共同区间、原始/去平滑）入字典 |
| `FundManagement.tsx` | 742 | 拆分 + token + 文案入字典；3 处 `bg-[#1a1a2e]` → `accent` |
| `store/useStore.ts` | 199 | 新增 UI 切片（`themeMode`/`resolvedTheme`/`lang`）与持久化 |

### 6.1 `FundManagement.tsx` 拆分

现状：742 行单组件，19 个 `useState`，5 块面板混在一个返回体里。
拆为 `src/pages/funds/` 下 5 个子组件，页面只留编排与跨面板共享状态：

- `FundTable.tsx` —— 基金列表 + 行操作（重算 / 更新 / 删除确认）
- `AddFundPanel.tsx` —— 添加基金表单（含高级选项折叠）
- `IngestJobPanel.tsx` —— 摄取 job 进度与轮询展示
- `PendingReviewPanel.tsx` —— 待审核队列通过/驳回
- `FundDataDrawer.tsx` —— 月度数据表 + RBA 历史

理由：本次要在该文件内改几十处文案与颜色，742 行单文件里做批量替换易改串；
拆后每块 100–200 行。拆分是**纯搬移**，不改状态语义、不改 API 调用、不改校验逻辑。

---

## 7. 测试与验收

### 7.1 新增单测（纯逻辑，不引 RTL）

1. `src/i18n/i18n.test.ts` —— 运行时 key 平价（`cn` 与 `en` 键集相等）、
   `{name}` 插值、缺参占位符保留且告警、未知 key 行为。
2. `src/theme/theme.test.ts` —— `themeMode` → `resolvedTheme` 解析矩阵
   （`system` × 系统亮/暗、显式 light/dark）、localStorage 读写、
   非法值回退 `system`、`matchMedia` 缺失时（旧环境）不抛异常。
3. `src/theme/palette.test.ts` —— 解析 `src/index.css` 两个变量块，
   与 `chartPalettes` 对拍：键集一致、色值一致（4.4 的防漂移闸）。
4. `src/i18n/no-hardcoded-cn.test.ts` —— 5.4 的中文字面量闸。

### 7.2 既有测试

`src/lib/rebase.test.ts`（269 行）不改动，必须继续全绿——用于证明本次改造
未触碰任何计算逻辑。

### 7.3 类型闸

`npm run build`（含 `tsc -b`）必须通过，兼作漏译闸。

### 7.4 浏览器实测

起 dev server，覆盖 2 主题 × 2 语言 × 3 页 = 12 组合，逐一检查：

- 控制台零报错、零 React key/依赖告警
- 切主题时图表立即重绘（验证 `useMemo` 依赖已加 `resolvedTheme`）
- 切语言时图表内文案跟随（验证依赖已加 `lang`）
- 深色下热力图缺月格不抢眼、锚定曲线可见、tooltip 可读
- 表格数字右对齐等宽、sticky 表头随滚动固定
- 后端中文消息在 EN 模式下带中立标签展示，不被误当作前端漏译

截图归档到 `docs/` 供人工确认。

---

## 8. 实施顺序

1. 基建：`index.css` 变量块 + `tailwind.config.js` + `index.html` 防白闪脚本 +
   store UI 切片 + `theme/` + `i18n/` 骨架 + 4 个新单测（此时字典为空，闸门先立起来）
2. `Sidebar` 控制簇（主题分段器 + 语言切换），跑通端到端切换
3. 基础组件 token 化 + 入字典：`Layout` / `MetricCard` / `FundChips` /
   `WarnBadge` / `ErrorBoundary`
4. 图表三件套：`useChartTheme` 接入 + 图内文案入字典
5. 表格与页面：`CompareTable` / `SmoothingCards` / `AnomalyTable` /
   `Anomalies` / `Dashboard`
6. `FundManagement` 拆分 + token + 字典（最大块，单独一步）
7. 全量验收：单测 + `tsc -b` + 12 组合浏览器实测 + 截图

每步结束运行 `npm test` 与 `npm run build`，保持可提交状态。

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| CSS 与 TS 图表色值漂移 | `palette.test.ts` 对拍闸 |
| 后续新功能又敲硬编码中文 | `no-hardcoded-cn.test.ts` 闸 |
| 切主题图表不重绘 | `useMemo` 依赖加 `resolvedTheme`，浏览器实测逐图确认 |
| `FundManagement` 拆分引入行为回归 | 拆分为纯搬移，不改状态语义；与 token/字典改动分两步提交，便于二分定位 |
| 深色下热力图缺月格变亮斑 | 缺月格改 `sunken`，实测确认其视觉权重低于有数据格 |
| EN 模式下后端中文消息被当作漏译 | 外层包 `Backend message:` 中立标签 |
| 深色下 `--anchor` 与序列色混淆 | 锚定曲线保留 `z` 提升与非锚定线 0.35 透明度的现有区分手段 |
