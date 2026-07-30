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
