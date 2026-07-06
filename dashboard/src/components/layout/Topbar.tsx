import { useEffect, useState } from 'react';
import { useLocation, useParams } from 'react-router-dom';
import { RunAgentDialog } from '../shared/RunAgentDialog';
import { useProjects } from '../../hooks/use-projects';
import { useSessions } from '../../hooks/use-sessions';

// ---------------------------------------------------------------------------
// Route → page name mapping
// ---------------------------------------------------------------------------

const SEGMENT_LABELS: Record<string, string> = {
  projects:  'Platform',
  sessions:  'Orchestrator',
  runs:      'Runs',
  approvals: 'Approvals',
  bom:       'BOM',
  twin:      'Digital Twin',
  files:     'Files',
  knowledge: 'Knowledge',
  assistant: 'Design Assistant',
};

// ---------------------------------------------------------------------------
// Breadcrumb helpers
// ---------------------------------------------------------------------------

interface BreadcrumbSegment {
  label: string;
  isCurrent: boolean;
}

function useBreadcrumbs(): { segments: BreadcrumbSegment[]; pageTitle: string } {
  const { pathname } = useLocation();
  const params = useParams();
  // Cached lists (shared with the Projects/Sessions pages) — used to show a
  // human name instead of a raw id in the breadcrumb (MET-512).
  const { data: projects } = useProjects();
  const { data: sessions } = useSessions();

  const parts = pathname.replace(/^\//, '').split('/').filter(Boolean);

  const decode = (s: string): string => {
    try {
      return decodeURIComponent(s);
    } catch {
      return s;
    }
  };

  const resolveName = (parent: string | undefined, id: string): string | undefined => {
    if (parent === 'projects') return projects?.find((p) => p.id === id)?.name;
    if (parent === 'sessions') {
      const s = sessions?.find((x) => x.id === id);
      return s ? s.taskType || s.agentCode : undefined;
    }
    return undefined;
  };

  const segments: BreadcrumbSegment[] = parts.map((part, idx) => {
    const isLast = idx === parts.length - 1;
    const decoded = decode(part);
    const isId = /^[0-9a-f-]{8,}$/i.test(part) || Object.values(params).includes(part);

    let label: string;
    if (isId) {
      // Prefer the resolved entity name; fall back to a short id.
      const name = resolveName(parts[idx - 1], decoded);
      label = name ?? (decoded.length > 8 ? `${decoded.slice(0, 8)}…` : decoded);
    } else {
      label = SEGMENT_LABELS[part] ?? decoded.charAt(0).toUpperCase() + decoded.slice(1);
    }

    return { label, isCurrent: isLast };
  });

  const topLevelSegment = parts[0] ?? '';
  const pageTitle =
    SEGMENT_LABELS[topLevelSegment] ??
    (topLevelSegment.charAt(0).toUpperCase() + topLevelSegment.slice(1) || 'MetaForge');

  return { segments, pageTitle };
}

// ---------------------------------------------------------------------------
// Topbar
// ---------------------------------------------------------------------------

export function Topbar() {
  const [runDialogOpen, setRunDialogOpen] = useState(false);
  const { segments, pageTitle } = useBreadcrumbs();

  useEffect(() => {
    document.title = pageTitle ? `${pageTitle} — MetaForge` : 'MetaForge';
  }, [pageTitle]);

  return (
    <>
      <header
        className="glass flex h-10 shrink-0 items-center justify-between px-5"
        style={{
          background: 'rgba(25,27,34,0.85)',
          borderBottom: '1px solid rgba(65,72,90,0.2)',
        }}
      >
        {/* Breadcrumbs */}
        <nav aria-label="Breadcrumb" className="flex items-center">
          {segments.length === 0 ? (
            <span className="font-mono text-xs text-on-surface-variant">MetaForge</span>
          ) : (
            <ol className="flex items-center gap-1.5">
              {segments.map((seg, idx) => (
                <li key={idx} className="flex items-center gap-1.5">
                  {idx > 0 && (
                    <span className="font-mono text-xs text-on-surface-variant" aria-hidden="true">
                      /
                    </span>
                  )}
                  <span
                    className={
                      seg.isCurrent
                        ? 'font-mono text-xs font-medium text-on-surface'
                        : 'font-mono text-xs text-on-surface-variant'
                    }
                    aria-current={seg.isCurrent ? 'page' : undefined}
                  >
                    {seg.label}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </nav>

        {/* Right-side actions */}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setRunDialogOpen(true)}
            className="rounded px-3 py-1 font-sans text-xs font-medium transition-colors"
            style={{
              background: '#e67e22',
              color: '#111319',
            }}
          >
            Run Agent
          </button>
        </div>
      </header>

      {runDialogOpen && (
        <RunAgentDialog onClose={() => setRunDialogOpen(false)} />
      )}
    </>
  );
}
