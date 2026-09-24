import { useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ChevronRight, FlaskConical, Menu, PanelLeft, PlugZap, UserRound } from 'lucide-react';
import { ProjectSwitcher } from '../shared/ProjectSwitcher';
import { useHealth } from '../../hooks/use-health';
import { useLayoutStore } from '../../store/layout-store';
import type { DependencyStatus } from '../../types/health';
import { ThemeToggle } from './ThemeToggle';
import { SAMPLE_WORKSPACE_HREF, SECTION_LABELS, isSampleWorkspace } from './nav';

interface TopbarProps {
  /** Toggle the mobile navigation drawer. */
  onMenu?: () => void;
  navOpen?: boolean;
}

const HEALTH_LABELS: Record<DependencyStatus, string> = {
  healthy: 'Connected',
  degraded: 'Degraded',
  unhealthy: 'Unhealthy',
};

export function Topbar({ onMenu, navOpen = false }: TopbarProps) {
  const collapsed = useLayoutStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useLayoutStore((s) => s.toggleSidebar);
  const { pathname, search } = useLocation();
  const parts = pathname.split('/').filter(Boolean);
  const section = SECTION_LABELS[parts[0] ?? ''] ?? 'Workspace';
  const sample = isSampleWorkspace(search);

  const health = useHealth();
  const gatewayLabel = health.isError
    ? 'Unavailable'
    : health.data
      ? HEALTH_LABELS[health.data.status] ?? health.data.status
      : 'Connecting';
  const gatewayTitle = sample ? 'Sample workspace · offline' : `Gateway: ${gatewayLabel}`;

  useEffect(() => {
    document.title = `${section} — MetaForge`;
  }, [section]);

  return (
    <header className="workspace-topbar">
      <div className="topbar-location">
        <button
          type="button"
          className="desktop-nav-toggle icon-control"
          onClick={toggleSidebar}
          aria-label={collapsed ? 'Expand navigation and explorer' : 'Minimise navigation and explorer'}
          aria-expanded={!collapsed}
        >
          <PanelLeft size={18} />
        </button>
        <button
          type="button"
          className="mobile-menu icon-control"
          onClick={onMenu}
          aria-label="Toggle navigation"
          aria-expanded={navOpen}
        >
          <Menu size={20} />
        </button>
        <nav aria-label="Breadcrumb">
          <ol>
            <li>
              <Link to={`/${parts[0] || 'projects'}`} aria-current={parts.length < 2 ? 'page' : undefined}>
                {section}
              </Link>
            </li>
            {parts.length > 1 && (
              <li>
                <ChevronRight size={14} aria-hidden="true" />
                <span aria-current="page">Details</span>
              </li>
            )}
          </ol>
        </nav>
      </div>

      <div className="topbar-actions">
        <ThemeToggle compact />
        {sample ? (
          <a className="nav-sample-link" href="/twin" title="Exit sample workspace">
            Exit sample
          </a>
        ) : (
          <a className="nav-sample-link" href={SAMPLE_WORKSPACE_HREF} title="Open sample workspace">
            <FlaskConical size={14} />
            <span>Sample</span>
          </a>
        )}
        <Link
          className="icon-control nav-account"
          to="/settings"
          aria-label="Account and API keys"
          title="Account and API keys"
        >
          <UserRound size={17} />
        </Link>
        <Link
          to="/settings"
          aria-label={gatewayTitle}
          title={gatewayTitle}
          className={`gateway-chip ${health.isError ? 'disconnected' : ''}`}
        >
          <PlugZap size={15} aria-hidden="true" />
          <span>{sample ? 'Sample' : gatewayLabel}</span>
        </Link>
        <ProjectSwitcher />
      </div>
    </header>
  );
}
