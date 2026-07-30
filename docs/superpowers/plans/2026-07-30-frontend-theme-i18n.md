# 前端装修（token 体系 + 深色模式 + CN/EN 双语）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `webapp/frontend` 改造成带设计 token 体系的专业金融数据台风格，支持 system/light/dark 三态主题与 CN/EN 全局语言切换，且不改动任何数据或计算逻辑。

**Architecture:** 颜色唯一真源是 `src/index.css` 的两个 CSS 变量块（`:root` / `:root.dark`），Tailwind 语义色映射到变量，组件只写 `bg-surface text-fg` 这类语义类名，**永不写 `dark:` 双份类名**。ECharts 画布不解析 CSS 变量，因此另立 `src/theme/chartTheme.ts` 存 TS 副本，并用单测解析 `index.css` 对拍防漂移。i18n 是自写扁平字典 + `useT()` hook，`en.ts` 声明为 `Record<keyof typeof cn, string>`，漏译由 `tsc -b` 拦截；另有中文字面量扫描闸保证后续新页面天生双语。

**Tech Stack:** React 18、Vite 5、Tailwind 3、zustand 4、ECharts 5（`echarts-for-react/lib/core`）、Vitest 4（`environment: 'node'`，`include: ['src/**/*.test.ts']`）。

**Spec:** `docs/superpowers/specs/2026-07-30-frontend-theme-i18n-design.md`

---

## Global Constraints

- 工作目录：所有相对路径以 `webapp/frontend/` 为根，除非写了完整路径。
- **不新增任何依赖**，`dependencies` 与 `devDependencies` 均不动。不引 shadcn/ui、Radix、react-i18next、图标库、`@testing-library/react`。图标用内联 SVG。
- **不改** `webapp/backend/`、`llm_ingest/`、任何计算逻辑。`src/lib/rebase.ts` 与 `src/lib/fundCodes.ts` 的逻辑不动（`rebase.ts` 里的中文是注释，保留）。
- **不改** `src/api/client.ts` 的请求逻辑、`src/types/index.ts` 的类型定义。
- 后端返回的中文消息（HTTP `detail`、摄取 job 日志）**不翻译**，原样透传，外层包中立标签 `t('common.backendMessage')`。
- 组件中禁止出现 `dark:` 前缀类名，禁止出现新的硬编码颜色（`#rrggbb`、`rgb(...)`、`bg-gray-*`、`text-gray-*`、`cyan-*`、`red-*`、`amber-*`）。
- Vitest 环境是 `node`，测试文件必须是 `src/**/*.test.ts`（不是 `.tsx`），且**不得**触碰真实 `document` / `localStorage` / `matchMedia` —— 所有依赖通过参数注入。
- 测试命令：`npx vitest run <path>`；构建/类型闸：`npm run build`（含 `tsc -b`）。
- 已有测试 `src/lib/rebase.test.ts`（269 行）全程必须保持绿色，它是"未触碰计算逻辑"的证据。
- 数字展示一律 `font-mono tabular-nums` 且右对齐；百分比与小数位不做 locale 化。
- 基金名、APIR、基金短码、月份码（`2025-03`）两语言都不翻译。
- 每个 Task 结束时 `npx vitest run` 与 `npm run build` 都必须通过，保持可提交状态。

### 全局 token 替换映射（Task 6–13 逐文件套用）

| 现状类名 | 替换为 |
|---|---|
| `bg-white` | `bg-surface` |
| `bg-gray-50`（页面画布） | `bg-bg` |
| `bg-gray-50` / `bg-gray-100`（表头、空态、斑马纹、禁用底） | `bg-sunken` |
| `text-gray-800` / `text-gray-900` / 无色默认正文 | `text-fg` |
| `text-gray-500` / `text-gray-600` / `text-gray-700` | `text-fg-muted` |
| `text-gray-300` / `text-gray-400` | `text-fg-subtle` |
| `border-gray-100` / `border-gray-200` | `border-border` |
| `border-gray-300` | `border-border-strong` |
| `text-cyan-800` / `border-cyan-400` / `text-cyan-*` | `text-accent` / `border-accent` |
| `bg-cyan-50` | `bg-accent-soft` |
| `bg-[#1a1a2e]`（按钮）/ `hover:bg-[#2a2a4e]` | `bg-accent text-accent-fg` / `hover:opacity-90` |
| `text-red-500` / `text-red-700` | `text-neg` |
| `bg-red-50` / `border-red-200` | `bg-neg-soft` / `border-neg-border` |
| `text-amber-700` / `bg-amber-50` / `border-amber-200` | `text-warn` / `bg-warn-soft` / `border-warn-border` |
| `bg-green-*` / `text-green-*` | `bg-pos-soft` / `text-pos` |
| `rounded` | `rounded-md` |
| `shadow-sm` + `bg-white` 卡片 | `.card`（见 Task 1） |

---

## File Structure

**新建：**

| 文件 | 责任 |
|---|---|
| `src/theme/theme.ts` | 主题纯函数：读写 localStorage、解析 system、套 class。零 React 依赖，可在 node 环境测。 |
| `src/theme/theme.test.ts` | 上者的单测。 |
| `src/theme/chartTheme.ts` | ECharts 用的 TS 调色板 + `CHART_TOKEN_MAP`（字段 → CSS 变量名，供对拍）。 |
| `src/theme/useChartTheme.ts` | `useChartTheme()`：按 store 的 `resolvedTheme` 返回调色板。 |
| `src/theme/palette.test.ts` | 解析 `src/index.css` 与 `chartPalettes` 对拍，防漂移。 |
| `src/i18n/cn.ts` | 中文字典（唯一 key 定义源，`as const`）。 |
| `src/i18n/en.ts` | 英文字典，类型 `Record<keyof typeof cn, string>`。 |
| `src/i18n/index.ts` | `translate(lang, key, params?)` + 类型导出。 |
| `src/i18n/useT.ts` | `useT()` hook，从 store 读 `lang`。 |
| `src/i18n/i18n.test.ts` | 字典平价 / 插值 / 缺参告警 / 未知 key。 |
| `src/i18n/no-hardcoded-cn.test.ts` | 中文字面量扫描闸（带逐步收缩的待迁移白名单）。 |
| `src/components/ThemeLangControls.tsx` | 侧栏底部控制簇：三态主题分段器 + CN/EN 切换。 |
| `src/pages/funds/FundTable.tsx` | 基金列表表格 + 行操作。 |
| `src/pages/funds/AddFundPanel.tsx` | 添加基金表单（含高级选项折叠）。 |
| `src/pages/funds/IngestJobPanel.tsx` | 摄取 job 进度展示。 |
| `src/pages/funds/PendingReviewPanel.tsx` | 待审核队列。 |
| `src/pages/funds/FundDataDrawer.tsx` | 月度数据表 + RBA 利率历史。 |

**修改：** `index.html`、`tailwind.config.js`、`src/index.css`、`src/main.tsx`、`src/store/useStore.ts`、`src/components/{Layout,Sidebar,MetricCard,FundChips,WarnBadge,ErrorBoundary,CompareTable,ExcessHeatmap,NavChart,RollingExcessChart,SmoothingCards,AnomalyTable}.tsx`、`src/pages/{Dashboard,Anomalies,FundManagement}.tsx`。

---

## Task 1: 设计 token 基建（CSS 变量 + Tailwind 映射 + 防白闪）

**Files:**
- Modify: `webapp/frontend/src/index.css`（当前 8 行，整体重写）
- Modify: `webapp/frontend/tailwind.config.js`（当前 8 行，整体重写）
- Modify: `webapp/frontend/index.html`（`<head>` 加内联脚本，`<body>` 换 class）
- Test: `webapp/frontend/src/theme/palette.test.ts`（本 Task 只建"变量完整性"部分，Task 5 再加对拍部分）

**Interfaces:**
- Consumes: 无
- Produces: CSS 变量名集合（下方 `REQUIRED_TOKENS`），Tailwind 语义色类名（`bg-bg` `bg-surface` `bg-sunken` `border-border` `border-border-strong` `text-fg` `text-fg-muted` `text-fg-subtle` `bg-accent` `text-accent` `text-accent-fg` `bg-accent-soft` `text-pos` `bg-pos-soft` `text-neg` `bg-neg-soft` `border-neg-border` `text-warn` `bg-warn-soft` `border-warn-border`），以及 `.card` / `.num` / `.th` 三个组件类。

> **注意（spec 之外的补充）**：Tailwind 的 `/opacity` 修饰符对 `var()` 颜色无效，所以正/负/告警的浅底与边框各自独立成 token（`--pos-soft` `--neg-soft` `--neg-border` `--warn-soft` `--warn-border`），另加按钮前景 `--accent-fg` 与 tooltip 三色。

- [ ] **Step 1: 写变量完整性测试**

创建 `src/theme/palette.test.ts`：

```ts
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const CSS_PATH = resolve(__dirname, '../index.css')

/** 解析 index.css 里的 `:root {...}` / `:root.dark {...}` 变量块。 */
export function parseCssVars(css: string, selector: string): Record<string, string> {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const block = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(css)
  if (!block) throw new Error(`selector not found in index.css: ${selector}`)
  const out: Record<string, string> = {}
  for (const line of block[1].split(';')) {
    const m = /^\s*(--[\w-]+)\s*:\s*(.+?)\s*$/.exec(line)
    if (m) out[m[1]] = m[2]
  }
  return out
}

const REQUIRED_TOKENS = [
  '--bg', '--surface', '--sunken', '--border', '--border-strong',
  '--fg', '--fg-muted', '--fg-subtle',
  '--accent', '--accent-soft', '--accent-fg',
  '--pos', '--pos-soft', '--neg', '--neg-soft', '--neg-border',
  '--warn', '--warn-soft', '--warn-border',
  '--grid', '--anchor',
  '--tooltip-bg', '--tooltip-border', '--tooltip-fg',
  '--heat-pos', '--heat-neg',
  '--series-1', '--series-2', '--series-3', '--series-4',
  '--series-5', '--series-6', '--series-7', '--series-8',
]

describe('index.css 设计 token', () => {
  const css = readFileSync(CSS_PATH, 'utf8')
  const light = parseCssVars(css, ':root')
  const dark = parseCssVars(css, ':root.dark')

  it.each(REQUIRED_TOKENS)('浅色块定义了 %s', token => {
    expect(light[token]).toBeTruthy()
  })

  it.each(REQUIRED_TOKENS)('深色块定义了 %s', token => {
    expect(dark[token]).toBeTruthy()
  })

  it('两块 token 键集完全一致', () => {
    expect(Object.keys(light).sort()).toEqual(Object.keys(dark).sort())
  })

  it('浅色与深色的值不得完全相同（否则等于没做深色）', () => {
    const identical = Object.keys(light).filter(k => light[k] === dark[k])
    expect(identical).toEqual([])
  })

  it('热力图基色是空格分隔 RGB 三元组（组件要按 alpha 合成）', () => {
    for (const vars of [light, dark]) {
      expect(vars['--heat-pos']).toMatch(/^\d{1,3} \d{1,3} \d{1,3}$/)
      expect(vars['--heat-neg']).toMatch(/^\d{1,3} \d{1,3} \d{1,3}$/)
    }
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd webapp/frontend && npx vitest run src/theme/palette.test.ts`
Expected: FAIL，报 `selector not found in index.css: :root`

- [ ] **Step 3: 重写 `src/index.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --bg: #f6f7f9;
  --surface: #ffffff;
  --sunken: #f1f3f5;
  --border: #e3e6ea;
  --border-strong: #cfd4da;
  --fg: #14181d;
  --fg-muted: #6b7480;
  --fg-subtle: #9aa3ad;
  --accent: #0e7490;
  --accent-soft: #e0f2f7;
  --accent-fg: #ffffff;
  --pos: #157f57;
  --pos-soft: #e6f4ec;
  --neg: #c0392f;
  --neg-soft: #fdecea;
  --neg-border: #f5c2bd;
  --warn: #b45309;
  --warn-soft: #fdf3e3;
  --warn-border: #f0d9a8;
  --grid: #eef0f3;
  --anchor: #14181d;
  --tooltip-bg: #ffffff;
  --tooltip-border: #e3e6ea;
  --tooltip-fg: #14181d;
  --heat-pos: 42 120 214;
  --heat-neg: 227 73 72;
  --series-1: #2478c4;
  --series-2: #6f42c1;
  --series-3: #e8590c;
  --series-4: #2f9e44;
  --series-5: #c99a06;
  --series-6: #d6336c;
  --series-7: #0c8599;
  --series-8: #9c36b5;
  --card-shadow: 0 1px 2px rgba(20, 24, 29, 0.04);
}

:root.dark {
  --bg: #0d1117;
  --surface: #161b22;
  --sunken: #1c232c;
  --border: #273040;
  --border-strong: #3a4552;
  --fg: #e6edf3;
  --fg-muted: #9aa5b1;
  --fg-subtle: #6e7a87;
  --accent: #22b8cf;
  --accent-soft: rgba(34, 184, 207, 0.14);
  --accent-fg: #06222a;
  --pos: #3fb984;
  --pos-soft: rgba(63, 185, 132, 0.14);
  --neg: #f2685f;
  --neg-soft: rgba(242, 104, 95, 0.14);
  --neg-border: rgba(242, 104, 95, 0.35);
  --warn: #e0a03a;
  --warn-soft: rgba(224, 160, 58, 0.14);
  --warn-border: rgba(224, 160, 58, 0.35);
  --grid: #21272f;
  --anchor: #f0f4f8;
  --tooltip-bg: #1c232c;
  --tooltip-border: #273040;
  --tooltip-fg: #e6edf3;
  --heat-pos: 77 155 233;
  --heat-neg: 240 105 100;
  --series-1: #4dabf7;
  --series-2: #a78bfa;
  --series-3: #ff922b;
  --series-4: #51cf66;
  --series-5: #ffd43b;
  --series-6: #f783ac;
  --series-7: #22b8cf;
  --series-8: #da77f2;
  --card-shadow: none;
}

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background-color: var(--bg);
  color: var(--fg);
}

@layer components {
  /* 卡片：1px 边框为主，浅色下极弱阴影，深色下无阴影（--card-shadow 控制） */
  .card {
    background-color: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.5rem;
    box-shadow: var(--card-shadow);
  }
  /* 数字单元：等宽 + 表格数字宽度锁定 + 右对齐 */
  .num {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-variant-numeric: tabular-nums;
    text-align: right;
  }
  /* 表头：11px 大写字距 + sunken 底 + 吸顶 */
  .th {
    position: sticky;
    top: 0;
    z-index: 1;
    background-color: var(--sunken);
    color: var(--fg-muted);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    border-bottom: 1px solid var(--border);
  }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd webapp/frontend && npx vitest run src/theme/palette.test.ts`
