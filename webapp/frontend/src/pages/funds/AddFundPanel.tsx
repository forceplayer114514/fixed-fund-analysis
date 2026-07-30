import type { Dispatch, SetStateAction } from 'react'
import { useT } from '../../i18n/useT'

export interface AddFormState {
  fund_id: string
  fund_name: string
  apir_code: string
  confirmed_url: string
  issuer: string
  issuer_domain: string
  asx_code: string
  // Spec G: 搜索引擎选择, 每次摄取生效, 不记在基金上
  search_engine: 'tavily' | 'grok'
}

/** message origin 标记：backend=true 时来自后端 detail（原样展示, 不翻译, 只包中立标签）。 */
export interface AddFormError {
  message: string
  backend: boolean
}

interface AddFundPanelProps {
  open: boolean
  form: AddFormState
  setForm: Dispatch<SetStateAction<AddFormState>>
  showAdvanced: boolean
  setShowAdvanced: Dispatch<SetStateAction<boolean>>
  error: AddFormError | null
  submitting: boolean
  onSubmit: () => void
}

export default function AddFundPanel({
  open,
  form,
  setForm,
  showAdvanced,
  setShowAdvanced,
  error,
  submitting,
  onSubmit,
}: AddFundPanelProps) {
  const t = useT()
  if (!open) return null

  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs text-fg-muted block mb-1">
          {t('funds.nameLabel')} <span className="text-neg">*</span>
          <span className="text-fg-subtle ml-1">{t('funds.onlyRequired')}</span>
        </label>
        <input
          className="w-full text-sm border border-border-strong rounded bg-surface text-fg px-3 py-2 placeholder:text-fg-subtle"
          value={form.fund_name}
          onChange={e => setForm({ ...form, fund_name: e.target.value })}
          placeholder={t('funds.namePlaceholder')}
        />
        <div className="text-xs text-fg-subtle mt-1">
          {t('funds.autoIngestHint')}
        </div>
      </div>

      <div className="mb-3">
        <label className="block text-sm font-medium mb-1 text-fg">{t('funds.searchEngine')}</label>
        <div className="flex gap-4">
          <label className="flex items-center gap-1 text-sm text-fg">
            <input
              type="radio"
              name="search_engine"
              value="tavily"
              checked={form.search_engine === 'tavily'}
              onChange={() => setForm({ ...form, search_engine: 'tavily' })}
            />
            {t('funds.engineTavily')}
          </label>
          <label className="flex items-center gap-1 text-sm text-fg">
            <input
              type="radio"
              name="search_engine"
              value="grok"
              checked={form.search_engine === 'grok'}
              onChange={() => setForm({ ...form, search_engine: 'grok' })}
            />
            {t('funds.engineGrok')}
          </label>
        </div>
      </div>

      <button
        type="button"
        className="text-xs text-fg-muted hover:text-fg underline"
        onClick={() => setShowAdvanced(v => !v)}
      >
        {showAdvanced ? '▼' : '▶'} {t('funds.advancedOptions')}
      </button>

      {showAdvanced && (
        <div className="space-y-3 border-l-2 border-border pl-3">
          <div>
            <label className="text-xs text-fg-muted block mb-1">
              fund_id <span className="text-fg-subtle">{t('funds.slugHint')}</span>
            </label>
            <input
              className="w-full text-sm border border-border-strong rounded bg-surface text-fg px-3 py-2 placeholder:text-fg-subtle"
              value={form.fund_id}
              onChange={e => setForm({ ...form, fund_id: e.target.value })}
              placeholder={t('funds.slugPlaceholder')}
            />
          </div>
          <div>
            <label className="text-xs text-fg-muted block mb-1">{t('funds.apir')}</label>
            <input
              className="w-full text-sm border border-border-strong rounded bg-surface text-fg px-3 py-2 placeholder:text-fg-subtle"
              value={form.apir_code}
              onChange={e => setForm({ ...form, apir_code: e.target.value })}
              placeholder={t('funds.apirPlaceholder')}
            />
          </div>
          <div>
            <label className="text-xs text-fg-muted block mb-1">{t('funds.archiveUrl')}</label>
            <input
              className="w-full text-sm border border-border-strong rounded bg-surface text-fg px-3 py-2 placeholder:text-fg-subtle"
              value={form.confirmed_url}
              onChange={e => setForm({ ...form, confirmed_url: e.target.value })}
              placeholder="https://.../monthly-reports"
            />
          </div>
          <div>
            <label className="text-xs text-fg-muted block mb-1">{t('funds.issuer')}</label>
            <input
              className="w-full text-sm border border-border-strong rounded bg-surface text-fg px-3 py-2 placeholder:text-fg-subtle"
              value={form.issuer}
              onChange={e => setForm({ ...form, issuer: e.target.value })}
              placeholder={t('funds.issuerPlaceholder')}
            />
          </div>
          <div>
            <label className="text-xs text-fg-muted block mb-1">{t('funds.issuerDomain')}</label>
            <input
              className="w-full text-sm border border-border-strong rounded bg-surface text-fg px-3 py-2 placeholder:text-fg-subtle"
              value={form.issuer_domain}
              onChange={e => setForm({ ...form, issuer_domain: e.target.value })}
              placeholder={t('funds.issuerDomainPlaceholder')}
            />
          </div>
          <div>
            <label className="text-xs text-fg-muted block mb-1">{t('funds.asxCode')}</label>
            <input
              className="w-full text-sm border border-border-strong rounded bg-surface text-fg px-3 py-2 placeholder:text-fg-subtle"
              value={form.asx_code}
              onChange={e => setForm({ ...form, asx_code: e.target.value })}
              placeholder={t('funds.asxPlaceholder')}
            />
          </div>
        </div>
      )}

      {error && (
        <div className="text-xs text-neg">
          {error.backend ? (
            <>
              <span className="text-fg-muted">{t('common.backendMessage')}</span>
              <span>{error.message}</span>
            </>
          ) : (
            error.message
          )}
        </div>
      )}
      <button
        className="w-full text-sm bg-accent text-accent-fg py-2 rounded-lg hover:opacity-90 disabled:opacity-50"
        onClick={onSubmit}
        disabled={submitting}
      >
        {submitting ? t('funds.startingJob') : t('funds.startIngest')}
      </button>
    </div>
  )
}
