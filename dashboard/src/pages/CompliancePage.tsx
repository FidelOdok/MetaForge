import { useMemo, useState } from 'react';
import { useActiveProject } from '../hooks/use-active-project';
import { useChecklist, useLinkEvidence } from '../hooks/use-compliance';
import { EmptyState } from '../components/ui/EmptyState';
import { Button } from '../components/ui/Button';
import type { ChecklistItem, ComplianceRegime, EvidenceType } from '../types/compliance';

// ─── Kinetic Console design tokens ──────────────────────────────────────────
const KC = {
  surface: '#111319',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  success: '#3dd68c',
  warning: '#f59e0b',
  error: '#ffb4ab',
  tertiary: '#86cfff',
  border: 'rgba(65,72,90,0.2)',
  glass: 'rgba(30,31,38,0.85)',
  surfaceHigh: '#282a30',
} as const;

const glassPanel: React.CSSProperties = {
  background: KC.glass,
  backdropFilter: 'blur(16px)',
  WebkitBackdropFilter: 'blur(16px)',
  border: `1px solid ${KC.border}`,
  borderRadius: 4,
};

const ALL_REGIMES: ComplianceRegime[] = ['UKCA', 'CE', 'FCC', 'PSTI'];
const EVIDENCE_TYPES: EvidenceType[] = [
  'TEST_REPORT',
  'DECLARATION',
  'CERTIFICATE',
  'TECHNICAL_FILE',
  'RISK_ASSESSMENT',
];

const REGIME_COLOR: Record<ComplianceRegime, string> = {
  UKCA: KC.tertiary,
  CE: KC.warning,
  FCC: KC.success,
  PSTI: '#c792ea',
};

function statusIcon(status: ChecklistItem['evidence_status']): { symbol: string; color: string } {
  switch (status) {
    case 'APPROVED':
      return { symbol: '✓', color: KC.success };
    case 'REVIEWED':
      return { symbol: '✓', color: KC.tertiary };
    case 'UPLOADED':
      return { symbol: '⏳', color: KC.warning };
    default:
      return { symbol: '✗', color: KC.error };
  }
}

// ─── Market filter chips ────────────────────────────────────────────────────
function MarketChips({
  selected,
  onToggle,
}: {
  selected: ComplianceRegime[];
  onToggle: (regime: ComplianceRegime) => void;
}) {
  return (
    <div className="flex gap-1.5">
      {ALL_REGIMES.map((regime) => {
        const active = selected.includes(regime);
        return (
          <button
            key={regime}
            type="button"
            onClick={() => onToggle(regime)}
            aria-pressed={active}
            className="font-mono rounded"
            style={{
              fontSize: 10,
              letterSpacing: '0.06em',
              padding: '4px 10px',
              border: 'none',
              cursor: 'pointer',
              background: active ? REGIME_COLOR[regime] : KC.surfaceHigh,
              color: active ? KC.surface : KC.onSurfaceVariant,
              transition: 'background 0.15s, color 0.15s',
            }}
          >
            {regime}
          </button>
        );
      })}
    </div>
  );
}

// ─── Regime summary card ────────────────────────────────────────────────────
function RegimeCard({ regime, items }: { regime: ComplianceRegime; items: ChecklistItem[] }) {
  const total = items.length;
  const evidenced = items.filter((i) => i.evidence_status !== 'MISSING').length;
  const pct = total > 0 ? (evidenced / total) * 100 : 0;
  const color = REGIME_COLOR[regime];

  return (
    <div style={{ ...glassPanel, padding: 16, borderLeft: `2px solid ${color}` }}>
      <div className="flex items-center justify-between mb-2">
        <span className="font-mono uppercase" style={{ fontSize: 11, letterSpacing: '0.08em', color }}>
          {regime}
        </span>
      </div>
      <div className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant, marginBottom: 10 }}>
        {evidenced} / {total} requirements
      </div>
      <div style={{ height: 4, background: 'rgba(154,154,170,0.15)', borderRadius: 2 }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 2, transition: 'width 0.4s ease' }} />
      </div>
    </div>
  );
}