Expected: PASS（74 个断言全过）

- [ ] **Step 5: 重写 `tailwind.config.js`**

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        surface: 'var(--surface)',
        sunken: 'var(--sunken)',
        border: 'var(--border)',
        'border-strong': 'var(--border-strong)',
        fg: 'var(--fg)',
        'fg-muted': 'var(--fg-muted)',
        'fg-subtle': 'var(--fg-subtle)',
        accent: 'var(--accent)',
        'accent-soft': 'var(--accent-soft)',
        'accent-fg': 'var(--accent-fg)',
        pos: 'var(--pos)',
        'pos-soft': 'var(--pos-soft)',
        neg: 'var(--neg)',
        'neg-soft': 'var(--neg-soft)',
        'neg-border': 'var(--neg-border)',
        warn: 'var(--warn)',
        'warn-soft': 'var(--warn-soft)',
        'warn-border': 'var(--warn-border)',
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
```

- [ ] **Step 6: 改 `index.html`（防首帧白闪 + body class）**

`<head>` 内 `<title>` 之后插入内联脚本；`<body class="bg-gray-50">` 改为 `<body>`（背景由 `body` 规则接管）：

```html
    <title>固定收益基金分析</title>
    <script>
      // 首帧前套上 dark class，避免深色用户看到白闪。逻辑与 src/theme/theme.ts 一致。
      (function () {
        try {
          var m = localStorage.getItem('ff.theme')
          if (m !== 'light' && m !== 'dark' && m !== 'system') m = 'system'
          var dark = m === 'dark' || (m === 'system' &&
            window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
          if (dark) document.documentElement.classList.add('dark')
        } catch (e) {}
      })()
    </script>
```

- [ ] **Step 7: 构建确认**

Run: `cd webapp/frontend && npm run build`
Expected: 构建成功（此时组件仍用旧 gray-* 类名，属正常；`bg-gray-50` 仍是 Tailwind 内置色，不冲突）

- [ ] **Step 8: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/index.css webapp/frontend/tailwind.config.js webapp/frontend/index.html webapp/frontend/src/theme/palette.test.ts
git commit -m "feat(frontend): 设计 token 体系 — CSS 变量双块 + Tailwind 语义色 + 防白闪脚本"
```

---

## Task 2: 主题状态（三态解析 + 持久化）

**Files:**
- Create: `webapp/frontend/src/theme/theme.ts`
- Test: `webapp/frontend/src/theme/theme.test.ts`
- Modify: `webapp/frontend/src/store/useStore.ts`（`AppState` 接口 + 初始值 + setter）
- Modify: `webapp/frontend/src/main.tsx`（启动时初始化）

**Interfaces:**
- Consumes: Task 1 的 `dark` class 约定
- Produces:
  - `src/theme/theme.ts`：`type ThemeMode = 'system'|'light'|'dark'`、`type ResolvedTheme = 'light'|'dark'`、`readStoredThemeMode(storage?): ThemeMode`、`writeStoredThemeMode(mode, storage?): void`、`systemPrefersDark(): boolean`、`resolveTheme(mode, prefersDark): ResolvedTheme`、`applyTheme(resolved, root?): void`、`watchSystemTheme(cb): () => void`
  - store 新增：`themeMode: ThemeMode`、`resolvedTheme: ResolvedTheme`、`setThemeMode(mode: ThemeMode): void`、`initTheme(): () => void`

- [ ] **Step 1: 写失败测试**

创建 `src/theme/theme.test.ts`：

```ts
import { describe, expect, it, vi } from 'vitest'
import {
  applyTheme, readStoredThemeMode, resolveTheme, writeStoredThemeMode,
  type ThemeMode,
} from './theme'

function fakeStorage(initial: Record<string, string> = {}) {
  const data = { ...initial }
  return {
    getItem: (k: string) => (k in data ? data[k] : null),
    setItem: (k: string, v: string) => { data[k] = v },
    data,
  }
}

describe('resolveTheme', () => {
  it('system 跟随系统偏好', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
  })

  it('显式 light/dark 忽略系统偏好', () => {
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })
})

describe('readStoredThemeMode', () => {
  it.each<[string, ThemeMode]>([
    ['system', 'system'], ['light', 'light'], ['dark', 'dark'],
  ])('读回合法值 %s', (raw, expected) => {
    expect(readStoredThemeMode(fakeStorage({ 'ff.theme': raw }))).toBe(expected)
  })

  it('非法值回退 system', () => {
    expect(readStoredThemeMode(fakeStorage({ 'ff.theme': 'neon' }))).toBe('system')
  })

  it('未设置过回退 system', () => {
    expect(readStoredThemeMode(fakeStorage())).toBe('system')
  })

  it('storage 抛异常（隐私模式）时回退 system 而不崩', () => {
    const throwing = { getItem: () => { throw new Error('denied') } }
    expect(readStoredThemeMode(throwing)).toBe('system')
  })
})

describe('writeStoredThemeMode', () => {
  it('写入 ff.theme', () => {
    const s = fakeStorage()
    writeStoredThemeMode('dark', s)
    expect(s.data['ff.theme']).toBe('dark')
  })

  it('storage 抛异常时静默不崩', () => {
    const throwing = { setItem: () => { throw new Error('denied') } }
    expect(() => writeStoredThemeMode('dark', throwing)).not.toThrow()
  })
})

describe('applyTheme', () => {
  it('dark 时加 class，light 时去 class', () => {
    const toggle = vi.fn()
    const root = { classList: { toggle } }
    applyTheme('dark', root)
    expect(toggle).toHaveBeenCalledWith('dark', true)
    applyTheme('light', root)
    expect(toggle).toHaveBeenCalledWith('dark', false)
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd webapp/frontend && npx vitest run src/theme/theme.test.ts`
Expected: FAIL，报无法解析 `./theme`

- [ ] **Step 3: 实现 `src/theme/theme.ts`**

```ts
export type ThemeMode = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'ff.theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

/** localStorage 的最小契约，便于在 node 环境注入假实现。 */
interface ReadableStorage { getItem(key: string): string | null }
interface WritableStorage { setItem(key: string, value: string): void }
/** classList.toggle 的最小契约，同上。 */
interface ClassListHost { classList: { toggle(token: string, force: boolean): void } }

function defaultStorage(): (ReadableStorage & WritableStorage) | null {
  return typeof localStorage === 'undefined' ? null : localStorage
}

export function readStoredThemeMode(storage: ReadableStorage | null = defaultStorage()): ThemeMode {
  if (!storage) return 'system'
  let raw: string | null
  try {
    raw = storage.getItem(STORAGE_KEY)
  } catch {
    return 'system'
  }
  return raw === 'light' || raw === 'dark' || raw === 'system' ? raw : 'system'
}

export function writeStoredThemeMode(
  mode: ThemeMode, storage: WritableStorage | null = defaultStorage(),
): void {
  if (!storage) return
  try {
    storage.setItem(STORAGE_KEY, mode)
  } catch {
    // 隐私模式下写入被拒：本次会话内仍生效，只是不持久化
  }
}

export function systemPrefersDark(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia(DARK_QUERY).matches
}

export function resolveTheme(mode: ThemeMode, prefersDark: boolean): ResolvedTheme {
  if (mode === 'system') return prefersDark ? 'dark' : 'light'
  return mode
}

export function applyTheme(
  resolved: ResolvedTheme,
  root: ClassListHost | null = typeof document === 'undefined' ? null : document.documentElement,
): void {
  root?.classList.toggle('dark', resolved === 'dark')
}

/** 订阅系统深浅色变化，返回退订函数。非浏览器环境返回空函数。 */
export function watchSystemTheme(onChange: (prefersDark: boolean) => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {}
  const mql = window.matchMedia(DARK_QUERY)
  const handler = (e: MediaQueryListEvent) => onChange(e.matches)
  mql.addEventListener('change', handler)
  return () => mql.removeEventListener('change', handler)
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd webapp/frontend && npx vitest run src/theme/theme.test.ts`
Expected: PASS

- [ ] **Step 5: store 接入**

`src/store/useStore.ts` 顶部 import 追加：

```ts
import {
  applyTheme, readStoredThemeMode, resolveTheme, systemPrefersDark,
  watchSystemTheme, writeStoredThemeMode,
  type ResolvedTheme, type ThemeMode,
} from '../theme/theme'
```

`interface AppState` 内，在 `smoothingMode: SmoothingMode` 之后插入：

```ts
  // UI 偏好（主题）
  themeMode: ThemeMode
  resolvedTheme: ResolvedTheme
```

`interface AppState` 的操作区（`setSmoothingMode` 之后）插入：

```ts
  setThemeMode: (mode: ThemeMode) => void
  /** 启动时调用一次：套用已存主题并订阅系统变化，返回退订函数。 */
  initTheme: () => () => void
```

`create<AppState>((set, get) => ({` 的初始值区，`smoothingMode: 'original',` 之后插入：

```ts
  themeMode: readStoredThemeMode(),
  resolvedTheme: resolveTheme(readStoredThemeMode(), systemPrefersDark()),
```

实现区（文件末尾 `deleteFund` 之后，闭括号之前）插入：

```ts
  setThemeMode: (mode: ThemeMode) => {
    const resolved = resolveTheme(mode, systemPrefersDark())
    writeStoredThemeMode(mode)
    applyTheme(resolved)
    set({ themeMode: mode, resolvedTheme: resolved })
  },

  initTheme: () => {
    applyTheme(get().resolvedTheme)
    // system 态才需要跟随系统变化；显式 light/dark 下回调直接忽略
    return watchSystemTheme(prefersDark => {
      if (get().themeMode !== 'system') return
      const resolved = resolveTheme('system', prefersDark)
      applyTheme(resolved)
      set({ resolvedTheme: resolved })
    })
  },
```

- [ ] **Step 6: `main.tsx` 启动初始化**

`src/main.tsx` 改为：

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { useStore } from './store/useStore'
import './index.css'

useStore.getState().initTheme()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
```

- [ ] **Step 7: 全量测试 + 构建**

Run: `cd webapp/frontend && npx vitest run && npm run build`
Expected: 全部 PASS，构建成功

- [ ] **Step 8: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/theme webapp/frontend/src/store/useStore.ts webapp/frontend/src/main.tsx
git commit -m "feat(frontend): 三态主题状态 — system/light/dark 解析 + localStorage 持久化"
```

---

## Task 3: i18n 内核 + 中文字面量闸

**Files:**
- Create: `webapp/frontend/src/i18n/cn.ts`、`en.ts`、`index.ts`、`useT.ts`
- Test: `webapp/frontend/src/i18n/i18n.test.ts`、`webapp/frontend/src/i18n/no-hardcoded-cn.test.ts`
- Modify: `webapp/frontend/src/store/useStore.ts`（`lang` 状态）

**Interfaces:**
- Consumes: Task 2 的 store 扩展模式
- Produces:
  - `src/i18n/index.ts`：`type Lang = 'cn'|'en'`、`type I18nKey = keyof typeof cn`、`translate(lang, key, params?): string`、`readStoredLang(storage?): Lang`、`writeStoredLang(lang, storage?): void`、`applyLang(lang, root?): void`
  - `src/i18n/useT.ts`：`useT(): (key: I18nKey, params?: Record<string, string|number>) => string`
  - store 新增：`lang: Lang`、`setLang(lang: Lang): void`
  - `src/i18n/no-hardcoded-cn.test.ts` 顶部的 `PENDING_FILES` 白名单——**Task 6–13 每完成一个文件就从白名单删掉它**，Task 14 断言白名单为空

- [ ] **Step 1: 写失败测试（i18n 内核）**

创建 `src/i18n/i18n.test.ts`：

```ts
import { describe, expect, it, vi } from 'vitest'
import { cn } from './cn'
import { en } from './en'
import { readStoredLang, translate, writeStoredLang, type I18nKey } from './index'

function fakeStorage(initial: Record<string, string> = {}) {
  const data = { ...initial }
  return {
    getItem: (k: string) => (k in data ? data[k] : null),
    setItem: (k: string, v: string) => { data[k] = v },
    data,
  }
}

describe('字典平价', () => {
  it('cn 与 en 键集完全一致', () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(cn).sort())
  })

  it('两侧都没有空串', () => {
    const emptyCn = Object.entries(cn).filter(([, v]) => !String(v).trim()).map(([k]) => k)
    const emptyEn = Object.entries(en).filter(([, v]) => !String(v).trim()).map(([k]) => k)
    expect({ emptyCn, emptyEn }).toEqual({ emptyCn: [], emptyEn: [] })
  })

  it('同一 key 两侧占位符集合一致（防译文漏插值）', () => {
    const holders = (s: string) => (s.match(/\{(\w+)\}/g) ?? []).sort()
    const mismatched = Object.keys(cn).filter(
      k => holders(cn[k as I18nKey]).join() !== holders(en[k as I18nKey]).join(),
    )
    expect(mismatched).toEqual([])
  })

  it('en 一侧不含中日韩字符（防漏译混入）', () => {
    const leaked = Object.entries(en).filter(([, v]) => /[一-龥]/.test(String(v)))
    expect(leaked).toEqual([])
  })
})

describe('translate', () => {
  it('按语言取文', () => {
    expect(translate('cn', 'nav.dashboard')).toBe('对比看板')
    expect(translate('en', 'nav.dashboard')).toBe('Dashboard')
  })

  it('花括号插值', () => {
    expect(translate('cn', 'common.recovered', { n: 3 })).toBe('恢复 3 个月')
    expect(translate('en', 'common.recovered', { n: 3 })).toBe('Recovered in 3 mo')
  })

  it('缺参时保留占位符并告警，不返回 undefined', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(translate('cn', 'common.recovered')).toBe('恢复 {n} 个月')
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('未知 key 原样返回 key，不抛异常', () => {
    expect(translate('cn', 'not.a.real.key' as I18nKey)).toBe('not.a.real.key')
  })

  it('参数值为 0 或空串时照常代入（不当作缺参）', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(translate('cn', 'common.recovered', { n: 0 })).toBe('恢复 0 个月')
    expect(warn).not.toHaveBeenCalled()
    warn.mockRestore()
  })
})

describe('语言持久化', () => {
  it('读回合法值', () => {
    expect(readStoredLang(fakeStorage({ 'ff.lang': 'en' }))).toBe('en')
  })

  it('非法值与未设置都回退 cn', () => {
    expect(readStoredLang(fakeStorage({ 'ff.lang': 'jp' }))).toBe('cn')
    expect(readStoredLang(fakeStorage())).toBe('cn')
  })

  it('写入 ff.lang', () => {
    const s = fakeStorage()
    writeStoredLang('en', s)
    expect(s.data['ff.lang']).toBe('en')
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd webapp/frontend && npx vitest run src/i18n/i18n.test.ts`
Expected: FAIL，报无法解析 `./cn`

- [ ] **Step 3: 建字典骨架（common + nav + theme + lang 段）**

`src/i18n/cn.ts`：

```ts
/**
 * 中文字典 —— key 的唯一定义源。
 * en.ts 声明为 Record<keyof typeof cn, string>，漏译会在 tsc -b 阶段失败。
 * key 命名：<域>.<语义>，域为 common / nav / theme / lang / dashboard /
 * metric / chart / table / heatmap / anomaly / smoothing / funds。
 */
export const cn = {
  'common.loading': '加载中...',
  'common.noData': '暂无数据',
  'common.error': '出错了',
  'common.noDrawdown': '无回撤',
  'common.months': '{n} 个月',
  'common.recovered': '恢复 {n} 个月',
  'common.notRecovered': '未恢复(已 {n} 个月)',
  'common.smallSample': '样本不足(n={n})，统计指标不可靠',
  'common.listSeparator': '、',
  'common.backendMessage': '后端消息：',

  'nav.brand': '固定收益基金分析',
  'nav.dashboard': '对比看板',
  'nav.anomalies': '异常审计',
  'nav.funds': '基金管理',

  'theme.system': '跟随系统',
  'theme.light': '浅色',
  'theme.dark': '深色',
  'lang.switch': '切换语言',
} as const
```

`src/i18n/en.ts`：

```ts
import type { cn } from './cn'

/** 英文字典。缺任何一个 cn 的 key，tsc -b 直接失败。 */
export const en: Record<keyof typeof cn, string> = {
  'common.loading': 'Loading…',
  'common.noData': 'No data',
  'common.error': 'Something went wrong',
  'common.noDrawdown': 'No drawdown',
  'common.months': '{n} mo',
  'common.recovered': 'Recovered in {n} mo',
  'common.notRecovered': 'Not recovered ({n} mo so far)',
  'common.smallSample': 'Sample too small (n={n}); statistics unreliable',
  'common.listSeparator': ', ',
  'common.backendMessage': 'Backend message: ',

  'nav.brand': 'Fixed Income Fund Analytics',
  'nav.dashboard': 'Dashboard',
  'nav.anomalies': 'Anomaly audit',
  'nav.funds': 'Fund management',

  'theme.system': 'System',
  'theme.light': 'Light',
  'theme.dark': 'Dark',
  'lang.switch': 'Switch language',
}
```

- [ ] **Step 4: 实现 `src/i18n/index.ts`**

```ts
import { cn } from './cn'
import { en } from './en'

export type Lang = 'cn' | 'en'
export type I18nKey = keyof typeof cn
export type I18nParams = Record<string, string | number>

const STORAGE_KEY = 'ff.lang'
const dicts: Record<Lang, Record<I18nKey, string>> = { cn, en }

interface ReadableStorage { getItem(key: string): string | null }
interface WritableStorage { setItem(key: string, value: string): void }

function defaultStorage(): (ReadableStorage & WritableStorage) | null {
  return typeof localStorage === 'undefined' ? null : localStorage
}

export function translate(lang: Lang, key: I18nKey, params?: I18nParams): string {
  const template = dicts[lang][key] ?? dicts.cn[key]
  if (template == null) return String(key)
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => {
    if (params && Object.prototype.hasOwnProperty.call(params, name)) return String(params[name])
    console.warn(`[i18n] key "${String(key)}" 缺少参数 "${name}"`)
    return whole
  })
}

export function readStoredLang(storage: ReadableStorage | null = defaultStorage()): Lang {
  if (!storage) return 'cn'
  let raw: string | null
  try {
    raw = storage.getItem(STORAGE_KEY)
  } catch {
    return 'cn'
  }
  return raw === 'en' || raw === 'cn' ? raw : 'cn'
}

export function writeStoredLang(
  lang: Lang, storage: WritableStorage | null = defaultStorage(),
): void {
  if (!storage) return
  try {
    storage.setItem(STORAGE_KEY, lang)
  } catch {
    // 隐私模式：本次会话仍生效，不持久化
  }
}

/** 同步 <html lang>，供屏幕阅读器与浏览器翻译判断。 */
export function applyLang(
  lang: Lang,
  root: { setAttribute(name: string, value: string): void } | null =
    typeof document === 'undefined' ? null : document.documentElement,
): void {
  root?.setAttribute('lang', lang === 'cn' ? 'zh-CN' : 'en')
}
```

- [ ] **Step 5: 实现 `src/i18n/useT.ts`**

```ts
import { useCallback } from 'react'
import { useStore } from '../store/useStore'
import { translate, type I18nKey, type I18nParams } from './index'

/** 组件取文入口。返回的 t 随 lang 变化而变化，可安全放进 useMemo 依赖。 */
export function useT() {
  const lang = useStore(s => s.lang)
  return useCallback(
    (key: I18nKey, params?: I18nParams) => translate(lang, key, params),
    [lang],
  )
}
```

- [ ] **Step 6: store 接入 `lang`**

`src/store/useStore.ts` import 追加：

```ts
import { applyLang, readStoredLang, writeStoredLang, type Lang } from '../i18n'
```

`interface AppState` 内 `resolvedTheme: ResolvedTheme` 之后插入：

```ts
  lang: Lang
```

操作区 `initTheme` 声明之后插入：

```ts
  setLang: (lang: Lang) => void
```

初始值区 `resolvedTheme: ...` 之后插入：

```ts
  lang: readStoredLang(),
```

实现区 `initTheme` 之后插入：

```ts
  setLang: (lang: Lang) => {
    writeStoredLang(lang)
    applyLang(lang)
    set({ lang })
  },
```

`initTheme` 实现体首行 `applyTheme(get().resolvedTheme)` 之后补一行，让启动时 `<html lang>` 与存储一致：

```ts
    applyLang(get().lang)
```

- [ ] **Step 7: 跑测试确认通过**

Run: `cd webapp/frontend && npx vitest run src/i18n/i18n.test.ts`
Expected: PASS

- [ ] **Step 8: 建中文字面量闸（带待迁移白名单）**

创建 `src/i18n/no-hardcoded-cn.test.ts`：

```ts
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = resolve(__dirname, '..')
const SCAN_DIRS = ['components', 'pages']

/**
 * 尚未迁移到字典的文件。每完成一个文件的 i18n 改造就从这里删掉它。
 * Task 14 会断言此数组为空 —— 空数组之后，任何新页面写死中文都会让本测试报红。
 */
const PENDING_FILES: string[] = [
  'components/AnomalyTable.tsx',
  'components/CompareTable.tsx',
  'components/ErrorBoundary.tsx',
  'components/ExcessHeatmap.tsx',
  'components/MetricCard.tsx',
  'components/NavChart.tsx',
  'components/RollingExcessChart.tsx',
  'components/Sidebar.tsx',
  'components/SmoothingCards.tsx',
  'pages/Anomalies.tsx',
  'pages/Dashboard.tsx',
  'pages/FundManagement.tsx',
]

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) out.push(...walk(full))
    else if (name.endsWith('.tsx')) out.push(full)
  }
  return out
}

/** 剥掉块注释与行注释；注释里的中文是允许的（说明代码用）。 */
function stripComments(code: string): string {
  return code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1')
}

describe('组件与页面不得出现中文字面量', () => {
  const files = SCAN_DIRS.flatMap(d => walk(join(SRC, d)))

  it('扫到了文件（防止路径写错导致空跑通过）', () => {
    expect(files.length).toBeGreaterThan(10)
  })

  it.each(files.map(f => relative(SRC, f)))('%s 无中文字面量', rel => {
    if (PENDING_FILES.includes(rel)) return
    const stripped = stripComments(readFileSync(join(SRC, rel), 'utf8'))
    const offenders = stripped.split('\n')
      .map((line, i) => ({ line: i + 1, text: line }))
      .filter(x => /[一-龥]/.test(x.text))
      .map(x => `${rel}:${x.line}: ${x.text.trim()}`)
    expect(offenders).toEqual([])
  })

  it('白名单里的文件都真实存在（防止改名后闸门失效）', () => {
    const existing = new Set(files.map(f => relative(SRC, f)))
    const stale = PENDING_FILES.filter(p => !existing.has(p))
    expect(stale).toEqual([])
  })
})
```

- [ ] **Step 9: 跑闸门确认通过（白名单覆盖全部存量文件）**

Run: `cd webapp/frontend && npx vitest run src/i18n/no-hardcoded-cn.test.ts`
Expected: PASS。若报某个未在白名单的文件有中文（例如 `Layout.tsx` / `FundChips.tsx` / `WarnBadge.tsx` 本无中文），说明该文件确实含中文，把它补进白名单。

- [ ] **Step 10: 全量测试 + 构建**

Run: `cd webapp/frontend && npx vitest run && npm run build`
Expected: 全部 PASS，构建成功

- [ ] **Step 11: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/i18n webapp/frontend/src/store/useStore.ts
git commit -m "feat(frontend): i18n 内核 — 扁平字典 + 类型漏译闸 + 中文字面量扫描闸"
```

---

## Task 4: 侧栏控制簇（主题分段器 + 语言切换）+ Sidebar 改造

**Files:**
- Create: `webapp/frontend/src/components/ThemeLangControls.tsx`
- Modify: `webapp/frontend/src/components/Sidebar.tsx`（36 行，整体重写）
- Modify: `webapp/frontend/src/i18n/cn.ts`、`en.ts`（无需加新 key，Task 3 已含 `nav.*` / `theme.*` / `lang.switch`）
- Modify: `webapp/frontend/src/i18n/no-hardcoded-cn.test.ts`（白名单删 `components/Sidebar.tsx`）

**Interfaces:**
- Consumes: `useT()`（Task 3）、store 的 `themeMode` / `setThemeMode` / `lang` / `setLang`（Task 2、3）
- Produces: `<ThemeLangControls />`（无 props）

- [ ] **Step 1: 实现 `ThemeLangControls.tsx`**

```tsx
import { useStore } from '../store/useStore'
import { useT } from '../i18n/useT'
import type { ThemeMode } from '../theme/theme'

const SunIcon = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
    <circle cx="8" cy="8" r="3.2" />
    <path d="M8 1v1.8M8 13.2V15M1 8h1.8M13.2 8H15M3.1 3.1l1.3 1.3M11.6 11.6l1.3 1.3M12.9 3.1l-1.3 1.3M4.4 11.6l-1.3 1.3" />
  </svg>
)

