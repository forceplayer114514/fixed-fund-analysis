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
