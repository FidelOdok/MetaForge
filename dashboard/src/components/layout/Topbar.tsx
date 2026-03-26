import { useEffect, useState } from 'react';
import { useLocation, useParams } from 'react-router-dom';
import { TopbarChatToggle } from './TopbarChatToggle';
import { RunAgentDialog } from '../shared/RunAgentDialog';

// ---------------------------------------------------------------------------
// Route → page name mapping
// ---------------------------------------------------------------------------

const SEGMENT_LABELS: Record<string, string> = {
  projects:  'Platform',
  sessions:  'Orchestrator',
  approvals: 'Approvals',
  bom:       'BOM',
  twin:      'Digital Twin',
  files:     'Knowledge',
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

  const parts = pathname.replace(/^\//, '').split('/').filter(Boolean);

  const segments: BreadcrumbSegment[] = parts.map((part, idx) => {
    const isLast = idx === parts.length - 1;
    const isId = /^[0-9a-f-]{8,}$/i.test(part) || Object.values(params).includes(part);

    let label: string;
    if (isId) {
      label = part.length > 8 ? `${part.slice(0, 8)}…` : part;
    } else {
      label = SEGMENT_LABELS[part] ?? part.charAt(0).toUpperCase() + part.slice(1);
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
          <TopbarChatToggle />
        </div>
      </header>

      {runDialogOpen && (
        <RunAgentDialog onClose={() => setRunDialogOpen(false)} />
      )}
    </>
  );
}