const AutoIcon = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
    <rect x="1.8" y="2.8" width="12.4" height="8.4" rx="1.2" />
    <path d="M5.5 13.2h5" />
  </svg>
)

const MoonIcon = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4">
    <path d="M13 9.8A5.6 5.6 0 0 1 6.2 3a5.6 5.6 0 1 0 6.8 6.8z" />
  </svg>
)

const MODES: { mode: ThemeMode; icon: () => JSX.Element; labelKey: 'theme.light' | 'theme.system' | 'theme.dark' }[] = [
  { mode: 'light', icon: SunIcon, labelKey: 'theme.light' },
  { mode: 'system', icon: AutoIcon, labelKey: 'theme.system' },
  { mode: 'dark', icon: MoonIcon, labelKey: 'theme.dark' },
]

export default function ThemeLangControls() {
  const t = useT()
  const themeMode = useStore(s => s.themeMode)
  const setThemeMode = useStore(s => s.setThemeMode)
  const lang = useStore(s => s.lang)
  const setLang = useStore(s => s.setLang)

  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex rounded-md border border-border overflow-hidden">
        {MODES.map(({ mode, icon: Icon, labelKey }) => (
          <button
            key={mode}
            type="button"
            onClick={() => setThemeMode(mode)}
            title={t(labelKey)}
            aria-label={t(labelKey)}
            aria-pressed={themeMode === mode}
            className={`px-2 py-1.5 transition-colors ${
              themeMode === mode
                ? 'bg-accent-soft text-accent'
                : 'text-fg-subtle hover:text-fg'
            }`}
          >
            <Icon />
          </button>
        ))}
      </div>
      <button
        type="button"
        onClick={() => setLang(lang === 'cn' ? 'en' : 'cn')}
        title={t('lang.switch')}
        aria-label={t('lang.switch')}
        className="text-xs font-medium rounded-md border border-border px-2 py-1.5 text-fg-muted hover:text-fg hover:border-border-strong transition-colors"
      >
        <span className={lang === 'cn' ? 'text-accent' : ''}>CN</span>
        <span className="text-fg-subtle mx-1">|</span>
        <span className={lang === 'en' ? 'text-accent' : ''}>EN</span>
      </button>
    </div>
  )
}
```

- [ ] **Step 2: 重写 `Sidebar.tsx`**

```tsx
import { NavLink } from 'react-router-dom'
import ThemeLangControls from './ThemeLangControls'
import { useT } from '../i18n/useT'
import type { I18nKey } from '../i18n'