// ─── Add-evidence inline form ───────────────────────────────────────────────
function AddEvidenceForm({
  item,
  projectId,
  onDone,
}: {
  item: ChecklistItem;
  projectId: string;
  onDone: () => void;
}) {
  const [title, setTitle] = useState('');
  const [evidenceType, setEvidenceType] = useState<EvidenceType>(item.evidence_type);
  const [description, setDescription] = useState('');
  const link = useLinkEvidence(projectId);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    link.mutate(
      {
        checklist_item_id: item.id,
        evidence_type: evidenceType,
        title: title.trim(),
        description: description.trim() || undefined,
      },
      { onSuccess: onDone },
    );
  }

  return (
    <form onSubmit={handleSubmit} className="px-4 py-3 space-y-2" style={{ background: 'rgba(0,0,0,0.15)' }}>
      <input
        type="text"
        placeholder="Evidence title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        autoFocus
        style={{
          width: '100%',
          background: KC.surfaceHigh,
          border: `1px solid ${KC.border}`,
          borderRadius: 4,
          padding: '6px 8px',
          fontSize: 12,
          color: KC.onSurface,
          outline: 'none',
        }}
      />
      <select
        value={evidenceType}
        onChange={(e) => setEvidenceType(e.target.value as EvidenceType)}
        style={{
          width: '100%',
          background: KC.surfaceHigh,
          border: `1px solid ${KC.border}`,
          borderRadius: 4,
          padding: '6px 8px',
          fontSize: 12,
          color: KC.onSurface,
          outline: 'none',
        }}
      >
        {EVIDENCE_TYPES.map((t) => (
          <option key={t} value={t}>
            {t.replace(/_/g, ' ')}
          </option>
        ))}
      </select>
      <textarea
        placeholder="Description (optional)"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        rows={2}
        style={{
          width: '100%',
          background: KC.surfaceHigh,
          border: `1px solid ${KC.border}`,
          borderRadius: 4,
          padding: '6px 8px',
          fontSize: 12,
          color: KC.onSurface,
          outline: 'none',
          resize: 'vertical',
        }}
      />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" type="button" onClick={onDone}>
          Cancel
        </Button>
        <Button variant="primary" size="sm" type="submit" disabled={!title.trim() || link.isPending}>
          {link.isPending ? 'Linking…' : 'Link evidence'}
        </Button>
      </div>
    </form>
  );
}

