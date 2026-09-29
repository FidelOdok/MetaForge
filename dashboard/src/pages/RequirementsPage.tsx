import { useState } from 'react';
import { Link } from 'react-router-dom';
import { EmptyState } from '../components/ui/EmptyState';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { useToast } from '../components/ui/Toast';
import { useActiveProject } from '../hooks/use-active-project';
import {
  useRequirementMatrix,
  useRequirementQuality,
  useProposeRequirementFix,
  useCreateConstraint,
} from '../hooks/use-requirements';
import type {
  EvidenceSummary,
  PassFail,
  RequirementMatrixRow,
  RequirementMatrixStatus,
  RequirementRecord,
} from '../types/requirements';

const OPERATORS = ['<=', '>=', '==', '<', '>', '!='] as const;

const FIELD_STYLE: React.CSSProperties = {
  background: 'var(--mf-c-191b22)',
  border: '1px solid var(--mf-r-65-72-90-0p3)',
};

/** FORGE-259 (gap G-A3): the constraint editor -- pick metric, operator,
 * limit, unit, target node directly, no hand-typed Python expression. */
function NewConstraintForm({ projectId, onDone }: { projectId: string; onDone: () => void }) {
  const toast = useToast();
  const createConstraint = useCreateConstraint();
  const [name, setName] = useState('');
  const [metric, setMetric] = useState('');
  const [operator, setOperator] = useState<(typeof OPERATORS)[number]>('<=');
  const [limit, setLimit] = useState('');
  const [unit, setUnit] = useState('');
  const [targetNodeType, setTargetNodeType] = useState('');

  const limitValue = Number(limit);
  const canSubmit = name.trim() !== '' && metric.trim() !== '' && limit !== '' && !Number.isNaN(limitValue);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    createConstraint.mutate(
      {
        projectId,
        name: name.trim(),
        metric: metric.trim(),
        operator,
        limit: limitValue,
        unit: unit.trim(),
        targetNodeType: targetNodeType.trim(),
      },
      {
        onSuccess: () => {
          toast.success(`Recorded ${name.trim()}`);
          onDone();
        },
        onError: (err) => {
          const detail =
            (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
          toast.error(detail || 'Could not record the constraint');
        },
      },
    );
  };

  return (
    <form
      onSubmit={handleSubmit}
      data-testid="new-constraint-form"
      className="mb-3 flex flex-wrap items-end gap-2 rounded-lg px-3 py-3"
      style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
    >
      <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
        Name
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="tip_deflection"
          className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
          style={{ ...FIELD_STYLE, width: '160px' }}
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
        Metric
        <input
          value={metric}
          onChange={(e) => setMetric(e.target.value)}
          placeholder="tip_deflection"
          className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
          style={{ ...FIELD_STYLE, width: '160px' }}
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
        Operator
        <select
          value={operator}
          onChange={(e) => setOperator(e.target.value as (typeof OPERATORS)[number])}
          className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none appearance-none"
          style={{ ...FIELD_STYLE, width: '72px' }}
        >
          {OPERATORS.map((op) => (
            <option key={op} value={op}>
              {op}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
        Limit
        <input
          value={limit}
          onChange={(e) => setLimit(e.target.value)}
          placeholder="0.5"
          inputMode="decimal"
          className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
          style={{ ...FIELD_STYLE, width: '80px' }}
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
        Unit
        <input
          value={unit}
          onChange={(e) => setUnit(e.target.value)}
          placeholder="mm"
          className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
          style={{ ...FIELD_STYLE, width: '72px' }}
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
        Target node type
        <input
          value={targetNodeType}
          onChange={(e) => setTargetNodeType(e.target.value)}
          placeholder="cad_model"
          className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
          style={{ ...FIELD_STYLE, width: '140px' }}
        />
      </label>
      <div className="flex gap-2">
        <Button
          type="submit"
          size="sm"
          disabled={!canSubmit || createConstraint.isPending}
        >
          {createConstraint.isPending ? 'Recording…' : 'Record'}
        </Button>
        <Button type="button" variant="secondary" size="sm" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

const PRODUCT_TYPES = [
  { value: 'generic', label: 'Generic' },
  { value: 'robotic_arm', label: 'Robotic arm' },
];

const AXES: Array<{ key: keyof RequirementRecord; label: string }> = [
  { key: 'clarity', label: 'Clarity' },
  { key: 'atomicity', label: 'Atomic' },
  { key: 'quantified', label: 'Quantified' },
  { key: 'traceability', label: 'Traceable' },
  { key: 'verificationReady', label: 'Verifiable' },
];

function FlagBadge({ value }: { value: PassFail | null }) {
  if (value === null) return <Badge variant="default">n/a</Badge>;
  return <Badge variant={value === 'pass' ? 'success' : 'error'}>{value}</Badge>;
}

function RequirementRow({ requirement }: { requirement: RequirementRecord }) {
  const toast = useToast();
  const proposeFix = useProposeRequirementFix();
  const [proposal, setProposal] = useState<{ text: string; rationale: string } | null>(null);

  const failingAxes = AXES.filter(({ key }) => requirement[key] === 'fail').length;
  const hasIssues = failingAxes > 0 || requirement.conflicts.length > 0;

  const handleFix = () => {
    proposeFix.mutate(requirement.id, {
      onSuccess: (result) => {
        if (result.proposedText) {
          setProposal({ text: result.proposedText, rationale: result.rationale });
        } else {
          toast.error('No rewrite could be generated for this requirement');
        }
      },
      onError: () => toast.error('Could not reach the requirement author agent'),
    });
  };

  return (
    <>
      <tr
        className="hover:bg-[var(--mf-c-282a30)] cursor-default"
        style={{ borderBottom: proposal ? 'none' : '1px solid var(--mf-r-65-72-90-0p1)' }}
      >
        <td className="px-3 py-2 text-xs text-on-surface" style={{ maxWidth: '360px' }}>
          <div className="font-medium">{requirement.name}</div>
          <div className="text-on-surface-variant" style={{ fontSize: '11px' }}>
            {requirement.text}
          </div>
        </td>
        {AXES.map(({ key }) => (
          <td key={key} className="px-2 text-center">
            <FlagBadge value={requirement[key] as PassFail | null} />
          </td>
        ))}
        <td className="px-2 text-center">
          {requirement.conflicts.length > 0 ? (
            <Badge variant="error">{requirement.conflicts.length}</Badge>
          ) : (
            <span className="text-on-surface-variant text-xs">—</span>
          )}
        </td>
        <td className="px-3 text-right">
          {hasIssues && (
            <button
              type="button"
              onClick={handleFix}
              disabled={proposeFix.isPending}
              className="rounded px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
              style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
            >
              {proposeFix.isPending ? 'Asking…' : 'Fix with AI'}
            </button>
          )}
        </td>
      </tr>
      {proposal && (
        <tr style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p1)' }}>
          <td colSpan={AXES.length + 3} className="px-3 pb-2">
            <div
              className="rounded px-3 py-2 text-xs"
              style={{ background: 'var(--mf-c-191b22)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
            >
              <div className="text-on-surface-variant" style={{ marginBottom: '4px' }}>
                Proposed rewrite (not applied — review and add via the requirement graph):
              </div>
              <div className="text-on-surface">{proposal.text}</div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

const STATUS_VARIANT: Record<RequirementMatrixStatus, 'success' | 'warning' | 'error' | 'default' | 'info'> = {
  pass: 'success',
  uncertain: 'warning',
  fail: 'error',
  no_data: 'default',
  stale: 'info',
};

function formatNumber(n: number | null): string {
  return n === null ? '—' : n.toFixed(3).replace(/\.?0+$/, '') || '0';
}

function EvidenceDetail({ evidence }: { evidence: EvidenceSummary }) {
  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded px-2 py-1 text-xs"
      style={{ background: 'var(--mf-c-191b22)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
    >
      <span className="font-mono text-on-surface-variant">{evidence.method || 'unknown method'}</span>
      {evidence.tier !== null && <span className="text-on-surface-variant">tier {evidence.tier}</span>}
      <span className="text-on-surface">
        value {formatNumber(evidence.value)} / limit {formatNumber(evidence.limit)}
      </span>
      {evidence.margin !== null && (
        <span className="text-on-surface-variant">margin {formatNumber(evidence.margin)}</span>
      )}
      <Badge variant={evidence.staleness === 'current' || evidence.staleness === 'revalidated' ? 'success' : 'warning'}>
        {evidence.staleness}
      </Badge>
    </div>
  );
}

function MatrixRow({ row }: { row: RequirementMatrixRow }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <>
      <tr
        className="hover:bg-[var(--mf-c-282a30)] cursor-default"
        style={{ borderBottom: expanded ? 'none' : '1px solid var(--mf-r-65-72-90-0p1)' }}
      >
        <td className="px-3 py-2 text-xs text-on-surface" style={{ maxWidth: '320px' }}>
          <div className="font-medium">{row.requirementName}</div>
          <div className="text-on-surface-variant" style={{ fontSize: '11px' }}>
            {row.limitText}
          </div>
        </td>
        <td className="px-2 text-center">
          <Badge variant={STATUS_VARIANT[row.status]}>{row.status}</Badge>
        </td>
        <td className="px-3 py-2 text-xs text-on-surface-variant">{row.detail}</td>
        <td className="px-2 text-center">
          {row.evidence.length > 0 ? (
            <button
              type="button"
              onClick={() => setExpanded((e) => !e)}
              className="rounded px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
              style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
            >
              {row.evidence.length} evidence {expanded ? '▲' : '▼'}
            </button>
          ) : (
            <span className="text-on-surface-variant text-xs">—</span>
          )}
        </td>
        <td className="px-3 text-right">
          <Link to="/twin" className="text-xs text-tertiary hover:underline">
            Structure
          </Link>
        </td>
      </tr>
      {expanded && (
        <tr style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p1)' }}>
          <td colSpan={5} className="px-3 pb-2">
            <div className="flex flex-col gap-1">
              {row.evidence.map((e) => (
                <EvidenceDetail key={e.id} evidence={e} />
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function exportMatrixCsv(rows: RequirementMatrixRow[]) {
  const header = ['Requirement', 'Limit', 'Status', 'Detail', 'Evidence Count'];
  const csvRows = rows.map((r) => [r.requirementName, r.limitText, r.status, r.detail, String(r.evidence.length)]);
  const csv = [header, ...csvRows].map((r) => r.map((v) => `"${v}"`).join(',')).join('\n');
  downloadFile(csv, 'requirement-matrix.csv', 'text/csv');
}

function exportMatrixMd(rows: RequirementMatrixRow[]) {
  const header = '| Requirement | Limit | Status | Detail |\n|---|---|---|---|';
  const body = rows
    .map((r) => `| ${r.requirementName} | ${r.limitText} | ${r.status} | ${r.detail} |`)
    .join('\n');
  downloadFile(`${header}\n${body}\n`, 'requirement-matrix.md', 'text/markdown');
}

function downloadFile(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function RequirementMatrixSection({ projectId }: { projectId?: string }) {
  const { data: matrix, isLoading } = useRequirementMatrix(projectId);
  const rows = matrix?.rows ?? [];
  const [showForm, setShowForm] = useState(false);

  if (!isLoading && rows.length === 0 && !showForm && !projectId) return null;

  return (
    <div className="mb-6" data-testid="requirements-matrix">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-medium text-on-surface" style={{ margin: 0 }}>
          Evidence matrix
        </h2>
        <div className="flex gap-2">
          {projectId && !showForm && (
            <button
              type="button"
              data-testid="new-constraint-button"
              onClick={() => setShowForm(true)}
              className="rounded px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
              style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
            >
              + New constraint
            </button>
          )}
          {rows.length > 0 && (
            <>
              <button
                type="button"
                onClick={() => exportMatrixCsv(rows)}
                className="rounded px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
                style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
              >
                Export CSV
              </button>
              <button
                type="button"
                onClick={() => exportMatrixMd(rows)}
                className="rounded px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
                style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
              >
                Export MD
              </button>
            </>
          )}
        </div>
      </div>
      {projectId && showForm && (
        <NewConstraintForm projectId={projectId} onDone={() => setShowForm(false)} />
      )}
      {!isLoading && rows.length === 0 && (
        <EmptyState
          title="No structured requirements yet"
          description="Record one above to see it tracked here."
        />
      )}
      {!isLoading && rows.length > 0 && (
        <div
          className="rounded-lg overflow-hidden overflow-x-auto"
          style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          <table className="w-full text-left border-collapse">
            <thead>
              <tr style={{ background: 'var(--mf-c-191b22)' }}>
                <th className="px-3 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-left" style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
                  Requirement
                </th>
                <th className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-center" style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
                  Status
                </th>
                <th className="px-3 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-left" style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
                  Detail
                </th>
                <th className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-center" style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
                  Evidence
                </th>
                <th style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }} aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <MatrixRow key={row.requirementId} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function RequirementsPage() {
  const { activeProjectId } = useActiveProject();
  const [productType, setProductType] = useState('generic');
  const { data: report, isLoading } = useRequirementQuality(
    activeProjectId ?? undefined,
    productType,
  );

  const requirements = report?.requirements ?? [];
  const conflicts = report?.conflicts ?? [];
  const completeness = report?.completeness;

  return (
    <div data-testid="requirements-panel">
      <div className="mb-4 flex items-start justify-between">
        <div>
          <h1 className="text-lg font-medium text-on-surface" style={{ margin: 0 }}>
            Requirements
          </h1>
          <span className="font-mono text-xs text-on-surface-variant">
            {requirements.length} requirements &middot; {conflicts.length} conflicts
          </span>
        </div>
        <select
          value={productType}
          onChange={(e) => setProductType(e.target.value)}
          aria-label="Product type"
          className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none appearance-none"
          style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
        >
          {PRODUCT_TYPES.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </div>

      <RequirementMatrixSection projectId={activeProjectId ?? undefined} />

      {completeness && (
        <div
          data-testid="requirements-completeness"
          className="mb-4 flex flex-wrap items-center gap-2 rounded-lg px-3 py-2"
          style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          <span className="text-xs text-on-surface-variant" style={{ marginRight: '4px' }}>
            Checklist ({completeness.productType}):
          </span>
          {completeness.covered.map((id) => (
            <Badge key={id} variant="success">
              {id}
            </Badge>
          ))}
          {completeness.missing.map((id) => (
            <Badge key={id} variant="error">
              missing: {id}
            </Badge>
          ))}
        </div>
      )}

      {conflicts.length > 0 && (
        <div
          data-testid="requirements-conflicts"
          className="mb-4 rounded-lg px-3 py-2"
          style={{ background: 'rgba(255,180,171,0.08)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          <div className="text-xs text-on-surface-variant" style={{ marginBottom: '4px' }}>
            Conflicting requirements
          </div>
          {conflicts.map((pair) => (
            <div key={`${pair.aId}-${pair.bId}`} className="text-xs text-on-surface">
              <strong>{pair.aName}</strong> vs <strong>{pair.bName}</strong> — {pair.detail}
            </div>
          ))}
        </div>
      )}

      {isLoading && (
        <div
          className="rounded-lg overflow-hidden"
          style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          {[...Array(4)].map((_, i) => (
            <div
              key={i}
              className="animate-pulse"
              style={{
                height: '36px',
                borderBottom: '1px solid var(--mf-r-65-72-90-0p1)',
                background: i % 2 === 0 ? 'var(--mf-r-40-42-48-0p3)' : 'transparent',
              }}
            />
          ))}
        </div>
      )}

      {!isLoading && requirements.length === 0 && (
        <EmptyState
          title="No requirements yet"
          description={
            activeProjectId
              ? 'This project has no requirements recorded yet.'
              : 'Select a project to view its requirements.'
          }
        />
      )}

      {!isLoading && requirements.length > 0 && (
        <div
          data-testid="requirements-quality-table"
          className="rounded-lg overflow-hidden overflow-x-auto"
          style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          <table className="w-full text-left border-collapse">
            <thead>
              <tr style={{ background: 'var(--mf-c-191b22)' }}>
                <th
                  className="px-3 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-left"
                  style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}
                >
                  Requirement
                </th>
                {AXES.map(({ key, label }) => (
                  <th
                    key={key}
                    className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-center"
                    style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}
                  >
                    {label}
                  </th>
                ))}
                <th
                  className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-center"
                  style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}
                >
                  Conflicts
                </th>
                <th
                  style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}
                  aria-label="Actions"
                />
              </tr>
            </thead>
            <tbody>
              {requirements.map((requirement) => (
                <RequirementRow key={requirement.id} requirement={requirement} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
