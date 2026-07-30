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
