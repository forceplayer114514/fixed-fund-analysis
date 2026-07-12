import { NavLink } from 'react-router-dom'

const links = [
  { to: '/', label: '对比看板' },
  { to: '/anomalies', label: '异常审计' },
  { to: '/funds', label: '基金管理' },
]

export default function Sidebar() {
  return (
    <aside className="w-56 bg-[#1a1a2e] text-white flex flex-col shrink-0">
      <h2 className="px-6 py-6 text-base font-semibold border-b border-gray-700">
        固定收益基金分析
      </h2>
      <nav className="flex-1 pt-3">
        {links.map(link => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.to === '/'}
            className={({ isActive }) =>
              `block px-6 py-3 text-sm transition-colors ${
                isActive
                  ? 'text-white bg-white/10 border-r-2 border-cyan-400 font-medium'
                  : 'text-gray-400 hover:text-white hover:bg-white/5'
              }`
            }
          >
            {link.label}
          </NavLink>
        ))}
      </nav>
      <div className="px-6 py-4 text-xs text-gray-600 border-t border-gray-700">v0.1</div>
    </aside>
  )
}
