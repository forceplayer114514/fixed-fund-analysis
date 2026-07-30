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
