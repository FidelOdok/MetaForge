import { useState, type FocusEvent, type MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import { NavLink } from 'react-router-dom';
import { ArrowUpRight, Settings, X } from 'lucide-react';
import { useLayoutStore } from '../../store/layout-store';
import { MOBILE_NAV_MAX_WIDTH, NAV_GROUPS } from './nav';

interface SidebarProps {
  /** Mobile drawer open state. */
  open?: boolean;
  onClose?: () => void;
}

interface TooltipState {
  label: string;
  top: number;
}

const DOCS_URL = 'https://fidelodok.github.io/MetaForge/';
const SETTINGS_LABEL = 'Settings & connection';

/**
 * Floating workspace navigation. On desktop it collapses to an icon rail
 * (labels become hover/focus tooltips); on mobile it is an off-canvas drawer.
 */
export function Sidebar({ open = false, onClose }: SidebarProps) {
  const collapsed = useLayoutStore((s) => s.sidebarCollapsed);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

  const showTooltip = (el: HTMLElement, label: string) => {
    if (!collapsed || window.innerWidth <= MOBILE_NAV_MAX_WIDTH) return;
    const rect = el.getBoundingClientRect();
    setTooltip({ label, top: rect.top + rect.height / 2 });
  };
  const hideTooltip = () => setTooltip(null);

  const tooltipHandlers = (label: string) => ({
    onMouseEnter: (e: MouseEvent<HTMLElement>) => showTooltip(e.currentTarget, label),
    onMouseLeave: hideTooltip,
    onFocus: (e: FocusEvent<HTMLElement>) => showTooltip(e.currentTarget, label),
    onBlur: hideTooltip,
  });

  const close = () => onClose?.();

  return (
    <>
      {tooltip &&
        collapsed &&
        createPortal(
          <span className="rail-tooltip" role="tooltip" style={{ top: tooltip.top }}>
            {tooltip.label}
          </span>,
          document.body,
        )}
      {open && (
        <button type="button" className="nav-backdrop" aria-label="Close navigation" onClick={close} />
      )}
      <aside className={`workspace-sidebar ${open ? 'is-open' : ''}`}>
        <div className="brand-row">
          <NavLink to="/projects" className="collapsed-brand" aria-label="MetaForge projects">
            <img className="brand-logo-dark" src="/metaforge-symbol-dark.svg" alt="" width="28" height="32" />
            <img className="brand-logo-light" src="/metaforge-symbol-light.svg" alt="" width="28" height="32" />
          </NavLink>
          <NavLink to="/projects" className="brand" onClick={close}>
            <img className="brand-logo brand-logo-dark" src="/metaforge-logo.svg" alt="MetaForge" width="200" height="64" />
            <img className="brand-logo brand-logo-light" src="/metaforge-logo-light.svg" alt="MetaForge" width="200" height="64" />
          </NavLink>
          <button type="button" className="mobile-close icon-control" aria-label="Close navigation" onClick={close}>
            <X size={20} />
          </button>
        </div>

        <nav aria-label="Main navigation" className="workspace-nav">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="nav-group">
              <p>{group.label}</p>
              {group.items.map(({ to, label, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  aria-label={label}
                  {...tooltipHandlers(label)}
                  onClick={close}
                  className={({ isActive }) => `workspace-nav-link ${isActive ? 'selected' : ''}`}
                >
                  <Icon size={18} aria-hidden="true" />
                  <span>{label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <NavLink
            to="/settings"
            aria-label={SETTINGS_LABEL}
            {...tooltipHandlers(SETTINGS_LABEL)}
            onClick={close}
            className={({ isActive }) => `workspace-nav-link ${isActive ? 'selected' : ''}`}
          >
            <Settings size={18} aria-hidden="true" />
            <span>{SETTINGS_LABEL}</span>
          </NavLink>
          <a className="docs-link" href={DOCS_URL} target="_blank" rel="noreferrer">
            Documentation <ArrowUpRight size={15} aria-hidden="true" />
          </a>
        </div>
      </aside>
    </>
  );
}
