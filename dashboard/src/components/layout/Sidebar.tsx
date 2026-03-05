import { NavLink } from 'react-router-dom';
import { clsx } from 'clsx';

const NAV_ITEMS = [
  { to: '/projects', label: 'Projects', icon: '📦' },
  { to: '/sessions', label: 'Sessions', icon: '⚡' },
  { to: '/approvals', label: 'Approvals', icon: '✅' },
];

export function Sidebar() {
  return (
    <aside className="fixed inset-y-0 left-0 z-30 flex w-60 flex-col border-r border-zinc-200 bg-white dark:border-zinc-700 dark:bg-zinc-900">
      <div className="flex h-14 items-center gap-2 border-b border-zinc-200 px-4 dark:border-zinc-700">
        <span className="text-lg font-bold text-zinc-900 dark:text-zinc-100">
          MetaForge
        </span>
        <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
          v0.1
        </span>
      </div>

      <nav className="flex-1 space-y-1 p-3">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              clsx(
                'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                isActive
                  ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-400'
                  : 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800'
              )
            }
          >
            <span>{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-zinc-200 p-3 dark:border-zinc-700">
        <p className="text-xs text-zinc-400">MetaForge Platform v0.1.0</p>
      </div>
    </aside>
  );
}
