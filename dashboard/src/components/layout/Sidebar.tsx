import { NavLink } from 'react-router-dom';
import { clsx } from 'clsx';
import { ChevronLeft, ChevronRight, Menu, X } from 'lucide-react';
import { useLayoutStore } from '@/store/layout-store';

// ---------------------------------------------------------------------------
// Nav items
// ---------------------------------------------------------------------------

const NAV_ITEMS = [
  { to: '/projects',  label: 'Projects',        icon: '\uD83D\uDCE6', shortcut: 'G P' },
  { to: '/sessions',  label: 'Sessions',         icon: '\u26A1',       shortcut: 'G S' },
  { to: '/approvals', label: 'Approvals',        icon: '\u2705',       shortcut: 'G A' },
  { to: '/bom',       label: 'BOM',              icon: '\uD83D\uDCCB', shortcut: 'G B' },
  { to: '/twin',      label: 'Digital Twin',     icon: '\uD83E\uDDE0', shortcut: 'G T' },
  { to: '/files',     label: 'Files',            icon: '\uD83D\uDD17', shortcut: '' },
  { to: '/assistant', label: 'Design Assistant', icon: '\uD83E\uDD16', shortcut: 'G D' },
] as const;

// ---------------------------------------------------------------------------
// Sidebar content (shared between desktop and mobile overlay)
// ---------------------------------------------------------------------------

interface SidebarContentProps {
  collapsed: boolean;
  /** When provided (mobile mode), a close button is shown and clicking nav items also fires this. */
  onClose?: () => void;
}

function SidebarContent({ collapsed, onClose }: SidebarContentProps) {
  const { toggleSidebar } = useLayoutStore();

  return (
    <div className="flex h-full flex-col">
      {/* Logo / header */}
      <div className="flex h-14 items-center justify-between border-b border-zinc-200 px-3 dark:border-zinc-700">
        {collapsed ? (
          <span className="mx-auto text-lg font-bold text-zinc-900 dark:text-zinc-100">
            M
          </span>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold text-zinc-900 dark:text-zinc-100">
              MetaForge
            </span>
            <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
              v0.1
            </span>
          </div>
        )}
        {/* Mobile close button */}
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close sidebar"
            className="flex h-8 w-8 items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Nav items */}
      <nav className="flex-1 space-y-1 overflow-y-auto p-2">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            onClick={onClose}
            className={({ isActive }) =>
              clsx(
                'group relative flex items-center rounded-md px-2 py-2 text-sm font-medium transition-colors',
                collapsed ? 'justify-center' : 'gap-3',
                isActive
                  ? 'bg-zinc-100 text-blue-600 dark:bg-zinc-800 dark:text-blue-400'
                  : 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800'
              )
            }
          >
            {/* Icon */}
            <span className="shrink-0 text-base leading-none">{item.icon}</span>

            {/* Label — hidden when collapsed */}
            {!collapsed && (
              <span className="flex-1 truncate">{item.label}</span>
            )}

            {/* Keyboard shortcut badge on hover (expanded mode) */}
            {!collapsed && (
              <span className="ml-auto hidden rounded bg-zinc-100 px-1 py-0.5 font-mono text-[10px] text-zinc-400 opacity-0 transition-opacity group-hover:opacity-100 dark:bg-zinc-700 dark:text-zinc-500 md:block">
                {item.shortcut}
              </span>
            )}

            {/* Tooltip shown on hover in collapsed mode */}
            {collapsed && (
              <span className="pointer-events-none absolute left-full z-50 ml-2 hidden whitespace-nowrap rounded-md bg-zinc-800 px-2 py-1 text-xs text-zinc-100 shadow-lg group-hover:block dark:bg-zinc-700">
                {item.label}
                <span className="ml-2 font-mono text-zinc-400">{item.shortcut}</span>
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-zinc-200 p-2 dark:border-zinc-700">
        {!collapsed && (
          <p className="mb-2 truncate px-2 text-xs text-zinc-400">
            MetaForge Platform v0.1.0
          </p>
        )}
        {/* Collapse toggle (desktop only — mobile uses onClose) */}
        {!onClose && (
          <button
            type="button"
            onClick={toggleSidebar}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className={clsx(
              'flex h-8 w-full items-center rounded-md text-sm text-zinc-500 transition-colors hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800',
              collapsed ? 'justify-center' : 'gap-2 px-2'
            )}
          >
            {collapsed ? (
              <ChevronRight className="h-4 w-4" />
            ) : (
              <>
                <ChevronLeft className="h-4 w-4" />
                <span className="text-xs">Collapse</span>
              </>
            )}
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sidebar (exported)
// ---------------------------------------------------------------------------

export function Sidebar() {
  const { sidebarCollapsed, mobileSidebarOpen, closeMobileSidebar, openMobileSidebar } =
    useLayoutStore();

  return (
    <>
      {/* ------------------------------------------------------------------ */}
      {/* Desktop sidebar (hidden below md breakpoint)                        */}
      {/* ------------------------------------------------------------------ */}
      <aside
        className={clsx(
          'fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-zinc-200 bg-white transition-all duration-200 md:flex dark:border-zinc-700 dark:bg-zinc-900',
          sidebarCollapsed ? 'w-14' : 'w-56'
        )}
      >
        <SidebarContent collapsed={sidebarCollapsed} />
      </aside>

      {/* ------------------------------------------------------------------ */}
      {/* Mobile hamburger button (visible below md breakpoint)               */}
      {/* ------------------------------------------------------------------ */}
      <button
        type="button"
        onClick={openMobileSidebar}
        aria-label="Open navigation"
        className="fixed left-4 top-3.5 z-40 flex h-8 w-8 items-center justify-center rounded-md text-zinc-600 transition-colors hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800 md:hidden"
      >
        <Menu className="h-5 w-5" />
      </button>

      {/* ------------------------------------------------------------------ */}
      {/* Mobile overlay sidebar                                               */}
      {/* ------------------------------------------------------------------ */}
      {mobileSidebarOpen && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-40 bg-black/40 md:hidden"
            onClick={closeMobileSidebar}
            aria-hidden="true"
          />
          {/* Slide-in panel */}
          <aside className="fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-zinc-200 bg-white shadow-xl md:hidden dark:border-zinc-700 dark:bg-zinc-900">
            <SidebarContent collapsed={false} onClose={closeMobileSidebar} />
          </aside>
        </>
      )}
    </>
  );
}
