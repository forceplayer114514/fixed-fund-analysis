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
