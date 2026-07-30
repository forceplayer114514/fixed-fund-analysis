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
