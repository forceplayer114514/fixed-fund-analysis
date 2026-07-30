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
