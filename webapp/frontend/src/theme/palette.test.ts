import { describe, expect, it } from 'vitest'
import css from '../index.css?raw'
import { CHART_TOKEN_MAP, chartPalettes } from './chartTheme'

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

describe('chartTheme 与 index.css 对拍（防漂移）', () => {
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
