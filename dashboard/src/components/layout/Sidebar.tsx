import { NavLink } from 'react-router-dom';

// ---------------------------------------------------------------------------
// Nav items — Material Symbols Outlined icon names
// ---------------------------------------------------------------------------

const NAV_ITEMS = [
  { to: '/projects',  icon: 'grid_view',    title: 'Platform'         },
  { to: '/sessions',  icon: 'account_tree', title: 'Orchestrator'     },
  { to: '/approvals', icon: 'task_alt',     title: 'Approvals'        },
  { to: '/bom',       icon: 'inventory_2',  title: 'BOM'              },
  { to: '/twin',      icon: 'hub',          title: 'Digital Twin'     },
  { to: '/files',     icon: 'psychology',   title: 'Knowledge'        },
  { to: '/assistant', icon: 'auto_awesome', title: 'Design Assistant' },
] as const;

// ---------------------------------------------------------------------------
// NavRail — 48px icon-only, always visible
// ---------------------------------------------------------------------------

export function Sidebar() {
  return (
    <nav
      className="fixed inset-y-0 left-0 z-40 flex flex-col items-center"
      style={{
        width: 48,
        background: '#191b22',
        borderRight: '1px solid rgba(65,72,90,0.2)',
      }}
    >
      {/* Logo mark */}
      <div
        className="mt-3 mb-3 flex shrink-0 items-center justify-center rounded"
        style={{
          width: 32,
          height: 32,
          background: '#e67e22',
          fontFamily: 'Inter, sans-serif',
          fontWeight: 700,
          fontSize: 15,
          color: '#111319',
          letterSpacing: '-0.02em',
          userSelect: 'none',
        }}
      >
        M
      </div>

      {/* Nav items */}
      <div className="flex flex-1 flex-col items-center w-full">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            title={item.title}
            className={({ isActive }) =>
              [
                'nav-btn flex w-full items-center justify-center transition-colors',
                isActive
                  ? 'nav-active text-primary-container'
                  : 'text-on-surface-variant hover:bg-surface-high hover:text-on-surface',
              ].join(' ')
            }
            style={{ height: 44 }}
          >
            <span className="material-symbols-outlined">{item.icon}</span>
          </NavLink>
        ))}
      </div>

      {/* Bottom: settings + avatar */}
      <div className="flex flex-col items-center gap-1 pb-3">
        <button
          type="button"
          title="Settings"
          className="flex items-center justify-center rounded text-on-surface-variant hover:bg-surface-high transition-colors"
          style={{ width: 32, height: 32 }}
        >
          <span className="material-symbols-outlined">settings</span>
        </button>
        <div
          className="flex shrink-0 items-center justify-center rounded-full"
          style={{
            width: 28,
            height: 28,
            background: '#282a30',
            fontSize: 10,
            fontWeight: 600,
            color: '#9a9aaa',
            letterSpacing: '0.03em',
            cursor: 'pointer',
          }}
          title="Profile"
        >
          MF
        </div>
      </div>
    </nav>
  );
}