// ─── Checklist row ───────────────────────────────────────────────────────────
function ChecklistRow({
  item,
  projectId,
  expanded,
  onToggle,
}: {
  item: ChecklistItem;
  projectId: string;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { symbol, color } = statusIcon(item.evidence_status);
  const isMissing = item.evidence_status === 'MISSING';

  return (
    <div style={{ borderBottom: `1px solid rgba(65,72,90,0.08)` }}>
      <div
        role="button"
        tabIndex={isMissing ? 0 : undefined}
        onClick={isMissing ? onToggle : undefined}
        className="flex items-center gap-3 px-4"
        style={{ height: 40, cursor: isMissing ? 'pointer' : 'default' }}
      >
        <span style={{ fontSize: 13, color, flexShrink: 0, width: 14, textAlign: 'center' }}>{symbol}</span>
        <span
          className="font-mono flex-shrink-0"
          style={{ fontSize: 10, color: KC.onSurfaceVariant, width: 100 }}
        >
          {item.id}
        </span>
        <span
          style={{
            fontSize: 12,
            color: KC.onSurface,
            flex: 1,
            overflow: 'hidden',
            whiteSpace: 'nowrap',
            textOverflow: 'ellipsis',
          }}
        >
          {item.requirement}
        </span>
        <span
          className="font-mono rounded px-1.5 flex-shrink-0"
          style={{ fontSize: 9, color: REGIME_COLOR[item.regime], background: KC.surfaceHigh }}
        >
          {item.regime}
        </span>
      </div>
      {expanded && <AddEvidenceForm item={item} projectId={projectId} onDone={onToggle} />}
    </div>
  );
}

// ─── Page ────────────────────────────────────────────────────────────────────
export function CompliancePage() {
  const { activeProjectId, activeProject } = useActiveProject();
  const [markets, setMarkets] = useState<ComplianceRegime[]>(['UKCA', 'CE']);
  // Keyed by `${panel}:${item.id}`, not just the item id — a missing item
  // renders once in "Missing Evidence" and again in the full "Checklist",
  // and expanding one shouldn't silently expand the other too.
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const { data: checklist, isLoading } = useChecklist(activeProjectId ?? undefined, markets);

  function toggleMarket(regime: ComplianceRegime) {
    setMarkets((prev) =>
      prev.includes(regime) ? prev.filter((m) => m !== regime) : [...prev, regime],
    );
  }

  const itemsByRegime = useMemo(() => {
    const grouped: Record<string, ChecklistItem[]> = {};
    for (const item of checklist?.items ?? []) {
      (grouped[item.regime] ??= []).push(item);
    }
    return grouped;
  }, [checklist]);

  const missingItems = useMemo(
    () => (checklist?.items ?? []).filter((i) => i.evidence_status === 'MISSING'),
    [checklist],
  );

  if (!activeProjectId) {
    return (
      <div>
        <h1 className="text-lg font-medium" style={{ color: KC.onSurface, margin: 0, marginBottom: 12 }}>
          Compliance
        </h1>
        <EmptyState
          title="No project selected"
          description="Select a project from the Topbar to view its compliance checklist."
          icon={
            <span className="material-symbols-outlined" style={{ fontSize: 40, color: KC.onSurfaceVariant }}>
              verified_user
            </span>
          }
        />
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="mb-4 flex items-start justify-between">
        <div>
          <h1 className="text-lg font-medium leading-tight" style={{ color: KC.onSurface, margin: 0 }}>
            Compliance
          </h1>
          <span className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>
            {activeProject?.name ?? 'Project'} · {checklist?.total_items ?? 0} requirements
          </span>
        </div>
        <MarketChips selected={markets} onToggle={toggleMarket} />
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center gap-2" style={{ padding: '48px 0' }}>
          <span className="material-symbols-outlined" style={{ fontSize: 20, color: KC.onSurfaceVariant }}>
            progress_activity
          </span>
          <span className="font-mono" style={{ fontSize: 11, color: KC.onSurfaceVariant }}>
            Generating checklist…
          </span>
        </div>
      ) : !checklist || checklist.items.length === 0 ? (
        <EmptyState
          title="No checklist items"
          description={
            markets.length === 0
              ? 'Select at least one market above.'
              : 'No requirements were generated for the selected markets.'
          }
        />
      ) : (
        <>
          {/* Regime summary cards */}
          <div className="grid gap-3 mb-4" style={{ gridTemplateColumns: `repeat(${markets.length}, 1fr)` }}>
            {markets.map((regime) => (
              <RegimeCard key={regime} regime={regime} items={itemsByRegime[regime] ?? []} />
            ))}
          </div>

          {/* Two-column: missing evidence + full checklist */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            {/* Missing evidence */}
            <div style={{ ...glassPanel, overflow: 'hidden' }}>
              <div className="px-4" style={{ height: 36, display: 'flex', alignItems: 'center', borderBottom: `1px solid ${KC.border}` }}>
                <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
                  Missing Evidence · {missingItems.length}
                </span>
              </div>
              {missingItems.length === 0 ? (
                <div className="flex flex-col items-center justify-center gap-2" style={{ padding: '32px 0' }}>
                  <span className="material-symbols-outlined" style={{ fontSize: 28, color: KC.success, opacity: 0.6 }}>
                    check_circle
                  </span>
                  <span className="font-mono" style={{ fontSize: 11, color: KC.onSurfaceVariant }}>
                    Nothing missing
                  </span>
                </div>
              ) : (
                missingItems.map((item) => {
                  const key = `missing:${item.id}`;
                  return (
                    <ChecklistRow
                      key={item.id}
                      item={item}
                      projectId={activeProjectId}
                      expanded={expandedKey === key}
                      onToggle={() => setExpandedKey((k) => (k === key ? null : key))}
                    />
                  );
                })
              )}
            </div>

            {/* Full checklist */}
            <div style={{ ...glassPanel, overflow: 'hidden' }}>
              <div className="px-4" style={{ height: 36, display: 'flex', alignItems: 'center', borderBottom: `1px solid ${KC.border}` }}>
                <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
                  Checklist
                </span>
              </div>
              <div style={{ maxHeight: 480, overflowY: 'auto' }}>
                {checklist.items.map((item) => {
                  const key = `checklist:${item.id}`;
                  return (
                    <ChecklistRow
                      key={item.id}
                      item={item}
                      projectId={activeProjectId}
                      expanded={expandedKey === key}
                      onToggle={() => setExpandedKey((k) => (k === key ? null : key))}
                    />
                  );
                })}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
