import { useState } from 'react';
import { Link } from 'react-router-dom';

import { ApprovalCard } from '../components/approvals/ApprovalCard';
import { EmptyState } from '../components/ui/EmptyState';
import { useActiveProject } from '../hooks/use-active-project';
import { useApprovals } from '../hooks/use-approvals';
import { formatRelativeTime } from '../utils/format-time';
import type { ApprovalItem, ApprovalKind } from '../types/approvals';

const KC = {
  onSurface: 'var(--mf-c-e2e2eb)',
  onSurfaceVariant: 'var(--mf-c-9a9aaa)',
  success: 'var(--mf-c-3dd68c)',
  error: 'var(--mf-c-ffb4ab)',
  border: 'var(--mf-r-65-72-90-0p2)',
  glass: 'var(--mf-r-30-31-38-0p85)',
  surfaceLowest: 'var(--mf-c-0c0e14)',
} as const;

const KINDS: { value: ApprovalKind; label: string }[] = [
  { value: 'gate', label: 'Gate' },
  { value: 'flow_proposal', label: 'Flow proposal' },
  { value: 'flow_version', label: 'Flow version' },
  { value: 'tool_call', label: 'Tool call' },
  { value: 'human_authority', label: 'Human authority' },
  { value: 'design_change', label: 'Design change' },
  { value: 'design_loop', label: 'Design loop' },
  { value: 'sketch', label: 'Sketch' },
  { value: 'drawing', label: 'Drawing' },
];

type Tab = 'pending' | 'audit';

const panel: React.CSSProperties = {
  background: KC.glass,
  backdropFilter: 'blur(16px)',
  WebkitBackdropFilter: 'blur(16px)',
  border: `1px solid ${KC.border}`,
  borderRadius: 4,
};

function AuditTable({ items }: { items: ApprovalItem[] }) {
  return (
    <div className="overflow-auto">
      <table className="w-full font-mono" style={{ fontSize: 12, color: KC.onSurface }}>
        <thead>
          <tr style={{ color: KC.onSurfaceVariant, textAlign: 'left' }}>
            <th>Item</th>
            <th>Outcome</th>
            <th>Who</th>
            <th>Surface</th>
            <th>Agent</th>
            <th>On behalf of</th>
            <th>Verified</th>
            <th>When</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const d = item.decision;
            return (
              <tr key={item.id} data-testid="audit-row" style={{ borderTop: `1px solid ${KC.border}` }}>
                <td>{item.title}</td>
                <td style={{ color: item.status === 'approved' ? KC.success : item.status === 'rejected' ? KC.error : undefined }}>
                  {d ? d.decision : item.status}
                  {d?.reason ? `: ${d.reason}` : ''}
                </td>
                <td>{d?.approver ?? 'unknown'}</td>
                <td>{d?.surface ?? 'unknown'}</td>
                <td>{d?.agent ?? 'none'}</td>
                <td>{d?.on_behalf_of ?? 'none'}</td>
                <td>{d ? (d.approver_verified ? 'verified' : 'unverified') : 'n/a'}</td>
                <td>{d ? formatRelativeTime(d.decided_at) : 'n/a'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ApprovalsPage() {
  const { activeProjectId } = useActiveProject();
  const [tab, setTab] = useState<Tab>('pending');
  const [kind, setKind] = useState<ApprovalKind | ''>('');

  const query = useApprovals({
    status: tab === 'pending' ? 'pending' : 'decided',
    projectId: activeProjectId ?? undefined,
    kind: kind || undefined,
  });
  const items = query.data?.items ?? [];
  // Items with no project are dropped server-side when a project is selected.
  // Said out loud: an approval that quietly disappears is the one nobody answers.
  const unscoped = query.data?.unscopedCount ?? 0;

  return (
    <div>
      <div className="mb-5 flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-lg font-medium leading-tight" style={{ color: KC.onSurface }}>
            Approvals
          </h1>
          <span className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>
            Human-in-the-loop review · {activeProjectId ? 'this project' : 'all projects'}
          </span>
        </div>
        <label className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>
          Kind{' '}
          <select
            aria-label="Filter by kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as ApprovalKind | '')}
            className="rounded p-1"
            style={{ background: KC.surfaceLowest, color: KC.onSurface, border: `1px solid ${KC.border}` }}
          >
            <option value="">All kinds</option>
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div role="tablist" aria-label="Approvals views" className="flex gap-2 mb-3">
        {(['pending', 'audit'] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className="font-mono rounded px-3 py-1.5"
            style={{
              fontSize: 12,
              letterSpacing: '0.06em',
              color: tab === t ? KC.onSurface : KC.onSurfaceVariant,
              border: `1px solid ${tab === t ? 'var(--mf-c-ffb783)' : KC.border}`,
              background: 'transparent',
            }}
          >
            {t === 'pending' ? 'PENDING' : 'AUDIT'}
          </button>
        ))}
      </div>

      {unscoped > 0 && (
        <p className="font-mono" role="status" data-testid="approvals-unscoped" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>
          {unscoped} item{unscoped === 1 ? '' : 's'} not shown: no project recorded.
        </p>
      )}

      {query.isLoading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="rounded-lg h-24 animate-pulse" style={{ background: KC.glass, border: `1px solid ${KC.border}` }} />
          ))}
        </div>
      ) : query.isError ? (
        <div className="workspace-empty" role="alert">
          <h2>Approvals could not be loaded</h2>
          <p>Check your gateway connection and try again.</p>
          <Link className="text-action" to="/settings">
            Connection settings
          </Link>
        </div>
      ) : (
        <div style={panel}>
          <div style={{ padding: 12 }}>
            {items.length === 0 ? (
              <EmptyState
                title={tab === 'pending' ? 'Nothing awaiting approval' : 'No decisions recorded yet'}
                description={
                  tab === 'pending'
                    ? 'Gates, proposals, held tool calls and design changes that need a human will appear here.'
                    : 'Decided approvals, with who decided and from where, will appear here.'
                }
                icon={
                  <span className="material-symbols-outlined" style={{ fontSize: 40, color: KC.onSurfaceVariant }}>
                    inbox
                  </span>
                }
              />
            ) : tab === 'pending' ? (
              <div className="space-y-3">
                {items.map((item) => (
                  <ApprovalCard key={item.id} item={item} />
                ))}
              </div>
            ) : (
              <AuditTable items={items} />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