const ChartIcon = () => (
  <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.4">
    <path d="M2 13.2h12M3.6 10.4V6.2M7.2 10.4V3.4M10.8 10.4V7.8" />
  </svg>
)

const AlertIcon = () => (
  <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.4">
    <path d="M8 2.6l5.6 9.8H2.4L8 2.6z" />
    <path d="M8 6.4v3M8 11.1v.6" />
  </svg>
)

const DbIcon = () => (
  <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.4">
    <ellipse cx="8" cy="4" rx="5.2" ry="2.2" />
    <path d="M2.8 4v8c0 1.2 2.3 2.2 5.2 2.2s5.2-1 5.2-2.2V4" />
    <path d="M2.8 8c0 1.2 2.3 2.2 5.2 2.2s5.2-1 5.2-2.2" />
  </svg>
)

const links: { to: string; labelKey: I18nKey; icon: () => JSX.Element }[] = [
  { to: '/', labelKey: 'nav.dashboard', icon: ChartIcon },
  { to: '/anomalies', labelKey: 'nav.anomalies', icon: AlertIcon },
  { to: '/funds', labelKey: 'nav.funds', icon: DbIcon },
]

export default function Sidebar() {
  const t = useT()
  return (
    <aside className="w-56 bg-surface border-r border-border flex flex-col shrink-0">
      <h2 className="px-5 py-5 text-sm font-semibold text-fg border-b border-border leading-snug">
        {t('nav.brand')}
      </h2>
      <nav className="flex-1 pt-2">
        {links.map(({ to, labelKey, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-2.5 px-5 py-2.5 text-sm transition-colors ${
                isActive
                  ? 'text-accent bg-accent-soft border-r-2 border-accent font-medium'
                  : 'text-fg-muted hover:text-fg hover:bg-sunken'
              }`
            }
          >
            <Icon />
            {t(labelKey)}
          </NavLink>
        ))}
      </nav>
      <div className="px-4 py-3 border-t border-border space-y-2">
        <ThemeLangControls />
        <div className="text-[11px] text-fg-subtle px-0.5">v0.1</div>
      </div>
    </aside>
  )
}
```

- [ ] **Step 3: 白名单删 Sidebar**

`src/i18n/no-hardcoded-cn.test.ts` 的 `PENDING_FILES` 删掉 `'components/Sidebar.tsx',` 这一行。

- [ ] **Step 4: 跑闸门 + 全量测试 + 构建**

Run: `cd webapp/frontend && npx vitest run && npm run build`
Expected: 全部 PASS。`no-hardcoded-cn` 里 `components/Sidebar.tsx` 这条从跳过变为实检并通过；若报 `ThemeLangControls.tsx` 有中文，检查是否误把文案写死。

- [ ] **Step 5: 浏览器实测端到端切换**

启动 dev server（用 preview_start，勿用 bash 起服务），打开 `/`：
- 点太阳/自动/月亮，整页配色立即变（侧栏、画布、卡片）；刷新后保持
- 点 CN|EN，侧栏三个导航项与品牌名变英文；刷新后保持
- 控制台零报错
- 系统切深色时，主题处于"自动"档位下页面跟着变

- [ ] **Step 6: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/components/ThemeLangControls.tsx webapp/frontend/src/components/Sidebar.tsx webapp/frontend/src/i18n/no-hardcoded-cn.test.ts
git commit -m "feat(frontend): 侧栏控制簇 — 三态主题分段器 + CN/EN 切换, Sidebar 去硬编码深墨底"
```

---

## Task 5: ECharts 调色板 + 防漂移对拍闸

**Files:**
- Create: `webapp/frontend/src/theme/chartTheme.ts`、`webapp/frontend/src/theme/useChartTheme.ts`
- Modify: `webapp/frontend/src/theme/palette.test.ts`（追加对拍用例）

**Interfaces:**
- Consumes: Task 1 的 CSS 变量、Task 2 的 `resolvedTheme`
- Produces:
  - `chartTheme.ts`：`interface ChartPalette { series: string[]; anchor: string; axisLabel: string; splitLine: string; baseline: string; spliceBorder: string; tooltipBg: string; tooltipBorder: string; tooltipFg: string; heatPos: string; heatNeg: string; heatEmpty: string }`、`CHART_TOKEN_MAP: Record<Exclude<keyof ChartPalette,'series'>, string>`、`chartPalettes: Record<ResolvedTheme, ChartPalette>`
  - `useChartTheme(): ChartPalette`

- [ ] **Step 1: 追加对拍测试**

在 `src/theme/palette.test.ts` 末尾追加（顶部 import 补 `chartPalettes` 与 `CHART_TOKEN_MAP`）：

```ts
import { CHART_TOKEN_MAP, chartPalettes } from './chartTheme'

describe('chartTheme 与 index.css 对拍（防漂移）', () => {
  const css = readFileSync(CSS_PATH, 'utf8')
  const vars = { light: parseCssVars(css, ':root'), dark: parseCssVars(css, ':root.dark') }

  it.each(['light', 'dark'] as const)('%s：非序列字段值与 CSS 变量一致', theme => {
    const mismatched = Object.entries(CHART_TOKEN_MAP)
      .filter(([field, token]) => {
        const expected = vars[theme][token]
        const actual = chartPalettes[theme][field as keyof typeof CHART_TOKEN_MAP]
        return expected !== actual
      })
      .map(([field, token]) => `${theme}.${field}: chartTheme="${
        chartPalettes[theme][field as keyof typeof CHART_TOKEN_MAP]
      }" vs ${token}="${vars[theme][token]}"`)
    expect(mismatched).toEqual([])
  })

  it.each(['light', 'dark'] as const)('%s：8 条序列色与 --series-1..8 一致', theme => {
    expect(chartPalettes[theme].series).toHaveLength(8)
    const mismatched = chartPalettes[theme].series
      .map((c, i) => (c === vars[theme][`--series-${i + 1}`] ? null : `series[${i}]=${c}`))
      .filter(Boolean)
    expect(mismatched).toEqual([])
  })

  it('CHART_TOKEN_MAP 引用的 token 都在 CSS 里存在', () => {
    const missing = Object.values(CHART_TOKEN_MAP).filter(tk => !vars.light[tk] || !vars.dark[tk])
    expect(missing).toEqual([])
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd webapp/frontend && npx vitest run src/theme/palette.test.ts`
Expected: FAIL，报无法解析 `./chartTheme`

- [ ] **Step 3: 实现 `chartTheme.ts`**

```ts
import type { ResolvedTheme } from './theme'

/**
 * ECharts 画布不解析 CSS 变量，只能给具体色值，所以这里是 index.css 的 TS 副本。
 * 两侧靠 palette.test.ts 对拍锁死：改一边不改另一边，测试报红。
 * heatPos / heatNeg 是空格分隔 RGB 三元组，ExcessHeatmap 要按超额绝对值合成 alpha。
 */
export interface ChartPalette {
  series: string[]
  anchor: string
  axisLabel: string
  splitLine: string
  baseline: string
  spliceBorder: string
  tooltipBg: string
  tooltipBorder: string
  tooltipFg: string
  heatPos: string
  heatNeg: string
  heatEmpty: string
}

/** 字段 → index.css 变量名。对拍测试据此逐项比对。 */
export const CHART_TOKEN_MAP: Record<Exclude<keyof ChartPalette, 'series'>, string> = {
  anchor: '--anchor',
  axisLabel: '--fg-subtle',
  splitLine: '--grid',
  baseline: '--border-strong',
  spliceBorder: '--fg-muted',
  tooltipBg: '--tooltip-bg',
  tooltipBorder: '--tooltip-border',
  tooltipFg: '--tooltip-fg',
  heatPos: '--heat-pos',
  heatNeg: '--heat-neg',
  heatEmpty: '--sunken',
}

export const chartPalettes: Record<ResolvedTheme, ChartPalette> = {
  light: {
    series: ['#2478c4', '#6f42c1', '#e8590c', '#2f9e44', '#c99a06', '#d6336c', '#0c8599', '#9c36b5'],
    anchor: '#14181d',
    axisLabel: '#9aa3ad',
    splitLine: '#eef0f3',
    baseline: '#cfd4da',
    spliceBorder: '#6b7480',
    tooltipBg: '#ffffff',
    tooltipBorder: '#e3e6ea',
    tooltipFg: '#14181d',
    heatPos: '42 120 214',
    heatNeg: '227 73 72',
    heatEmpty: '#f1f3f5',
  },
  dark: {
    series: ['#4dabf7', '#a78bfa', '#ff922b', '#51cf66', '#ffd43b', '#f783ac', '#22b8cf', '#da77f2'],
    anchor: '#f0f4f8',
    axisLabel: '#6e7a87',
    splitLine: '#21272f',
    baseline: '#3a4552',
    spliceBorder: '#9aa5b1',
    tooltipBg: '#1c232c',
    tooltipBorder: '#273040',
    tooltipFg: '#e6edf3',
    heatPos: '77 155 233',
    heatNeg: '240 105 100',
    heatEmpty: '#1c232c',
  },
}
```

- [ ] **Step 4: 实现 `useChartTheme.ts`**

```ts
import { useStore } from '../store/useStore'
import { chartPalettes, type ChartPalette } from './chartTheme'

/**
 * 图表调色板。调用方必须把 resolvedTheme 也放进 option 的 useMemo 依赖，
 * 否则切主题时 ECharts option 不重建、画布不重绘。
 */
export function useChartTheme(): ChartPalette {
  const resolvedTheme = useStore(s => s.resolvedTheme)
  return chartPalettes[resolvedTheme]
}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd webapp/frontend && npx vitest run src/theme/palette.test.ts`
Expected: PASS

- [ ] **Step 6: 故意制造漂移，验证闸门真的会报红**

临时把 `chartPalettes.dark.anchor` 改成 `'#ffffff'`，跑 `npx vitest run src/theme/palette.test.ts`
Expected: FAIL，报 `dark.anchor: chartTheme="#ffffff" vs --anchor="#f0f4f8"`
然后改回 `'#f0f4f8'`，重跑 Expected: PASS

- [ ] **Step 7: 全量测试 + 构建 + 提交**

Run: `cd webapp/frontend && npx vitest run && npm run build`

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/theme
git commit -m "feat(frontend): ECharts 调色板双主题 + 与 index.css 对拍防漂移闸"
```

---

## Task 6: 基础组件 token 化（Layout / MetricCard / FundChips / WarnBadge / ErrorBoundary）

**Files:**
- Modify: `webapp/frontend/src/components/Layout.tsx`（13 行）
- Modify: `webapp/frontend/src/components/MetricCard.tsx`（34 行）
- Modify: `webapp/frontend/src/components/FundChips.tsx`（28 行）
- Modify: `webapp/frontend/src/components/WarnBadge.tsx`（20 行）
- Modify: `webapp/frontend/src/components/ErrorBoundary.tsx`（32 行）
- Modify: `webapp/frontend/src/i18n/cn.ts`、`en.ts`
- Modify: `webapp/frontend/src/i18n/no-hardcoded-cn.test.ts`（白名单删 `components/ErrorBoundary.tsx`、`components/MetricCard.tsx`）

**Interfaces:**
- Consumes: `useT()`、Task 1 的 `.card` / `.num` 类
- Produces: `MetricCard` props 不变（`label` 仍是已翻译好的字符串，由调用方传入）

- [ ] **Step 1: 字典加 key**

`cn.ts` 在 `'lang.switch'` 之前插入：

```ts
  'error.boundaryTitle': '出错了',
  'metric.rankTitle': '当前口径下名次',
```

`en.ts` 对应位置插入：

```ts
  'error.boundaryTitle': 'Something went wrong',
  'metric.rankTitle': 'Rank under the current basis',
```

- [ ] **Step 2: 重写 `Layout.tsx`**

```tsx
import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'

export default function Layout() {
  return (
    <div className="flex h-screen bg-bg text-fg">
      <Sidebar />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-[1600px]">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
```

- [ ] **Step 3: 重写 `MetricCard.tsx`**

```tsx
import WarnBadge from './WarnBadge'
import { useT } from '../i18n/useT'

interface Props {
  label: string
  value: string | number
  rank?: number
  /** 副文本：最大回撤卡片显示恢复月数 */
  subtext?: string
  /** 小样本警告：名次置灰 + 角标 tooltip（PDD 1.5） */
  warn?: boolean
  warnNote?: string
}

export default function MetricCard({ label, value, rank, subtext, warn, warnNote }: Props) {
  const t = useT()
  return (
    <div className="card flex-1 min-w-[150px] p-4">
      <div className="text-xs text-fg-muted mb-1.5">
        {label}
        {warn && warnNote && <WarnBadge note={warnNote} />}
      </div>
      <div className="flex items-baseline gap-2">
        <div className="text-xl font-semibold font-mono tabular-nums text-fg">{value ?? '-'}</div>
        {rank != null && (
          <span
            title={t('metric.rankTitle')}
            className={`text-[11px] font-medium rounded px-1.5 py-0.5 ${
              warn ? 'bg-sunken text-fg-subtle' : 'bg-accent-soft text-accent'
            }`}
          >
            #{rank}
          </span>
        )}
      </div>
      {subtext && <div className="text-xs text-fg-subtle mt-1.5">{subtext}</div>}
    </div>
  )
}
```

- [ ] **Step 4: 改 `FundChips.tsx` 的 className**

`className` 表达式整体替换为：

```tsx
            className={`px-3.5 py-1.5 rounded-full text-sm border transition-colors ${
              active
                ? 'border-accent bg-accent-soft text-accent font-medium'
                : 'border-border bg-surface text-fg-muted hover:border-border-strong hover:text-fg'
            }`}
```

- [ ] **Step 5: 改 `WarnBadge.tsx` 与 `ErrorBoundary.tsx`**

`WarnBadge.tsx`：把角标的颜色类按全局映射改为 `text-warn`（`⚠` 符号保留，不是中文字面量）。若原来有 `text-amber-*` / `bg-amber-*`，改 `text-warn` / `bg-warn-soft`。

`ErrorBoundary.tsx`：`出错了` 改为 `{t('error.boundaryTitle')}`。注意 `ErrorBoundary` 是 class 组件，不能用 hook——改成把标题下沉到一个函数子组件：

```tsx
import { useT } from '../i18n/useT'

function ErrorFallback({ message }: { message: string }) {
  const t = useT()
  return (
    <div className="card m-6 p-6">
      <h2 className="text-base font-semibold text-neg mb-2">{t('error.boundaryTitle')}</h2>
      <pre className="text-xs text-fg-muted whitespace-pre-wrap">{message}</pre>
    </div>
  )
}
```

`render()` 里错误分支返回 `<ErrorFallback message={String(this.state.error)} />`，其余 class 组件逻辑（`getDerivedStateFromError` / `componentDidCatch`）不动。

- [ ] **Step 6: 白名单删两个文件**

`PENDING_FILES` 删掉 `'components/ErrorBoundary.tsx',` 与 `'components/MetricCard.tsx',`。

- [ ] **Step 7: 全量测试 + 构建**

Run: `cd webapp/frontend && npx vitest run && npm run build`
Expected: 全部 PASS

- [ ] **Step 8: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/components/{Layout,MetricCard,FundChips,WarnBadge,ErrorBoundary}.tsx webapp/frontend/src/i18n
git commit -m "style(frontend): 基础组件 token 化 — Layout/MetricCard/FundChips/WarnBadge/ErrorBoundary"
```

---

## Task 7: 图表双主题接入（NavChart + RollingExcessChart）

**Files:**
- Modify: `webapp/frontend/src/components/NavChart.tsx`（227 行）
- Modify: `webapp/frontend/src/components/RollingExcessChart.tsx`（108 行）
- Modify: `webapp/frontend/src/i18n/cn.ts`、`en.ts`
- Modify: `webapp/frontend/src/i18n/no-hardcoded-cn.test.ts`（白名单删这两个文件）

**Interfaces:**
- Consumes: `useChartTheme()`（Task 5）、`useT()`（Task 3）、store 的 `resolvedTheme`
- Produces: 无新导出

- [ ] **Step 1: 字典加 key**

`cn.ts` 追加：

```ts
  'chart.tabNav': '累计 NAV',
  'chart.tabRolling': '滚动12月超额',
  'chart.titleNav': '累计 NAV / 回撤',
  'chart.titleRolling': '滚动 12 月超额',
  'chart.baseStart': '起点',
  'chart.noData': '无数据',
  'chart.spliceTip': '{name}：拼接基点，等于锚定基金 {month} 累计值，次月起为该基金自身收益',
  'chart.insufficientHistory': '{name}（历史不足12个月）',
```

`en.ts` 追加：

```ts
  'chart.tabNav': 'Cumulative NAV',
  'chart.tabRolling': 'Rolling 12m excess',
  'chart.titleNav': 'Cumulative NAV / drawdown',
  'chart.titleRolling': 'Rolling 12-month excess',
  'chart.baseStart': 'Base',
  'chart.noData': 'No data',
  'chart.spliceTip': '{name}: splice base point, equal to the anchor fund cumulative value at {month}; from the next month onward this is the fund own return',
  'chart.insufficientHistory': '{name} (less than 12 months of history)',
```

- [ ] **Step 2: `NavChart.tsx` 接主题与取文**

删掉第 25 行的 `const COLORS = [...]`。组件内 `useMemo` 之前加：

```tsx
  const t = useT()
  const palette = useChartTheme()
  const resolvedTheme = useStore(s => s.resolvedTheme)
  const lang = useStore(s => s.lang)
```

import 追加：

```tsx
import { useT } from '../i18n/useT'
import { useChartTheme } from '../theme/useChartTheme'
```

色值逐处替换：

| 原值 | 替换 |
|---|---|
| `COLORS[i % COLORS.length]` | `palette.series[i % palette.series.length]` |
| `r.isAnchor ? '#000' : undefined`（两处 `lineStyle.color`） | `r.isAnchor ? palette.anchor : undefined` |
| `r.isAnchor ? '#000' : COLORS[...]`（两处 `itemStyle.color`） | `r.isAnchor ? palette.anchor : palette.series[i % palette.series.length]` |
| `markLine.lineStyle.color: '#bbb'` | `palette.baseline` |
| `markLine.label.color: '#999'` | `palette.axisLabel` |
| `markLine.label.formatter: '起点'` | `t('chart.baseStart')` |
| 拼接点 `borderColor: '#555'` | `palette.spliceBorder` |
| 4 处 `axisLabel.color: '#999'` | `palette.axisLabel` |
| 4 处 `splitLine.lineStyle.color: '#f0f0f0'` | `palette.splitLine` |

tooltip 段改为（深色适配 + 取文）：

```tsx
      tooltip: {
        trigger: 'axis',
        axisPointer: { link: [{ xAxisIndex: 'all' }] },
        backgroundColor: palette.tooltipBg,
        borderColor: palette.tooltipBorder,
        textStyle: { color: palette.tooltipFg },
        formatter: (params: any[]) => {
          if (!params || params.length === 0) return ''
          const date = params[0]?.axisValue ?? ''
          const lines = params
            .filter((p: any) => p.seriesType === 'line' && p.seriesId?.startsWith('nav:'))
            .sort((a: any, b: any) => (b.data ?? -Infinity) - (a.data ?? -Infinity))
            .map((p: any) => `${p.marker} ${p.seriesName}: ${
              p.data == null ? t('chart.noData') : p.data.toFixed(4)}`)
          return `<div style="font-weight:500;margin-bottom:4px">${date}</div>${lines.join('<br/>')}`
        },
      },
```

`legend.textStyle` 补色：`legend: { top: 0, textStyle: { fontSize: 12, color: palette.axisLabel } },`

拼接点 tooltip formatter 改为：

```tsx
        tooltip: {
          formatter: () => t('chart.spliceTip', {
            name: codeMap.get(r.fund_id) ?? r.fund_name,
            month: r.splicePoint!.month,
          }),
        },
```

图表切换按钮的 `累计 NAV` / `滚动12月超额` 文案改 `t('chart.tabNav')` / `t('chart.tabRolling')`，`title` 属性的 `累计 NAV / 回撤` / `滚动 12 月超额` 改 `t('chart.titleNav')` / `t('chart.titleRolling')`，`加载中...` 改 `t('common.loading')`。按钮/容器类名按全局映射 token 化（容器用 `.card`）。

**关键**：`option` 的 `useMemo` 依赖数组末尾补 `palette, t`（`palette` 随 `resolvedTheme` 变，`t` 随 `lang` 变，两者都是稳定引用，可直接作为依赖）。

- [ ] **Step 3: `RollingExcessChart.tsx` 同样处理**

删掉第 14 行本地 `COLORS` 副本，改用 `useChartTheme()`；替换 `#000`（锚定）→ `palette.anchor`、`#bbb`（基准线）→ `palette.baseline`、两处 `#999` → `palette.axisLabel`、`#f0f0f0` → `palette.splitLine`；tooltip 补 `backgroundColor` / `borderColor` / `textStyle`，其 formatter 里的 `'无数据'` 改 `t('chart.noData')`；`（历史不足12个月）` 拼接改 `t('chart.insufficientHistory', { name })`；`加载中...` 改 `t('common.loading')`；`useMemo` 依赖补 `palette, t`。

- [ ] **Step 4: 白名单删两个文件**

`PENDING_FILES` 删掉 `'components/NavChart.tsx',` 与 `'components/RollingExcessChart.tsx',`。

- [ ] **Step 5: 全量测试 + 构建**

Run: `cd webapp/frontend && npx vitest run && npm run build`
Expected: 全部 PASS。`rebase.test.ts` 必须仍全绿（证明未碰计算）。

- [ ] **Step 6: 浏览器实测**

打开 `/`，选中 2 支以上基金：
- 切深色：曲线颜色、轴标签、分割线、tooltip 全部跟着变，**无需刷新**（验证 `useMemo` 依赖已补 `palette`）
- 点表格某行锚定：锚定曲线在深色下是亮色（`--anchor`）而非黑色，可见
- 切 EN：基准线标签变 `Base`，tooltip 缺数据处变 `No data`，页签变 `Cumulative NAV`
- 控制台零报错

- [ ] **Step 7: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/components/{NavChart,RollingExcessChart}.tsx webapp/frontend/src/i18n
git commit -m "feat(frontend): NavChart/RollingExcessChart 接双主题调色板 + 图内文案入字典"
```

---

## Task 8: 热力图双主题（ExcessHeatmap）

**Files:**
- Modify: `webapp/frontend/src/components/ExcessHeatmap.tsx`（107 行）
- Modify: `webapp/frontend/src/i18n/cn.ts`、`en.ts`
- Modify: `webapp/frontend/src/i18n/no-hardcoded-cn.test.ts`（白名单删该文件）

**Interfaces:**
- Consumes: `useChartTheme()` 的 `heatPos` / `heatNeg` / `heatEmpty`、`useT()`
- Produces: 无新导出

- [ ] **Step 1: 字典加 key**

`cn.ts` 追加：

```ts
  'heatmap.year': '年',
  'heatmap.cellNoData': '{ym}：无数据',
  'heatmap.cellTip': '{ym}\n基金月收益: {fund}%\n基准月收益: {bench}%\n超额: {excess}%',
```

`en.ts` 追加：

```ts
  'heatmap.year': 'Year',
  'heatmap.cellNoData': '{ym}: no data',
  'heatmap.cellTip': '{ym}\nFund monthly return: {fund}%\nBenchmark monthly return: {bench}%\nExcess: {excess}%',
```

- [ ] **Step 2: 改色值来源**

第 47–52 行附近的着色函数改为读调色板（保留"缺月不插值"的语义，只换视觉权重）：

```tsx
  const palette = useChartTheme()
  const t = useT()

  // 蓝(正超额)/红(负超额)——红绿色盲验证过的发散色对，基色随主题切换。
  // 缺月格用 sunken 底：它代表禁插值的真实缺口，视觉权重必须低于有数据的格子。
  const cellColor = (e: number | null) => {
    if (e == null) return palette.heatEmpty
    const a = Math.min(1, Math.abs(e) / MAX_ABS_EXCESS)
    return e >= 0 ? `rgba(${palette.heatPos} / ${a})` : `rgba(${palette.heatNeg} / ${a})`
  }
```

> 注意：`rgba(R G B / a)` 是 CSS Color 4 空格语法，现代浏览器均支持，与三元组 token 直接拼接最省事。若实现时发现某目标浏览器不支持，改成 `` `rgba(${palette.heatPos.split(' ').join(',')},${a})` ``。

原有 `MAX_ABS_EXCESS`（或等价的 alpha 归一常量）沿用文件里已有的定义，不改数值。

- [ ] **Step 3: 文案入字典**

`年` 改 `t('heatmap.year')`；缺数据 tooltip 改 `t('heatmap.cellNoData', { ym })`；有数据 tooltip 改：

```tsx
t('heatmap.cellTip', {
  ym,
  fund: ((p!.fundReturn as number) * 100).toFixed(3),
  bench: (monthlyBench(p!.rbaRate as number) * 100).toFixed(3),
  excess: (e * 100).toFixed(3),
})
```

其中 `ym` 沿用原来的 `` `${y}-${String(mn).padStart(2, '0')}` `` 计算。容器与标题类名按全局映射 token 化（容器用 `.card`）。

- [ ] **Step 4: 白名单删该文件 + 测试构建**

`PENDING_FILES` 删 `'components/ExcessHeatmap.tsx',`

Run: `cd webapp/frontend && npx vitest run && npm run build`
Expected: 全部 PASS

- [ ] **Step 5: 浏览器实测（重点：缺口格不得抢眼）**

打开 `/`，锚定一支有数据缺口的基金（`/funds` 页可看哪支有 gap），切深色：
- 缺月格是暗底（与卡片底接近），**不是亮块**
- 正/负超额格子在深色底上可辨、渐变仍单调
- 悬停 tooltip 三行数值与切换语言前一致（数值不受语言影响）

- [ ] **Step 6: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/components/ExcessHeatmap.tsx webapp/frontend/src/i18n
git commit -m "feat(frontend): 热力图双主题发散色 + 缺月格改 sunken 底(缺口不得比数据抢眼)"
```

---

## Task 9: 对比表与去平滑卡（CompareTable + SmoothingCards）

**Files:**
- Modify: `webapp/frontend/src/components/CompareTable.tsx`（182 行）
- Modify: `webapp/frontend/src/components/SmoothingCards.tsx`（157 行）
- Modify: `webapp/frontend/src/i18n/cn.ts`、`en.ts`
- Modify: `webapp/frontend/src/i18n/no-hardcoded-cn.test.ts`（白名单删这两个文件）

**Interfaces:**
- Consumes: `useT()`、`.card` / `.num` / `.th` 类
- Produces: 无新导出（`rankBy` / `fmt` 等内部函数签名不变）

- [ ] **Step 1: 字典加 key**

`cn.ts` 追加：

```ts
  'table.title': '指标对比',
  'table.fundName': '基金名称',
  'table.annReturn': '年化收益率',
  'table.annExcess': '年化超额收益',
  'table.sharpe': '夏普比率（超额）',
  'table.maxDrawdown': '最大回撤',
  'table.winRate': '超额胜率',
  'table.longestUnderperform': '最长跑输',
  'table.annVol': '年化波动率',
  'table.rowHint': '点击行锚定该基金（与点击曲线等效）· 再次点击同一行取消锚定',
  'table.anchorOn': '点击锚定该基金（同点击曲线）',
  'table.anchorOff': '再次点击取消锚定',
  'table.anchorMode': '锚定模式',
  'table.anchorWindow': '锚定窗口: {startMonth} 起 · 各基金至自身最新月份',
  'table.currentWindow': '当前窗口: {startYM} 至 {endYM} (n={n})',

  'smoothing.phi': '自相关系数 φ',
  'smoothing.months': '数据月数',
  'smoothing.interventionProb': '人为干预概率',
  'smoothing.unknown': '无法判定',
  'smoothing.fw1Fail': '防火墙 1 未通过：历史数据 {n} 个月，不足 36 个月，无法检验自相关性',
  'smoothing.allPass': '三重防火墙全部通过，已应用 Geltner 去平滑',
  'smoothing.phiPosWeak': 'φ 为正但未达显著，建议持续观测',
  'smoothing.notSignificant': '自相关性不显著（φ≈0 或 Q 检验未通过），无需去平滑',
```

`en.ts` 追加：

```ts
  'table.title': 'Metric comparison',
  'table.fundName': 'Fund',
  'table.annReturn': 'Ann. return',
  'table.annExcess': 'Ann. excess',
  'table.sharpe': 'Sharpe (excess)',
  'table.maxDrawdown': 'Max drawdown',
  'table.winRate': 'Excess win rate',
  'table.longestUnderperform': 'Longest underperformance',
  'table.annVol': 'Ann. volatility',
  'table.rowHint': 'Click a row to anchor that fund (same as clicking its line) · click again to unanchor',
  'table.anchorOn': 'Click to anchor this fund (same as clicking its line)',
  'table.anchorOff': 'Click again to unanchor',
  'table.anchorMode': 'Anchored',
  'table.anchorWindow': 'Anchor window: from {startMonth} · each fund up to its own latest month',
  'table.currentWindow': 'Current window: {startYM} to {endYM} (n={n})',

  'smoothing.phi': 'Autocorrelation φ',
  'smoothing.months': 'Months of data',
  'smoothing.interventionProb': 'Manual intervention probability',
  'smoothing.unknown': 'Undetermined',
  'smoothing.fw1Fail': 'Firewall 1 not passed: {n} months of history, below the 36-month minimum, autocorrelation cannot be tested',
  'smoothing.allPass': 'All three firewalls passed; Geltner unsmoothing applied',
  'smoothing.phiPosWeak': 'φ is positive but not significant; keep monitoring',
  'smoothing.notSignificant': 'Autocorrelation not significant (φ≈0 or Q-test not passed); no unsmoothing needed',
```

- [ ] **Step 2: `CompareTable.tsx` 文案入字典**

`无回撤` → `t('common.noDrawdown')`；`` `恢复${recoveryMonths}个月` `` → `t('common.recovered', { n: recoveryMonths })`；`` `未恢复(已${recoveryMonths}个月)` `` → `t('common.notRecovered', { n: recoveryMonths })`；`` `${...} 个月` ``（最长跑输）→ `t('common.months', { n: ... })`；`锚定窗口: ...` → `t('table.anchorWindow', { startMonth })`；`当前窗口: ...` → `t('table.currentWindow', { startYM, endYM, n })`；`全部区间` → `t('dashboard.periodFull')`（该 key 在 Task 11 加；本 Task 先在 `cn.ts`/`en.ts` 里一并加上：`'dashboard.periodFull': '全部区间'` / `'All periods'`）；`锚定模式` → `t('table.anchorMode')`；8 个表头 → 对应 `table.*`；两处 `样本不足...` → `t('common.smallSample', { n: r.n })`；行 hint → `t('table.rowHint')`；两个 `title` → `t('table.anchorOff')` / `t('table.anchorOn')`。`●`（锚定标记）保留符号。

- [ ] **Step 3: `CompareTable.tsx` 视觉改造**

- 外层容器：`.card overflow-hidden`，内加 `overflow-x-auto max-h-[70vh] overflow-y-auto`（配合 `.th` 吸顶）
- `<thead>` 的 `<th>` 一律加 `.th` 类 + `px-3 py-2`；数值列 `<th>` 额外 `text-right`
- 数值 `<td>` 一律加 `num px-3 py-2`（等宽 + tabular-nums + 右对齐）；基金名列左对齐 `px-3 py-2 text-fg`
- 行样式：`odd:bg-surface even:bg-sunken/40` 不可用（`/opacity` 对 var 无效），改 `even:bg-sunken`
- 行 hover：`hover:bg-accent-soft cursor-pointer transition-colors`
- 锚定行：`border-l-2 border-accent bg-accent-soft`
- 年化超额与最大回撤两列按正负着色：`value >= 0 ? 'text-pos' : 'text-neg'`（`fmt()` 返回字符串，取符号用原始数值判断，不要解析字符串）
- 名次徽标沿用 `MetricCard` 同款：`text-[11px] rounded px-1.5 py-0.5 bg-accent-soft text-accent`，小样本置灰版 `bg-sunken text-fg-subtle`

- [ ] **Step 4: `SmoothingCards.tsx` 文案入字典 + token 化**

4 条状态说明 → `smoothing.fw1Fail`（带 `{n}`）/ `smoothing.allPass` / `smoothing.phiPosWeak` / `smoothing.notSignificant`；`自相关系数 φ` / `数据月数` / `人为干预概率` / `无法判定` → 对应 key；`✓` / `✗` 保留符号。卡片容器改 `.card`，数值加 `font-mono tabular-nums`，颜色按全局映射（通过态 `text-pos`，未通过 `text-fg-subtle`，告警 `text-warn`）。

- [ ] **Step 5: 白名单删两个文件 + 测试构建**

`PENDING_FILES` 删 `'components/CompareTable.tsx',` 与 `'components/SmoothingCards.tsx',`

Run: `cd webapp/frontend && npx vitest run && npm run build`
Expected: 全部 PASS

- [ ] **Step 6: 浏览器实测**

- 表格数字右对齐等宽，滚动时表头吸顶
- 正超额绿、负超额红，深浅两主题下都可读
- 点行锚定：左侧强调边出现，再点取消
- 切 EN：8 个表头、窗口说明、行提示全英
- 控制台零报错

- [ ] **Step 7: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/components/{CompareTable,SmoothingCards}.tsx webapp/frontend/src/i18n
git commit -m "feat(frontend): 对比表吸顶表头/等宽数字/正负着色 + 去平滑卡 token 化, 文案入字典"
```

---

## Task 10: 异常审计（AnomalyTable + Anomalies 页）

**Files:**
- Modify: `webapp/frontend/src/components/AnomalyTable.tsx`（183 行）
- Modify: `webapp/frontend/src/pages/Anomalies.tsx`（35 行）
- Modify: `webapp/frontend/src/i18n/cn.ts`、`en.ts`
- Modify: `webapp/frontend/src/i18n/no-hardcoded-cn.test.ts`（白名单删这两个文件）

**Interfaces:**
- Consumes: `useT()`
- Produces: 无新导出

- [ ] **Step 1: 字典加 key**

`cn.ts` 追加：

```ts
  'anomaly.title': '异常审计',
  'anomaly.loading': '加载异常数据...',
  'anomaly.fund': '基金',
  'anomaly.type': '类型',
  'anomaly.date': '日期',
  'anomaly.return': '收益率',
  'anomaly.threshold': '阈值',
  'anomaly.median': '中位数',
  'anomaly.stdev': '标准差',
  'anomaly.actionReason': '操作/原因',
  'anomaly.invalidNumber': '请输入有效数字',
  'anomaly.returnTooLarge': '收益率绝对值应小于 100%',
  'anomaly.rbaMissing': 'RBA 基准缺失',
```

`en.ts` 追加：

```ts
  'anomaly.title': 'Anomaly audit',
  'anomaly.loading': 'Loading anomalies…',
  'anomaly.fund': 'Fund',
  'anomaly.type': 'Type',
  'anomaly.date': 'Date',
  'anomaly.return': 'Return',
  'anomaly.threshold': 'Threshold',
  'anomaly.median': 'Median',
  'anomaly.stdev': 'Std dev',
  'anomaly.actionReason': 'Action / reason',
  'anomaly.invalidNumber': 'Enter a valid number',
  'anomaly.returnTooLarge': 'Absolute return must be below 100%',
  'anomaly.rbaMissing': 'RBA benchmark missing',
```

- [ ] **Step 2: 替换文案 + token 化**

`AnomalyTable.tsx`：8 个表头 → `anomaly.*`；两条校验提示（`请输入有效数字` / `收益率绝对值应小于 100%`）→ 对应 key；`RBA 基准缺失` → `t('anomaly.rbaMissing')`。表格结构套 Task 9 同一套（`.card` + `.th` + `.num`），输入框 `border-border-strong bg-surface text-fg`，校验错误文字 `text-neg`。

`Anomalies.tsx`：`异常审计` → `t('anomaly.title')`；`加载异常数据...` → `t('anomaly.loading')`；类名 token 化。

- [ ] **Step 3: 白名单删两个文件 + 测试构建**

Run: `cd webapp/frontend && npx vitest run && npm run build`
Expected: 全部 PASS

- [ ] **Step 4: 浏览器实测**

`/anomalies` 页：深浅两主题 × CN/EN 下表头、输入框、校验提示都正常；订正输入框在深色下文字可读（不是黑字黑底）。

- [ ] **Step 5: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/components/AnomalyTable.tsx webapp/frontend/src/pages/Anomalies.tsx webapp/frontend/src/i18n
git commit -m "feat(frontend): 异常审计页 token 化 + 文案入字典"
```

---

## Task 11: 对比看板页（Dashboard）

**Files:**
- Modify: `webapp/frontend/src/pages/Dashboard.tsx`（165 行）
- Modify: `webapp/frontend/src/i18n/cn.ts`、`en.ts`
- Modify: `webapp/frontend/src/i18n/no-hardcoded-cn.test.ts`（白名单删该文件）

**Interfaces:**
- Consumes: `useT()`、`MetricCard`（Task 6）
- Produces: 无新导出

- [ ] **Step 1: 字典加 key**

`cn.ts` 追加（`dashboard.periodFull` 若 Task 9 已加则不重复）：

```ts
  'dashboard.title': '对比看板',
  'dashboard.periodFull': '全部区间',
  'dashboard.period3y': '近3年',
  'dashboard.period1y': '近1年',
  'dashboard.periodCommon': '共同区间',
  'dashboard.periodDisabledHint': '锚定模式下展示锚定基金完整历史',
  'dashboard.smoothingOriginal': '原始',
  'dashboard.smoothingUnsmoothed': '去平滑',
  'dashboard.loadingFunds': '加载基金列表...',
  'dashboard.fundsLoadFailed': '基金列表加载失败：',
  'dashboard.metricsLoadFailed': '指标加载失败：',
  'dashboard.noFunds': '暂无基金数据，请先在基金管理页添加基金',
  'dashboard.excludedNotice': '{ids} 因数据缺口已排除对比，请到基金管理页查看详情',
  'dashboard.pickAnchorShort': '点击曲线或下方列表行锚定基金查看详情',
  'dashboard.pickAnchorLong': '点击下方曲线或指标对比列表中的一行锚定基金，查看其完整历史指标卡片与月度超额热力图',
  'dashboard.showing': '当前展示：',
  'dashboard.historyMonths': '（{n} 个月历史）',
  'metric.excess': '年化超额收益',
  'metric.sharpe': '夏普比率（超额）',
  'metric.winRate': '超额胜率',
  'metric.maxDrawdown': '最大回撤',
```

`en.ts` 追加：

```ts
  'dashboard.title': 'Dashboard',
  'dashboard.periodFull': 'All periods',
  'dashboard.period3y': 'Last 3 years',
  'dashboard.period1y': 'Last 1 year',
  'dashboard.periodCommon': 'Common period',
  'dashboard.periodDisabledHint': 'Anchored mode shows the full history of the anchor fund',
  'dashboard.smoothingOriginal': 'Original',
  'dashboard.smoothingUnsmoothed': 'Unsmoothed',
  'dashboard.loadingFunds': 'Loading fund list…',
  'dashboard.fundsLoadFailed': 'Failed to load fund list: ',
  'dashboard.metricsLoadFailed': 'Failed to load metrics: ',
  'dashboard.noFunds': 'No fund data yet. Add a fund on the fund management page first.',
  'dashboard.excludedNotice': '{ids} excluded from comparison due to data gaps; see the fund management page for details',
  'dashboard.pickAnchorShort': 'Click a line or a row below to anchor a fund and see its details',
  'dashboard.pickAnchorLong': 'Click a line below, or a row in the metric comparison table, to anchor a fund and see its full-history metric cards and monthly excess heatmap',
  'dashboard.showing': 'Showing: ',
  'dashboard.historyMonths': '({n} months of history)',
  'metric.excess': 'Ann. excess return',
  'metric.sharpe': 'Sharpe ratio (excess)',
  'metric.winRate': 'Excess win rate',
  'metric.maxDrawdown': 'Max drawdown',
```

> `dashboard.noFunds` 的中文从原文 `暂无基金数据，请先通过 skills 端添加基金` 改掉——`skills/` 管道已于 2026-07 删除，这句话现在是错的指引（README 已记录该架构变更）。

- [ ] **Step 2: 替换文案**

逐处替换：`加载基金列表...`、`基金列表加载失败：`、`暂无基金数据...`、`对比看板`、4 个 period 选项、`锚定模式下展示...`（`title` 属性）、2 个 smoothing 选项、`指标加载失败：`、排除提示（`、` 用 `t('common.listSeparator')` 连接 id，整句用 `t('dashboard.excludedNotice', { ids })`）、`点击曲线或下方列表行锚定基金查看详情`、`当前展示：`、`（{n} 个月历史）`、空态长句、4 个 `MetricCard` 的 `label`、`样本不足...`（`t('common.smallSample', { n })`）、`无回撤` / `恢复 {n} 个月` / `未恢复(已 {n} 个月)`。

排除提示里原本有个 `<a href="/funds">` 硬跳转，改成 `react-router` 的 `<Link to="/funds">`，`className="underline"` 保留，链接文字用 `t('nav.funds')`。

- [ ] **Step 3: token 化 + 空态重做**

- 两个 `<select>`：`text-sm border border-border rounded-md px-3 py-1.5 bg-surface text-fg disabled:bg-sunken disabled:text-fg-subtle`
- 错误横幅：`bg-neg-soft border border-neg-border text-neg text-sm rounded-lg p-4 mb-5`
- 排除提示横幅：`bg-warn-soft border border-warn-border text-warn text-sm rounded-lg p-3 mb-5`
- 空态卡：`bg-sunken border border-dashed border-border rounded-lg p-6 mb-6 text-center text-sm text-fg-subtle`
- 标题：`text-xl font-semibold text-fg`；`当前展示` 一行：`text-sm text-fg-muted`，基金名 `font-medium text-fg`

- [ ] **Step 4: 白名单删该文件 + 测试构建**

Run: `cd webapp/frontend && npx vitest run && npm run build`
Expected: 全部 PASS

- [ ] **Step 5: 浏览器实测**

`/` 页：4 个指标卡数字等宽、名次徽标；两个下拉在深色下可读；锚定模式下 period 下拉置灰且 hover 有提示；切 EN 全页无残留中文（后端错误消息除外）。

- [ ] **Step 6: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/pages/Dashboard.tsx webapp/frontend/src/i18n
git commit -m "feat(frontend): 看板页 token 化 + 文案入字典, 修正过时的 skills 端添加基金指引"
```

---

## Task 12: 拆分 FundManagement（纯搬移，不改样式不改文案）

**Files:**
- Create: `webapp/frontend/src/pages/funds/FundTable.tsx`、`AddFundPanel.tsx`、`IngestJobPanel.tsx`、`PendingReviewPanel.tsx`、`FundDataDrawer.tsx`
- Modify: `webapp/frontend/src/pages/FundManagement.tsx`（742 行 → 约 150 行编排）
- Modify: `webapp/frontend/src/i18n/no-hardcoded-cn.test.ts`（新文件默认被扫描，须把 5 个新文件加入 `PENDING_FILES`，Task 13 再删）

**Interfaces:**
- Consumes: 现有 store 方法（`recomputeFund` / `deleteFund` / `fetchFunds`）、`src/api/client.ts` 的现有方法、`src/types/index.ts` 的 `Fund` / `IngestJob` / `PendingReview` / `MonthlyReturnRow`
- Produces:
  - `FundTable`：`{ funds: Fund[]; activeJobs: Record<string,string>; recomputing: string|null; updatingFundId: string|null; deleteConfirm: string|null; onRecompute(id): void; onUpdate(fund): void; onToggleHidden(fund): void; onRequestDelete(id): void; onConfirmDelete(id): void; onCancelDelete(): void; onOpenReview(fundId): void; onOpenData(fund): void }`
  - `AddFundPanel`：`{ open: boolean; form: AddFormState; setForm(next): void; showAdvanced: boolean; setShowAdvanced(v): void; error: string; submitting: boolean; onSubmit(): void; onClose(): void }`
  - `IngestJobPanel`：`{ job: IngestJob | null }`
  - `PendingReviewPanel`：`{ fundId: string; items: PendingReview[]; loading: boolean; onApprove(id): void; onReject(id): void; onClose(): void }`
  - `FundDataDrawer`：`{ fund: Fund; returns: MonthlyReturnRow[]; loading: boolean; rbaHistory: RbaHistoryRow[]; rbaHistoryLoading: boolean; showRbaHistory: boolean; onToggleRbaHistory(): void; onClose(): void }`
  - `AddFormState` 与 `RbaHistoryRow` 两个类型从 `FundManagement.tsx` 内联结构提取，导出在各自子组件文件里

**这个 Task 的硬规则：纯搬移。** 不改任何 className、不改任何中文文案、不改 API 调用、不改校验逻辑、不改状态语义。状态全部留在 `FundManagement.tsx`，子组件只收 props。目的是让 Task 13 的批量改动落在 100–200 行的文件里而不是 742 行里。

- [ ] **Step 1: 记录拆分前基线**

Run: `cd webapp/frontend && npx vitest run && npm run build && wc -l src/pages/FundManagement.tsx`
记下行数（742）与构建产物是否成功。

- [ ] **Step 2: 起 dev server，把拆分前的 `/funds` 页截图存档**

存到 `docs/logs/funds-before-split.png`，作为搬移前后对照基线。

- [ ] **Step 3: 抽 `FundTable.tsx`**

把 `FundManagement.tsx` 里基金列表 `<table>`（约 287–410 行，含表头 8 列、`(已隐藏)` 标记、名称核对 `title`、`完整` / `—` 状态、实时状态 `提取中` / `搜索中`、待审计数、行操作按钮 `更新数据` / `重算` / `隐藏` / 删除确认）整段搬进新文件，把它引用的状态与回调改成 props（签名见 Interfaces）。JSX 内容逐字符照搬。

- [ ] **Step 4: 抽 `AddFundPanel.tsx`**

搬"添加基金 (LLM 摄取)"面板（约 420–560 行）：基金名、搜索引擎选择、高级选项折叠（`▼`/`▶`、slug、APIR、归档页 URL、发行商、域名、ASX 代码）、错误提示、提交按钮。表单状态留在父组件，通过 `form` / `setForm` 传。

- [ ] **Step 5: 抽 `IngestJobPanel.tsx`**

搬摄取 job 进度展示（含 `pending:` / `gap:` / `download_fail:` 计数那一行、job 阶段文案、`起任务中…`）。

- [ ] **Step 6: 抽 `PendingReviewPanel.tsx`**

搬待审核队列（约 565–620 行）：`加载中…` / `无待审记录`、来源标签（`L3 fundmonitors 表` / `LLM PDF 提取` / `权威源`）、`该月已由权威源 (...) 覆盖, pending 未采纳。`、通过/驳回按钮。

- [ ] **Step 7: 抽 `FundDataDrawer.tsx`**

搬月度数据表（`年月` / `月度净收益`、`暂无数据`）与 RBA 利率历史（`RBA 现金利率历史`、`期间` / `目标利率`）。

- [ ] **Step 8: `FundManagement.tsx` 收敛为编排**

保留全部 19 个 `useState`、所有 `useEffect`（含 job 轮询）、所有事件处理函数与 API 调用，`return` 里只渲染 5 个子组件并传 props。页面标题 `基金管理`、`+ 添加基金` 按钮留在本文件。

- [ ] **Step 9: 5 个新文件加进白名单**

`PENDING_FILES` 追加：

```ts
  'pages/funds/AddFundPanel.tsx',
  'pages/funds/FundDataDrawer.tsx',
  'pages/funds/FundTable.tsx',
  'pages/funds/IngestJobPanel.tsx',
  'pages/funds/PendingReviewPanel.tsx',
```

- [ ] **Step 10: 测试 + 构建 + 视觉对照**

Run: `cd webapp/frontend && npx vitest run && npm run build && wc -l src/pages/FundManagement.tsx src/pages/funds/*.tsx`
Expected: 全部 PASS；`FundManagement.tsx` 降到 200 行以内，每个子组件 100–200 行。

浏览器打开 `/funds`，与 Step 2 的截图逐块对照：
- 基金列表列数、状态标记、按钮位置与拆分前一致
- 点"+ 添加基金"面板照常展开，高级选项折叠正常
- 打开某支基金的待审队列与月度数据抽屉，内容与拆分前一致
- 起一次"更新数据"，job 进度面板照常轮询刷新（这是唯一有异步时序的部分，必须实测）
- 控制台零报错

- [ ] **Step 11: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/pages/FundManagement.tsx webapp/frontend/src/pages/funds webapp/frontend/src/i18n/no-hardcoded-cn.test.ts
git commit -m "refactor(frontend): FundManagement 742 行拆 5 个子组件(纯搬移, 不改样式与文案)"
```

---

## Task 13: FundManagement token 化 + 文案入字典

**Files:**
- Modify: `webapp/frontend/src/pages/FundManagement.tsx` 与 `src/pages/funds/` 下 5 个文件
- Modify: `webapp/frontend/src/i18n/cn.ts`、`en.ts`
- Modify: `webapp/frontend/src/i18n/no-hardcoded-cn.test.ts`（白名单删这 6 个文件）

**Interfaces:**
- Consumes: `useT()`、Task 12 的 5 个子组件 props 契约
- Produces: 无新导出

- [ ] **Step 1: 字典加 key**

`cn.ts` 追加：

```ts
  'funds.title': '基金管理',
  'funds.addButton': '+ 添加基金',
  'funds.colId': '基金 ID',
  'funds.colName': '基金名称',
  'funds.colSourceName': '数据源基金名',
  'funds.colDataThrough': '数据截止',
  'funds.colDataStatus': '数据状态',
  'funds.colLiveStatus': '实时状态',
  'funds.colPending': '待审',
  'funds.colActions': '操作',
  'funds.hiddenTag': '(已隐藏)',
  'funds.nameCheckTip': '输入名: {input}\n抓到名: {discovered}\n请核对是否为同一基金',
  'funds.statusComplete': '完整',
  'funds.statusExtracting': '提取中',
  'funds.statusSearching': '搜索中',
  'funds.startingJob': '起任务中…',
  'funds.updateData': '更新数据',
  'funds.gapBlocksRecompute': '该基金有数据缺口，重算将失败',
  'funds.computing': '计算中...',
  'funds.recompute': '重算',
  'funds.hideHint': '隐藏后不出现在对比看板',
  'funds.unhide': '取消隐藏',
  'funds.hide': '隐藏',
  'funds.addPanelTitle': '添加基金 (LLM 摄取)',
  'funds.onlyRequired': '(唯一必填)',
  'funds.namePlaceholder': '如 Bentham Global Income Fund',
  'funds.searchEngine': '搜索引擎',
  'funds.slugHint': '(选填 -- 留空由基金名自动生成 slug)',
  'funds.slugPlaceholder': '如 bentham_global_income_fund',
  'funds.apir': 'APIR 代码',
  'funds.apirPlaceholder': '如 ETL5010AU',
  'funds.apirInvalid': 'APIR 格式应为 3大写字母+4数字+AU（如 ETL5010AU）',
  'funds.nameRequired': '基金名 必填 (其余均选填)',
  'funds.archiveUrl': '归档页 URL (跳过搜索)',
  'funds.issuer': '发行商 (加速搜索)',
  'funds.issuerPlaceholder': '如 Bentham Asset Management',
  'funds.issuerDomain': '发行商官网域名',
  'funds.issuerDomainPlaceholder': '如 benthamam.com',
  'funds.asxCode': 'ASX 代码',
  'funds.asxPlaceholder': '如 MXT',
  'funds.startIngest': '开始 LLM 摄取',
  'funds.noPending': '无待审记录',
  'funds.sourceL3': 'L3 fundmonitors 表',
  'funds.sourceLlmPdf': 'LLM PDF 提取',
  'funds.sourceAuthoritative': '权威源',
  'funds.coveredByAuthoritative': '该月已由权威源 ({source}) 覆盖, pending 未采纳。',
  'funds.colYearMonth': '年月',
  'funds.colNetReturn': '月度净收益',
  'funds.rbaHistoryTitle': 'RBA 现金利率历史',
  'funds.colRbaPeriod': '期间',
  'funds.colRbaTarget': '目标利率',
  'funds.jobPending': '  ·  pending: ',
  'funds.jobGap': '  ·  gap: ',
  'funds.jobDownloadFail': '  ·  download_fail: ',
  'funds.monthSingle': '{year}年{month}月',
  'funds.monthRangeSameYear': '{year}年{from}-{to}月',
  'funds.monthRangeCrossYear': '{fromYear}年{fromMonth}月 ~ {toYear}年{toMonth}月',
```

`en.ts` 追加：

```ts
  'funds.title': 'Fund management',
  'funds.addButton': '+ Add fund',
  'funds.colId': 'Fund ID',
  'funds.colName': 'Fund name',
  'funds.colSourceName': 'Name on source',
  'funds.colDataThrough': 'Data through',
  'funds.colDataStatus': 'Data status',
  'funds.colLiveStatus': 'Live status',
  'funds.colPending': 'Pending',
  'funds.colActions': 'Actions',
  'funds.hiddenTag': '(hidden)',
  'funds.nameCheckTip': 'Entered: {input}\nScraped: {discovered}\nConfirm these are the same fund',
  'funds.statusComplete': 'Complete',
  'funds.statusExtracting': 'Extracting',
  'funds.statusSearching': 'Searching',
  'funds.startingJob': 'Starting…',
  'funds.updateData': 'Update data',
  'funds.gapBlocksRecompute': 'This fund has data gaps; recompute will fail',
  'funds.computing': 'Computing…',
  'funds.recompute': 'Recompute',
  'funds.hideHint': 'Hidden funds do not appear on the dashboard',
  'funds.unhide': 'Unhide',
  'funds.hide': 'Hide',
  'funds.addPanelTitle': 'Add fund (LLM ingest)',
  'funds.onlyRequired': '(only required field)',
  'funds.namePlaceholder': 'e.g. Bentham Global Income Fund',
  'funds.searchEngine': 'Search engine',
  'funds.slugHint': '(optional — leave blank to derive the slug from the fund name)',
  'funds.slugPlaceholder': 'e.g. bentham_global_income_fund',
  'funds.apir': 'APIR code',
  'funds.apirPlaceholder': 'e.g. ETL5010AU',
  'funds.apirInvalid': 'APIR must be 3 uppercase letters + 4 digits + AU (e.g. ETL5010AU)',
  'funds.nameRequired': 'Fund name is required (all other fields optional)',
  'funds.archiveUrl': 'Archive page URL (skips search)',
  'funds.issuer': 'Issuer (speeds up search)',
  'funds.issuerPlaceholder': 'e.g. Bentham Asset Management',
  'funds.issuerDomain': 'Issuer website domain',
  'funds.issuerDomainPlaceholder': 'e.g. benthamam.com',
  'funds.asxCode': 'ASX code',
  'funds.asxPlaceholder': 'e.g. MXT',
  'funds.startIngest': 'Start LLM ingest',
  'funds.noPending': 'No pending records',
  'funds.sourceL3': 'L3 fundmonitors table',
  'funds.sourceLlmPdf': 'LLM PDF extraction',
  'funds.sourceAuthoritative': 'Authoritative source',
  'funds.coveredByAuthoritative': 'This month is already covered by an authoritative source ({source}); the pending value was not adopted.',
  'funds.colYearMonth': 'Month',
  'funds.colNetReturn': 'Monthly net return',
  'funds.rbaHistoryTitle': 'RBA cash rate history',
  'funds.colRbaPeriod': 'Period',
  'funds.colRbaTarget': 'Target rate',
  'funds.jobPending': '  ·  pending: ',
  'funds.jobGap': '  ·  gap: ',
  'funds.jobDownloadFail': '  ·  download_fail: ',
  'funds.monthSingle': '{month}/{year}',
  'funds.monthRangeSameYear': '{from}–{to}/{year}',
  'funds.monthRangeCrossYear': '{fromMonth}/{fromYear} – {toMonth}/{toYear}',
```

- [ ] **Step 2: 逐文件替换文案**

按 Step 1 的 key 表，把 6 个文件里的中文字面量全部换成 `t(...)`。月份区间那三条（原 `` `${sy}年${Number(sm)}月` `` 等）用 `funds.monthSingle` / `funds.monthRangeSameYear` / `funds.monthRangeCrossYear` 带参替换。`—` / `▼` / `▶` 保留符号不入字典。

- [ ] **Step 3: 后端消息包中立标签**

摄取 job 的错误行与 `addError` 中来自后端的 `detail`（原样中文）改为：

```tsx
<span className="text-fg-muted">{t('common.backendMessage')}</span>
<span>{backendDetail}</span>
```

前端自校验产生的提示（`funds.nameRequired` / `funds.apirInvalid`）不包标签——它们是前端文案，已翻译。

- [ ] **Step 4: token 化**

3 处 `bg-[#1a1a2e] ... hover:bg-[#2a2a4e]` → `bg-accent text-accent-fg hover:opacity-90`；表格套 `.card` + `.th` + `.num`；输入框统一 `border border-border-strong rounded-md bg-surface text-fg px-2 py-1.5 placeholder:text-fg-subtle`；面板容器 `.card p-4`；删除确认按钮 `bg-neg text-white`（`--neg` 上白字两主题都够对比）；其余按全局映射表。

- [ ] **Step 5: 白名单删 6 个文件 + 测试构建**

`PENDING_FILES` 删掉 `'pages/FundManagement.tsx',` 与 5 个 `pages/funds/*.tsx`。

Run: `cd webapp/frontend && npx vitest run && npm run build`
Expected: 全部 PASS

- [ ] **Step 6: 浏览器实测**

`/funds` 页，深浅 × CN/EN 四态：
- 表格、面板、抽屉全部无残留中文（后端消息除外，且带 `Backend message:` 前缀）
- 输入框与 placeholder 在深色下可读
- 起一次真实"更新数据"，观察 job 面板：阶段文案走字典，后端日志原文带中立标签
- 删除确认弹窗在深色下红色按钮对比够
- 控制台零报错

- [ ] **Step 7: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/pages webapp/frontend/src/i18n
git commit -m "feat(frontend): 基金管理页 token 化 + 文案入字典, 后端消息包中立标签"
```

---

## Task 14: 闸门收口 + 全量验收

**Files:**
- Modify: `webapp/frontend/src/i18n/no-hardcoded-cn.test.ts`（加空白名单断言 + 硬编码色扫描）
- Create: `docs/logs/frontend-theme-i18n-verify/`（12 组合截图）

**Interfaces:**
- Consumes: Task 1–13 全部产出
- Produces: `PENDING_FILES` 恒为空的闸门；硬编码颜色扫描闸

- [ ] **Step 1: 加"白名单必须为空"与"禁硬编码色"断言**

在 `src/i18n/no-hardcoded-cn.test.ts` 末尾追加：

```ts
describe('闸门收口', () => {
  it('待迁移白名单已清空（此后任何新页面写死中文都会报红）', () => {
    expect(PENDING_FILES).toEqual([])
  })

  const files = SCAN_DIRS.flatMap(d => walk(join(SRC, d)))

  it.each(files.map(f => relative(SRC, f)))('%s 不含硬编码颜色与 dark: 类名', rel => {
    const code = stripComments(readFileSync(join(SRC, rel), 'utf8'))
    const offenders = code.split('\n')
      .map((text, i) => ({ line: i + 1, text }))
      .filter(x =>
        /#[0-9a-fA-F]{3,8}\b/.test(x.text) ||
        /\bdark:/.test(x.text) ||
        /\b(?:bg|text|border)-(?:gray|slate|zinc|neutral|stone|cyan|red|amber|green|blue)-\d{2,3}\b/.test(x.text),
      )
      .map(x => `${rel}:${x.line}: ${x.text.trim()}`)
    expect(offenders).toEqual([])
  })
})
```

- [ ] **Step 2: 跑闸门，修掉漏网的**

Run: `cd webapp/frontend && npx vitest run src/i18n/no-hardcoded-cn.test.ts`
Expected: PASS。若某文件报硬编码色，回到对应 Task 的映射表把它换成 token；若报 `dark:`，说明写了双份类名，删掉并改用 token。

- [ ] **Step 3: 全量测试 + 构建**

Run: `cd webapp/frontend && npx vitest run && npm run build`
Expected: 全部 PASS，其中 `src/lib/rebase.test.ts` 全绿（本次改造未触碰计算逻辑的证据）

- [ ] **Step 4: 12 组合浏览器验收**

起 dev server 与后端，逐一走 2 主题 × 2 语言 × 3 页：

| 检查项 | 判据 |
|---|---|
| 控制台 | 零 error、零 React 依赖/key 告警 |
| 切主题图表 | NAV 图、滚动超额图、热力图立即重绘，不需刷新 |
| 切语言图表 | 图内 `起点`/`Base`、`无数据`/`No data` 立即跟随 |
| 热力图缺口格 | 深色下暗于有数据格，不抢眼 |
| 锚定曲线 | 深浅两主题下都清晰可见 |
| 图表 tooltip | 深色下深底浅字，可读 |
| 表格 | 数字右对齐等宽、表头吸顶、正负着色正确 |
| 表单与输入 | 深色下文字与 placeholder 可读 |
| 后端消息 | EN 下带 `Backend message:` 前缀，不被误认为漏译 |
| 刷新持久化 | 主题与语言选择刷新后保持 |
| 首屏 | 深色偏好下无白闪 |

每个组合截一张图，存 `docs/logs/frontend-theme-i18n-verify/<theme>-<lang>-<page>.png`。

- [ ] **Step 5: 提交**

```bash
cd /Users/chong/Desktop/fixed_fund_analysis
git add webapp/frontend/src/i18n/no-hardcoded-cn.test.ts docs/logs/frontend-theme-i18n-verify
git commit -m "test(frontend): 闸门收口 — 白名单清空 + 禁硬编码色/dark: 类名, 12 组合验收截图"
```

---

## Self-Review 记录

**Spec 覆盖对照：**

| Spec 章节 | 落到哪个 Task |
|---|---|
| §3 token 体系（含 heat RGB 三元组） | Task 1 |
| §3.4 排版形状（等宽数字、边框化、表头） | Task 1（`.card`/`.num`/`.th`）+ Task 6、9、10、13 套用 |
| §4.1 三态状态与持久化 | Task 2 |
| §4.2 class 切换 | Task 2 |
| §4.3 防白闪 | Task 1 Step 6 |
| §4.4 ECharts 接入 + 防漂移闸 | Task 5（闸）+ Task 7、8（接入） |
| §5.1 字典与类型闸 | Task 3 |
| §5.2 取文与 `<html lang>` | Task 3 |
| §5.3 不翻译项 | Global Constraints + Task 13 Step 3 |
| §5.4 中文字面量闸 | Task 3（建，带白名单）+ Task 4–13（逐步收缩）+ Task 14（收口） |
| §6 组件清单 19 项 | Task 1（3 个配置）、4、6、7、8、9、10、11、13 全覆盖 |
| §6.1 FundManagement 拆分 | Task 12（纯搬移单独提交） |
| §7.1 四个新单测 | Task 1、2、3、5（`palette.test.ts` 分两次建） |
| §7.2 `rebase.test.ts` 保绿 | Task 7 Step 5、Task 14 Step 3 显式检查 |
| §7.3 `tsc -b` | 每个 Task 的构建步 |
| §7.4 12 组合实测 + 截图 | Task 14 Step 4 |
| §9 风险对策 | 漂移闸 Task 5（含故意制造漂移的验证步）、中文闸 Task 14、图表重绘 Task 7 Step 6、拆分回归 Task 12 Step 10、缺口格 Task 8 Step 5、后端消息 Task 13 Step 3 |

**对 spec 的两处补充（已在 Task 1 标注）：** 增加 `--pos-soft` / `--neg-soft` / `--neg-border` / `--warn-soft` / `--warn-border` / `--accent-fg` / `--card-shadow` 七个 token，因为 Tailwind 的 `/opacity` 修饰符对 `var()` 颜色无效，浅底与边框必须各自成 token。

**命名一致性核对：** `ThemeMode` / `ResolvedTheme` / `resolveTheme` / `applyTheme` / `watchSystemTheme` / `readStoredThemeMode` / `writeStoredThemeMode`（Task 2 定义，Task 2 store 使用）；`Lang` / `I18nKey` / `translate` / `readStoredLang` / `writeStoredLang` / `applyLang` / `useT`（Task 3 定义，Task 4–13 使用）；`ChartPalette` / `CHART_TOKEN_MAP` / `chartPalettes` / `useChartTheme`（Task 5 定义，Task 7、8 使用）；`parseCssVars`（Task 1 定义，Task 5 复用）；`PENDING_FILES`（Task 3 定义，Task 4–14 增删）。字典 key 在 Task 3、6、7、8、9、10、11、13 分批追加，无重复定义（`dashboard.periodFull` 在 Task 9 首次出现，Task 11 明确说明不重复加）。
