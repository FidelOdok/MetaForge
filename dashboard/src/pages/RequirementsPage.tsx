import { useState } from 'react';
import { Link } from 'react-router-dom';
import { EmptyState } from '../components/ui/EmptyState';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { DecisionList } from '../components/shared/DecisionList';
import { useToast } from '../components/ui/Toast';
import { useActiveProject } from '../hooks/use-active-project';
import {
  useRequirementCoverage,
  useRequirementMatrix,
  useRequirementQuality,
  useProposeRequirementFix,
  useCreateConstraint,
} from '../hooks/use-requirements';
import { useCreateReleasePackage, useReleasePackages } from '../hooks/use-releases';
import { useGenerateTestPlan, useTestPlan } from '../hooks/use-test-plan';
import {
  useApproveDesignLoop,
  useDesignLoop,
  useStartDesignLoop,
} from '../hooks/use-design-loop';
import { useAttemptPromotion, usePromotionHistory } from '../hooks/use-promotion';
import { useGenerateFeature } from '../hooks/use-features';
import {
  useAddConceptOption,
  useConceptOptions,
  useSelectConcept,
} from '../hooks/use-trade-study';
import type {
  EvidenceSummary,
  PassFail,
  RequirementCoverage,
  RequirementMatrixRow,
  RequirementMatrixStatus,
  RequirementRecord,
} from '../types/requirements';
import type { DesignLoopIteration, DesignLoopStatus } from '../types/design-loop';
import type { AttemptPromotionResult, MaturityLevel } from '../types/promotion';
import type { FeatureType, GenerateFeatureResult } from '../types/features';
import type { ConceptOption } from '../types/trade-study';
import { useRevisionIndex } from '../hooks/use-items';
import { RevisionBadge } from '../components/items/RevisionBadge';
import type { RevisionIndexEntry } from '../api/endpoints/items';

const OPERATORS = ['<=', '>=', '==', '<', '>', '!='] as const;

// FORGE-258 (gap G-A2): same real evidence_type values twin.record_evidence
// itself accepts (twin_core.models.enums.EVIDENCE_TYPES) -- an empty option
// means "not declared", never guessed.
const EXPECTED_EVIDENCE_TYPES = [
  '',
  'calculation',
  'simulation',
  'test',
  'inspection',
  'demonstration',
  'datasheet',
  'external_reference',
] as const;

const MATURITY_LEVELS: MaturityLevel[] = [
  'concept',
  'sim_validated',
  'physically_validated',
  'released',
];

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
  const [verificationMethod, setVerificationMethod] = useState('');
  const [expectedEvidence, setExpectedEvidence] = useState<(typeof EXPECTED_EVIDENCE_TYPES)[number]>('');

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
        verificationMethod: verificationMethod.trim(),
        expectedEvidence,
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
      <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
        Verification method
        <input
          value={verificationMethod}
          onChange={(e) => setVerificationMethod(e.target.value)}
          placeholder="FEA"
          className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
          style={{ ...FIELD_STYLE, width: '120px' }}
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
        Expected evidence
        <select
          value={expectedEvidence}
          onChange={(e) =>
            setExpectedEvidence(e.target.value as (typeof EXPECTED_EVIDENCE_TYPES)[number])
          }
          className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none appearance-none"
          style={{ ...FIELD_STYLE, width: '140px' }}
        >
          {EXPECTED_EVIDENCE_TYPES.map((t) => (
            <option key={t} value={t}>
              {t || 'not declared'}
            </option>
          ))}
        </select>
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
  // FORGE-258 (gap G-A2): "red cells for unverified requirements" -- a
  // no_data row has no claim citing it at all, the literal "unverified"
  // state this ticket asks to flag.
  no_data: 'error',
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

function MatrixRow({
  row,
  revision,
  projectId,
}: {
  row: RequirementMatrixRow;
  // FORGE-526: the constraint set revision this requirement belongs to.
  revision?: RevisionIndexEntry;
  projectId?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <>
      <tr
        className="hover:bg-[var(--mf-c-282a30)] cursor-default"
        style={{ borderBottom: expanded ? 'none' : '1px solid var(--mf-r-65-72-90-0p1)' }}
      >
        <td className="px-3 py-2 text-xs text-on-surface" style={{ maxWidth: '320px' }}>
          <div className="font-medium flex items-center gap-1.5">
            {row.requirementName}
            {revision ? (
              <RevisionBadge entry={revision} projectId={projectId} />
            ) : (
              row.revisionRef && (
                <span
                  data-testid="requirement-revision-ref"
                  className="font-mono text-on-surface-variant"
                  style={{ fontSize: '10px' }}
                >
                  {row.revisionRef}
                </span>
              )
            )}
          </div>
          <div className="text-on-surface-variant" style={{ fontSize: '11px' }}>
            {row.limitText}
          </div>
          {(row.verificationMethod || row.expectedEvidence) && (
            <div className="text-on-surface-variant" style={{ fontSize: '11px' }}>
              {row.verificationMethod || '—'} · expects {row.expectedEvidence || '—'}
            </div>
          )}
        </td>
        <td className="px-2 text-center">
          <Badge variant={STATUS_VARIANT[row.status]}>{row.status}</Badge>
          {!row.verificationMethod && !row.expectedEvidence && (
            <div
              data-testid="unverified-badge"
              className="text-error"
              style={{ fontSize: '10px', marginTop: '2px' }}
            >
              not declared
            </div>
          )}
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
  const { data: revisionIndex } = useRevisionIndex(projectId);
  const rows = matrix?.rows ?? [];
  // FORGE-528: the rows are the current constraint set revision(s), the one
  // home for requirement values; say which.
  const revisionRefs = matrix?.revisionRefs ?? [];
  const [showForm, setShowForm] = useState(false);

  if (!isLoading && rows.length === 0 && !showForm && !projectId) return null;

  return (
    <div className="mb-6" data-testid="requirements-matrix">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-medium text-on-surface" style={{ margin: 0 }}>
          Evidence matrix
          {revisionRefs.length > 0 && (
            <span
              data-testid="requirements-revision"
              className="font-mono text-on-surface-variant"
              style={{ fontSize: '11px', fontWeight: 400, marginLeft: '8px' }}
              title="The current constraint set revision these requirements are read from"
            >
              current: {revisionRefs.join(', ')}
            </span>
          )}
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
                <MatrixRow
                  key={row.requirementId}
                  row={row}
                  revision={revisionIndex?.[row.requirementId]}
                  projectId={projectId}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// FORGE-297 (gap G-I1): coverage heatmap tiles for the 5 TraceabilityAgent
// percentages. Color bands are advisory only -- no gate reads this
// component; `attempt_promotion` (FORGE-290/319) is the real live gate and
// consumes the evidence matrix above, not this coverage summary.
const COVERAGE_TILES: { key: keyof RequirementCoverage; label: string }[] = [
  { key: 'needs_to_requirements', label: 'Needs → Requirements' },
  { key: 'requirements_to_architecture', label: 'Requirements → Architecture' },
  { key: 'requirements_to_verification', label: 'Requirements → Verification' },
  { key: 'verification_to_evidence', label: 'Verification → Evidence' },
  { key: 'critical_requirements_to_evidence', label: 'Critical Reqs → Evidence' },
];

function coverageTileColor(value: number | null): string {
  if (value === null) return 'var(--mf-r-65-72-90-0p3)';
  if (value >= 80) return 'var(--mf-c-success, #4caf7d)';
  if (value >= 50) return 'var(--mf-c-warning, #d4a843)';
  return 'var(--mf-c-error, #d4595e)';
}

function CoverageHeatmapSection({ projectId }: { projectId?: string }) {
  const { data: coverage, isLoading } = useRequirementCoverage(projectId);

  if (!projectId || isLoading || !coverage) return null;

  return (
    <div className="mb-6" data-testid="requirements-coverage">
      <h2 className="mb-2 text-sm font-medium text-on-surface" style={{ margin: 0 }}>
        Traceability coverage
      </h2>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-5">
        {COVERAGE_TILES.map((tile) => {
          const value = coverage[tile.key];
          return (
            <div
              key={tile.key}
              data-testid={`coverage-tile-${tile.key}`}
              className="rounded-lg p-3"
              style={{
                background: 'var(--mf-r-30-31-38-0p85)',
                border: `1px solid ${coverageTileColor(value)}`,
              }}
            >
              <div className="text-[10px] uppercase tracking-widest text-on-surface-variant">
                {tile.label}
              </div>
              <div
                className="mt-1 text-lg font-mono"
                style={{ color: value === null ? 'var(--mf-c-on-surface-variant)' : coverageTileColor(value) }}
              >
                {value === null ? 'N/A' : `${value}%`}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// FORGE-299 (gap G-I3): a versioned snapshot of a project's current
// hierarchy/BOM/evidence/decision node ids, gated server-side at creation
// time on evaluate_g8_release(...) returning PASSED -- a 409 here means
// the project isn't release-ready yet, and names the failing check(s).
// "Diff against previous release" is a simple count delta, not a full
// structural diff (api_gateway/twin/release_package.py's own module
// docstring explains why).
function ReleasePackagesSection({ projectId }: { projectId?: string }) {
  const toast = useToast();
  const { data: releases, isLoading } = useReleasePackages(projectId);
  const createRelease = useCreateReleasePackage(projectId);
  const [notes, setNotes] = useState('');

  if (!projectId) return null;

  const handleCreate = () => {
    createRelease.mutate(notes.trim() || undefined, {
      onSuccess: (pkg) => {
        toast.success(`Created release package: ${pkg.title ?? pkg.nodeId}`);
        setNotes('');
      },
      onError: (err) => {
        const detail =
          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        toast.error(detail || 'Could not create a release package');
      },
    });
  };

  return (
    <div className="mb-6" data-testid="release-packages">
      <h2 className="mb-2 text-sm font-medium text-on-surface" style={{ margin: 0 }}>
        Release packages
      </h2>
      <div className="mt-2 mb-3 flex flex-wrap items-center gap-2">
        <input
          type="text"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Optional release title (e.g. v1.0 release candidate)"
          data-testid="release-notes-input"
          className="min-w-[240px] flex-1 rounded-lg px-3 py-2 text-sm"
          style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
        />
        <Button
          onClick={handleCreate}
          disabled={createRelease.isPending}
          data-testid="create-release-button"
        >
          {createRelease.isPending ? 'Creating…' : 'Create release package'}
        </Button>
      </div>

      {isLoading ? null : !releases || releases.length === 0 ? (
        <EmptyState
          title="No release packages yet"
          description="Create one once this project's G8 release gate (baseline, evidence, verification, waivers, release approval) passes."
        />
      ) : (
        <div className="flex flex-col gap-2">
          {[...releases].reverse().map((pkg) => (
            <div
              key={pkg.nodeId}
              data-testid={`release-package-${pkg.nodeId}`}
              className="rounded-lg p-3"
              style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-on-surface">{pkg.title}</span>
                <Badge variant="success">{pkg.gateStatus}</Badge>
              </div>
              {pkg.createdAt && (
                <div className="mt-0.5 text-[10px] text-on-surface-variant">
                  {new Date(pkg.createdAt).toLocaleString()}
                </div>
              )}
              <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                <SnapshotCount
                  label="Hierarchy"
                  count={pkg.snapshot.hierarchyNodeIds.length}
                  delta={pkg.diffFromPrevious.hierarchyDelta}
                />
                <SnapshotCount
                  label="BOM"
                  count={pkg.snapshot.bomItemIds.length}
                  delta={pkg.diffFromPrevious.bomDelta}
                />
                <SnapshotCount
                  label="Evidence"
                  count={pkg.snapshot.evidenceIds.length}
                  delta={pkg.diffFromPrevious.evidenceDelta}
                />
                <SnapshotCount
                  label="Decisions"
                  count={pkg.snapshot.decisionIds.length}
                  delta={pkg.diffFromPrevious.decisionDelta}
                />
              </div>
              {pkg.diffFromPrevious.comparedTo === null && (
                <div className="mt-1 text-[10px] text-on-surface-variant">First release</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SnapshotCount({
  label,
  count,
  delta,
}: {
  label: string;
  count: number;
  delta: number;
}) {
  const deltaText = delta === 0 ? '±0' : delta > 0 ? `+${delta}` : `${delta}`;
  const deltaColor =
    delta > 0
      ? 'var(--mf-c-success, #4caf7d)'
      : delta < 0
        ? 'var(--mf-c-error, #d4595e)'
        : 'var(--mf-c-on-surface-variant)';
  return (
    <div className="rounded-lg px-2 py-1.5" style={{ background: 'var(--mf-c-282a30)' }}>
      <div className="text-[10px] uppercase tracking-widest text-on-surface-variant">{label}</div>
      <div className="mt-0.5 flex items-baseline gap-1.5">
        <span className="font-mono text-sm text-on-surface">{count}</span>
        <span className="font-mono text-[10px]" style={{ color: deltaColor }}>
          {deltaText}
        </span>
      </div>
    </div>
  );
}

// FORGE-298 (gap G-I2): mechanically derives one verification_case per real
// requirement with verification_method == "test" from the requirement's own
// metric/operator/limit/unit/target_node_type fields -- a test step plus its
// acceptance value, no new authoring/synthesis. Generating twice creates
// duplicate entries per requirement (api_gateway/twin/test_plan.py's own
// module docstring explains why this mirrors release-package's semantics).
function TestPlanSection({ projectId }: { projectId?: string }) {
  const toast = useToast();
  const { data: entries, isLoading } = useTestPlan(projectId);
  const generate = useGenerateTestPlan(projectId);

  if (!projectId) return null;

  const handleGenerate = () => {
    generate.mutate(undefined, {
      onSuccess: (newEntries) => {
        toast.success(
          newEntries.length === 0
            ? 'No test-method requirements found on this project yet'
            : `Generated ${newEntries.length} test-plan ${newEntries.length === 1 ? 'entry' : 'entries'}`,
        );
      },
      onError: () => {
        toast.error('Could not generate a test plan');
      },
    });
  };

  return (
    <div className="mb-6" data-testid="test-plan">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-on-surface" style={{ margin: 0 }}>
          Test plan
        </h2>
        <Button
          onClick={handleGenerate}
          disabled={generate.isPending}
          data-testid="generate-test-plan-button"
        >
          {generate.isPending ? 'Generating…' : 'Generate test plan'}
        </Button>
      </div>

      {isLoading ? null : !entries || entries.length === 0 ? (
        <EmptyState
          title="No test-plan entries yet"
          description="Generated from requirements whose verification method is 'test'. Add one, or generate once this project has requirements declaring verification_method='test'."
        />
      ) : (
        <div className="overflow-x-auto rounded-lg" style={{ border: '1px solid var(--mf-r-65-72-90-0p2)' }}>
          <table className="w-full text-sm">
            <thead>
              <tr style={{ background: 'var(--mf-c-282a30)' }}>
                <th className="px-3 py-2 text-left text-[10px] uppercase tracking-widest text-on-surface-variant">
                  Step
                </th>
                <th className="px-3 py-2 text-left text-[10px] uppercase tracking-widest text-on-surface-variant">
                  Acceptance value
                </th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr
                  key={entry.nodeId}
                  data-testid={`test-plan-entry-${entry.nodeId}`}
                  style={{ borderTop: '1px solid var(--mf-r-65-72-90-0p1)' }}
                >
                  <td className="px-3 py-2 text-on-surface">{entry.step}</td>
                  <td className="px-3 py-2 font-mono text-on-surface">{entry.acceptanceValue}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function IterationRow({ iteration }: { iteration: DesignLoopIteration }) {
  const badgeVariant =
    iteration.status === 'converged' ? 'success' : iteration.status === 'infeasible' ? 'error' : 'default';
  return (
    <tr
      data-testid="design-loop-iteration-row"
      style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p1)' }}
    >
      <td className="px-3 py-2 font-mono text-xs text-on-surface-variant">
        {iteration.iteration_number}
      </td>
      <td className="px-2 py-2 font-mono text-xs text-on-surface text-right">
        {iteration.parameter_value.toFixed(4)} <span className="text-on-surface-variant">mm</span>
      </td>
      <td className="px-2 py-2 font-mono text-xs text-on-surface text-right">
        {iteration.objective_value.toFixed(4)} <span className="text-on-surface-variant">kg</span>
      </td>
      <td className="px-2 py-2 text-center">
        <Badge variant={iteration.feasible ? 'success' : 'error'}>
          {iteration.feasible ? 'feasible' : 'infeasible'}
        </Badge>
      </td>
      <td className="px-2 py-2 text-center">
        <Badge variant={badgeVariant}>{iteration.status}</Badge>
      </td>
      <td className="px-2 py-2 text-center">
        {iteration.is_winner &&
          (iteration.approved ? (
            <Badge variant="success" data-testid="design-loop-approved-badge">
              approved by {iteration.approved_by}
            </Badge>
          ) : (
            <span className="text-xs text-on-surface-variant">awaiting approval</span>
          ))}
      </td>
    </tr>
  );
}

/** FORGE-291 (gap G-G5): the "Loop health panel" the ticket's own dashboard
 * wording asks for, built from real available data only -- iteration_count
 * vs. max_iterations (the actual, honest iteration budget; "tokens" doesn't
 * apply here, this loop makes zero LLM calls), the run's status, and a
 * "duplicate of an earlier run" badge when this exact call already ran
 * before (FORGE-291's own duplicate-commit guard). Only rendered right
 * after a run (the mutation's own last result) -- max_iterations/duplicate
 * are properties of ONE invocation, not persisted loop state, so there is
 * nothing honest to show after a page reload. */
function LoopHealthPanel({
  status,
  iterationCount,
  maxIterations,
  duplicate,
}: {
  status: DesignLoopStatus;
  iterationCount: number;
  maxIterations: number;
  duplicate: boolean;
}) {
  const statusVariant = status === 'infeasible' ? 'error' : 'success';
  return (
    <div
      data-testid="loop-health-panel"
      className="flex flex-wrap items-center gap-2 px-3 py-2"
      style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}
    >
      <Badge variant={statusVariant}>{status}</Badge>
      <span className="font-mono text-[11px] text-on-surface-variant">
        {iterationCount} / {maxIterations} iterations
      </span>
      {duplicate && (
        <Badge variant="warning" data-testid="loop-duplicate-badge">
          duplicate of an earlier run
        </Badge>
      )}
    </div>
  );
}

/** FORGE-288 (gap G-G2): a tiny hand-rolled inline-SVG sparkline plotting
 * objective_value vs iteration_number -- no chart library in this
 * codebase, and the ticket's own "optimisation run view: objective vs
 * iteration chart" wording is satisfied by the smallest real version of
 * that, not a new dependency or page. Generic: works for whichever
 * parameter/metric the loop actually swept (wall_thickness_mm/mass_kg,
 * height_mm/mass_kg, ...). */
function IterationSparkline({ iterations }: { iterations: DesignLoopIteration[] }) {
  if (iterations.length < 2) return null;
  const width = 280;
  const height = 48;
  const padding = 4;
  const values = iterations.map((it) => it.objective_value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = iterations
    .map((it, i) => {
      const x = padding + (i / (iterations.length - 1)) * (width - 2 * padding);
      const y = height - padding - ((it.objective_value - min) / span) * (height - 2 * padding);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const metric = iterations[0]?.metric ?? 'objective';

  return (
    <div data-testid="design-loop-sparkline" className="px-3 pt-2 pb-1">
      <div className="mb-1 text-[10px] font-mono uppercase tracking-widest text-on-surface-variant">
        {metric} vs iteration
      </div>
      <svg width={width} height={height} role="img" aria-label={`${metric} across ${iterations.length} iterations`}>
        <polyline points={points} fill="none" stroke="var(--mf-c-86cfff, #86cfff)" strokeWidth={1.5} />
        {iterations.map((it, i) => {
          const x = padding + (i / (iterations.length - 1)) * (width - 2 * padding);
          const y = height - padding - ((it.objective_value - min) / span) * (height - 2 * padding);
          return (
            <circle
              key={it.iteration_number}
              cx={x}
              cy={y}
              r={it.is_winner ? 2.5 : 1.5}
              fill={it.is_winner ? 'var(--mf-c-3dd68c, #3dd68c)' : 'var(--mf-c-86cfff, #86cfff)'}
            />
          );
        })}
      </svg>
    </div>
  );
}

/** FORGE-287 (gap G-G1): the closed design loop's iteration timeline --
 * propose -> build -> simulate -> evaluate against constraints -> revise ->
 * repeat, until pass or proven infeasible, with a human approving the
 * winning candidate at the end. Reuses the same bisection search
 * twin.optimize_parameter (FORGE-320) already runs, persisting every
 * candidate it evaluates as a real, queryable DesignLoopIteration instead
 * of one opaque Evidence blob. */
function DesignLoopSection({ projectId }: { projectId?: string }) {
  const toast = useToast();
  const [showForm, setShowForm] = useState(false);
  const [loopId, setLoopId] = useState<string | null>(null);
  const [workProductId, setWorkProductId] = useState('');
  const [loadN, setLoadN] = useState('');
  const [deflectionLimitMm, setDeflectionLimitMm] = useState('');
  const [sfLimit, setSfLimit] = useState('2.0');

  const start = useStartDesignLoop();
  const { data: report, isLoading } = useDesignLoop(loopId ?? undefined);
  const approve = useApproveDesignLoop();

  const canSubmit =
    workProductId.trim() !== '' && loadN !== '' && deflectionLimitMm !== '' && !Number.isNaN(Number(loadN));

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    start.mutate(
      {
        workProductId: workProductId.trim(),
        loadN: Number(loadN),
        deflectionLimitMm: Number(deflectionLimitMm),
        sfLimit: Number(sfLimit) || 2.0,
        projectId,
      },
      {
        onSuccess: (result) => {
          setLoopId(result.loop_id);
          setShowForm(false);
          toast.success(`Design loop ${result.status}: ${result.detail}`);
        },
        onError: (err) => {
          const detail =
            (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
          toast.error(detail || 'Could not start the design loop');
        },
      },
    );
  };

  const iterations = report?.iterations ?? [];
  const winner = iterations.find((it) => it.is_winner);

  return (
    <div className="mb-6" data-testid="design-loop-section">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-medium text-on-surface" style={{ margin: 0 }}>
          Design loop
        </h2>
        {projectId && !showForm && (
          <button
            type="button"
            data-testid="start-design-loop-button"
            onClick={() => setShowForm(true)}
            className="rounded px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
            style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
          >
            + Start design loop
          </button>
        )}
      </div>

      {projectId && showForm && (
        <form
          data-testid="design-loop-form"
          onSubmit={handleSubmit}
          className="mb-3 flex flex-wrap items-end gap-2 rounded-lg p-3"
          style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
            CAD work product id
            <input
              value={workProductId}
              onChange={(e) => setWorkProductId(e.target.value)}
              placeholder="work product id"
              className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
              style={{ ...FIELD_STYLE, width: '220px' }}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
            Load (N)
            <input
              value={loadN}
              onChange={(e) => setLoadN(e.target.value)}
              placeholder="100"
              type="number"
              className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
              style={{ ...FIELD_STYLE, width: '90px' }}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
            Deflection limit (mm)
            <input
              value={deflectionLimitMm}
              onChange={(e) => setDeflectionLimitMm(e.target.value)}
              placeholder="0.5"
              type="number"
              className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
              style={{ ...FIELD_STYLE, width: '90px' }}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
            Safety factor limit
            <input
              value={sfLimit}
              onChange={(e) => setSfLimit(e.target.value)}
              type="number"
              className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
              style={{ ...FIELD_STYLE, width: '90px' }}
            />
          </label>
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={!canSubmit || start.isPending}>
              {start.isPending ? 'Running…' : 'Run'}
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={() => setShowForm(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}

      {!loopId && !showForm && (
        <EmptyState
          title="No design loop run yet"
          description="Start one above to iterate a CAD work product's wall thickness toward minimum mass, subject to deflection and safety-factor constraints."
        />
      )}

      {loopId && !isLoading && iterations.length > 0 && (
        <div
          className="rounded-lg overflow-hidden overflow-x-auto"
          style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          {start.data && (
            <LoopHealthPanel
              status={start.data.status}
              iterationCount={start.data.iteration_count}
              maxIterations={start.data.max_iterations}
              duplicate={start.data.duplicate}
            />
          )}
          <IterationSparkline iterations={iterations} />
          <table className="w-full text-left border-collapse">
            <thead>
              <tr style={{ background: 'var(--mf-c-191b22)' }}>
                <th className="px-3 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-left" style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
                  #
                </th>
                <th className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-right" style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
                  {iterations[0]?.parameter_name ?? 'parameter_value'}
                </th>
                <th className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-right" style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
                  {iterations[0]?.metric ?? 'objective_value'}
                </th>
                <th className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-center" style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
                  Feasible
                </th>
                <th className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-center" style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
                  Status
                </th>
                <th className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-center" style={{ height: '32px', borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
                  Approval
                </th>
              </tr>
            </thead>
            <tbody>
              {iterations.map((it) => (
                <IterationRow key={it.iteration_number} iteration={it} />
              ))}
            </tbody>
          </table>
          {winner && !winner.approved && (
            <div className="flex justify-end p-2">
              <Button
                size="sm"
                data-testid="approve-design-loop-button"
                disabled={approve.isPending}
                onClick={() =>
                  approve.mutate(
                    { loopId, approvedBy: 'fidel.odok@idroneinnovations.com' },
                    {
                      onSuccess: () => toast.success('Design loop winner approved'),
                      onError: () => toast.error('Could not approve the design loop'),
                    },
                  )
                }
              >
                {approve.isPending ? 'Approving…' : 'Approve winner'}
              </Button>
            </div>
          )}
        </div>
      )}

      {winner && <DecisionList nodeId={winner.id} heading="Decision" />}
    </div>
  );
}

function DecisionBadge({ decision }: { decision: string }) {
  const variant =
    decision === 'pass' || decision === 'waived'
      ? 'success'
      : decision === 'uncertain'
        ? 'warning'
        : 'error';
  return <Badge variant={variant}>{decision}</Badge>;
}

/** FORGE-290 (gap G-G4): evidence-gated approvals -- gates that evaluate
 * real measured data (the same live requirement matrix status the evidence
 * matrix above already renders) and block on `no_data`, plus a human
 * approve/reject-with-comment veto on top (FORGE-319's own gate only ever
 * refused on bad evidence; a reviewer can also refuse good evidence). */
function GateReviewSection({
  projectId,
  requirements,
}: {
  projectId?: string;
  requirements: RequirementRecord[];
}) {
  const toast = useToast();
  const [showForm, setShowForm] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [level, setLevel] = useState<MaturityLevel>('sim_validated');
  const [comment, setComment] = useState('');
  const [lastResult, setLastResult] = useState<AttemptPromotionResult | null>(null);

  const attempt = useAttemptPromotion();
  const { data: history } = usePromotionHistory(projectId);

  const toggle = (id: string) => {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const submit = (reject: boolean) => {
    if (!projectId || selectedIds.length === 0) return;
    attempt.mutate(
      {
        projectId,
        level,
        requiredClaimIds: selectedIds,
        comment: comment.trim() || undefined,
        reject,
      },
      {
        onSuccess: (result) => {
          setLastResult(result);
          toast[result.promoted ? 'success' : 'error'](
            result.promoted ? `Promoted to ${result.level}` : result.blockedReason || 'Blocked',
          );
        },
        onError: (err) => {
          const detail =
            (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
          toast.error(detail || 'Could not evaluate the gate');
        },
      },
    );
  };

  const canSubmit = !!projectId && selectedIds.length > 0;

  return (
    <div className="mb-6" data-testid="gate-review-section">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-medium text-on-surface" style={{ margin: 0 }}>
          Gate review
        </h2>
        {projectId && !showForm && (
          <button
            type="button"
            data-testid="open-gate-review-button"
            onClick={() => setShowForm(true)}
            className="rounded px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
            style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
          >
            + Attempt promotion
          </button>
        )}
      </div>

      {projectId && showForm && (
        <div
          data-testid="gate-review-form"
          className="mb-3 rounded-lg p-3"
          style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          <div className="mb-2 text-xs text-on-surface-variant">Required claims</div>
          <div className="mb-3 flex flex-wrap gap-2">
            {requirements.map((req) => (
              <label
                key={req.id}
                className="flex items-center gap-1 rounded px-2 py-1 text-xs text-on-surface"
                style={{ ...FIELD_STYLE, cursor: 'pointer' }}
              >
                <input
                  type="checkbox"
                  checked={selectedIds.includes(req.id)}
                  onChange={() => toggle(req.id)}
                />
                {req.name}
              </label>
            ))}
            {requirements.length === 0 && (
              <span className="text-xs text-on-surface-variant">No requirements to gate on yet.</span>
            )}
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
              Level
              <select
                value={level}
                onChange={(e) => setLevel(e.target.value as MaturityLevel)}
                className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none appearance-none"
                style={{ ...FIELD_STYLE, width: '160px' }}
              >
                {MATURITY_LEVELS.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-1 flex-col gap-1 text-xs text-on-surface-variant" style={{ minWidth: '200px' }}>
              Comment
              <input
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="rationale (optional on approve, becomes the reason on reject)"
                className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
                style={FIELD_STYLE}
              />
            </label>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                data-testid="approve-gate-button"
                disabled={!canSubmit || attempt.isPending}
                onClick={() => submit(false)}
              >
                {attempt.isPending ? 'Evaluating…' : 'Approve'}
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                data-testid="reject-gate-button"
                disabled={!canSubmit || attempt.isPending}
                onClick={() => submit(true)}
              >
                Reject
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={() => setShowForm(false)}>
                Close
              </Button>
            </div>
          </div>
        </div>
      )}

      {lastResult && (
        <div
          data-testid="gate-review-result"
          className="mb-3 rounded-lg p-3"
          style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          <div className="mb-2 flex items-center gap-2">
            <Badge variant={lastResult.promoted ? 'success' : 'error'}>
              {lastResult.promoted ? 'promoted' : 'blocked'}
            </Badge>
            <span className="text-xs text-on-surface-variant">{lastResult.level}</span>
          </div>
          {lastResult.blockedReason && (
            <div className="mb-2 text-xs text-on-surface">{lastResult.blockedReason}</div>
          )}
          <div className="flex flex-col gap-1">
            {lastResult.results.map((r) => (
              <div key={r.requirementId} className="flex items-center gap-2 text-xs text-on-surface">
                <DecisionBadge decision={r.decision} />
                <span>{r.requirementName}</span>
                <span className="text-on-surface-variant">{r.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {history && history.gates.length > 0 && (
        <div data-testid="gate-review-history" className="flex flex-col gap-1">
          {history.gates.map((g) => (
            <div
              key={g.gateId}
              className="flex items-center gap-2 rounded px-2 py-1 text-xs text-on-surface-variant"
              style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p1)' }}
            >
              <Badge variant={g.promoted ? 'success' : 'error'}>{g.promoted ? 'promoted' : 'blocked'}</Badge>
              <span>{g.level}</span>
              {g.decidedBy && <span>by {g.decidedBy}</span>}
              {g.comment && <span>&mdash; {g.comment}</span>}
            </div>
          ))}
        </div>
      )}

      {!showForm && !lastResult && (!history || history.gates.length === 0) && (
        <EmptyState
          title="No gate attempts yet"
          description="Attempt a promotion above to gate on real measured data -- blocks on no_data, not just fail."
        />
      )}
    </div>
  );
}

const FEATURE_TYPES: FeatureType[] = ['bolt_pattern', 'rib'];

/** FORGE-269 (gap G-D1): the parametric feature library -- pick a named
 * feature (bolt_pattern, rib -- see domain_agents/shared/design_ir_macros.py
 * for the full library and which features remain deferred), fill its typed
 * parameters, generate a standalone CAD_MODEL work product from it. Does
 * NOT modify an existing committed part in place -- that is FORGE-270's
 * own gap ("editable parameters on committed parts"), a separate ticket. */
function FeatureLibrarySection({ projectId }: { projectId?: string }) {
  const toast = useToast();
  const [showForm, setShowForm] = useState(false);
  const [featureType, setFeatureType] = useState<FeatureType>('bolt_pattern');
  const [name, setName] = useState('');
  const [plateLength, setPlateLength] = useState('60');
  const [plateWidth, setPlateWidth] = useState('40');
  const [plateThickness, setPlateThickness] = useState('5');
  const [holeDiameter, setHoleDiameter] = useState('4');
  const [holeCount, setHoleCount] = useState('4');
  const [patternRadius, setPatternRadius] = useState('15');
  const [ribLength, setRibLength] = useState('30');
  const [ribHeight, setRibHeight] = useState('20');
  const [ribThickness, setRibThickness] = useState('3');
  const [lastResult, setLastResult] = useState<GenerateFeatureResult | null>(null);

  const generate = useGenerateFeature();

  const canSubmit = name.trim() !== '';

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    const feature =
      featureType === 'bolt_pattern'
        ? {
            feature_type: 'bolt_pattern' as const,
            plate_length_mm: Number(plateLength),
            plate_width_mm: Number(plateWidth),
            plate_thickness_mm: Number(plateThickness),
            hole_diameter_mm: Number(holeDiameter),
            hole_count: Number(holeCount),
            pattern_radius_mm: Number(patternRadius),
          }
        : {
            feature_type: 'rib' as const,
            length_mm: Number(ribLength),
            height_mm: Number(ribHeight),
            thickness_mm: Number(ribThickness),
          };
    generate.mutate(
      { name: name.trim(), feature, projectId, adapter: 'freecad' },
      {
        onSuccess: (result) => {
          setLastResult(result);
          setShowForm(false);
          toast.success(`Generated ${result.feature_type}: ${result.entity_count} entities`);
        },
        onError: (err) => {
          const detail =
            (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
          toast.error(detail || 'Could not generate the feature');
        },
      },
    );
  };

  return (
    <div className="mb-6" data-testid="feature-library-section">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-medium text-on-surface" style={{ margin: 0 }}>
          Feature library
        </h2>
        {!showForm && (
          <button
            type="button"
            data-testid="open-feature-library-button"
            onClick={() => setShowForm(true)}
            className="rounded px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
            style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
          >
            + Generate feature
          </button>
        )}
      </div>

      {showForm && (
        <form
          data-testid="feature-library-form"
          onSubmit={handleSubmit}
          className="mb-3 flex flex-wrap items-end gap-2 rounded-lg p-3"
          style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
            Feature
            <select
              value={featureType}
              onChange={(e) => setFeatureType(e.target.value as FeatureType)}
              className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none appearance-none"
              style={{ ...FIELD_STYLE, width: '140px' }}
            >
              {FEATURE_TYPES.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
            Name
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Motor mount bolt pattern"
              className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
              style={{ ...FIELD_STYLE, width: '200px' }}
            />
          </label>

          {featureType === 'bolt_pattern' && (
            <>
              <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                Plate length (mm)
                <input value={plateLength} onChange={(e) => setPlateLength(e.target.value)} type="number" className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none" style={{ ...FIELD_STYLE, width: '80px' }} />
              </label>
              <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                Plate width (mm)
                <input value={plateWidth} onChange={(e) => setPlateWidth(e.target.value)} type="number" className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none" style={{ ...FIELD_STYLE, width: '80px' }} />
              </label>
              <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                Plate thickness (mm)
                <input value={plateThickness} onChange={(e) => setPlateThickness(e.target.value)} type="number" className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none" style={{ ...FIELD_STYLE, width: '80px' }} />
              </label>
              <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                Hole diameter (mm)
                <input value={holeDiameter} onChange={(e) => setHoleDiameter(e.target.value)} type="number" className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none" style={{ ...FIELD_STYLE, width: '80px' }} />
              </label>
              <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                Hole count
                <input value={holeCount} onChange={(e) => setHoleCount(e.target.value)} type="number" className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none" style={{ ...FIELD_STYLE, width: '70px' }} />
              </label>
              <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                Pattern radius (mm)
                <input value={patternRadius} onChange={(e) => setPatternRadius(e.target.value)} type="number" className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none" style={{ ...FIELD_STYLE, width: '80px' }} />
              </label>
            </>
          )}

          {featureType === 'rib' && (
            <>
              <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                Length (mm)
                <input value={ribLength} onChange={(e) => setRibLength(e.target.value)} type="number" className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none" style={{ ...FIELD_STYLE, width: '80px' }} />
              </label>
              <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                Height (mm)
                <input value={ribHeight} onChange={(e) => setRibHeight(e.target.value)} type="number" className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none" style={{ ...FIELD_STYLE, width: '80px' }} />
              </label>
              <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                Thickness (mm)
                <input value={ribThickness} onChange={(e) => setRibThickness(e.target.value)} type="number" className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none" style={{ ...FIELD_STYLE, width: '80px' }} />
              </label>
            </>
          )}

          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={!canSubmit || generate.isPending}>
              {generate.isPending ? 'Generating…' : 'Generate'}
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={() => setShowForm(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}

      {lastResult && (
        <div
          data-testid="feature-library-result"
          className="rounded-lg p-3"
          style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          <div className="mb-1 flex items-center gap-2">
            <Badge variant="success">{lastResult.feature_type}</Badge>
            <span className="text-xs text-on-surface-variant">
              {lastResult.entity_count} entities &middot; {lastResult.volume_mm3.toFixed(1)}mm&sup3;
            </span>
          </div>
          {lastResult.committed && lastResult.model_url && (
            <a
              href={lastResult.model_url}
              className="text-xs"
              style={{ color: 'var(--mf-c-86cfff, #86cfff)' }}
            >
              View committed model
            </a>
          )}
          {lastResult.commit_error && (
            <div className="text-xs text-on-surface-variant">
              not committed: {lastResult.commit_error}
            </div>
          )}
        </div>
      )}

      {!showForm && !lastResult && (
        <EmptyState
          title="No features generated yet"
          description="Generate a bolt pattern or rib above from the parametric feature library."
        />
      )}
    </div>
  );
}

const TRADE_STUDY_CRITERIA = ['mass_kg', 'cost_usd', 'risk', 'performance'] as const;

/** FORGE-262 (gap G-B2): "Trade-study view: options as columns, criteria
 * rows, weights editable, scores from evidence; 'select concept' creates a
 * decision." Weighted scores are recomputed client-side from the already-
 * fetched criteria_scores as weights change -- only the FINAL committed
 * weights get baked into the Decision's rationale at selection time
 * (POST /v1/trade-study/select), matching every other "editable before
 * commit" pattern in this codebase (e.g. the Design Loop section's own
 * form). Only mass_kg has any real measured source anywhere in this
 * codebase today -- a criterion an option marks evidence_backed_criteria
 * gets a small tertiary marker distinguishing it from an asserted number,
 * same discipline the evidence matrix above uses for no_data vs pass. */
function TradeStudySection({ projectId }: { projectId?: string }) {
  const toast = useToast();
  const [showAddForm, setShowAddForm] = useState(false);
  const [title, setTitle] = useState('');
  const [scores, setScores] = useState<Record<string, string>>({});
  const [massIsGrounded, setMassIsGrounded] = useState(false);
  const [weights, setWeights] = useState<Record<string, number>>({
    mass_kg: -1,
    cost_usd: -0.01,
    risk: -1,
    performance: 1,
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rationale, setRationale] = useState('');

  const { data: options } = useConceptOptions(projectId);
  const addOption = useAddConceptOption(projectId);
  const select = useSelectConcept();

  const canAddOption = title.trim() !== '';
  const handleAddOption = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canAddOption) return;
    const criteriaScores: Record<string, number> = {};
    for (const c of TRADE_STUDY_CRITERIA) {
      const v = scores[c];
      if (v !== undefined && v !== '') criteriaScores[c] = Number(v);
    }
    addOption.mutate(
      {
        title: title.trim(),
        criteriaScores,
        evidenceBackedCriteria: massIsGrounded ? ['mass_kg'] : [],
        projectId,
      },
      {
        onSuccess: () => {
          setTitle('');
          setScores({});
          setMassIsGrounded(false);
          setShowAddForm(false);
        },
        onError: () => toast.error('Could not record the concept option'),
      },
    );
  };

  const scored = (options ?? []).map((o: ConceptOption) => ({
    ...o,
    weighted_score: TRADE_STUDY_CRITERIA.reduce(
      (sum, c) => sum + (weights[c] ?? 0) * (o.criteria_scores[c] ?? 0),
      0,
    ),
  }));

  const canSelect = !!selectedId && rationale.trim() !== '';
  const handleSelect = () => {
    if (!selectedId || !options) return;
    const decisionTitle = `Concept selection: ${options.find((o) => o.id === selectedId)?.title ?? selectedId}`;
    select.mutate(
      {
        optionIds: options.map((o) => o.id),
        selectedOptionId: selectedId,
        weights,
        title: decisionTitle,
        rationale: rationale.trim(),
        projectId,
      },
      {
        onSuccess: () => {
          toast.success('Concept selected -- recorded as a Decision');
          setSelectedId(null);
          setRationale('');
        },
        onError: (err) => {
          const detail =
            (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
          toast.error(detail || 'Could not record the selection');
        },
      },
    );
  };

  return (
    <div className="mb-6" data-testid="trade-study-section">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-medium text-on-surface" style={{ margin: 0 }}>
          Concept trade study
        </h2>
        {projectId && !showAddForm && (
          <button
            type="button"
            data-testid="open-add-option-button"
            onClick={() => setShowAddForm(true)}
            className="rounded px-2 py-1 text-xs text-on-surface-variant hover:text-on-surface transition-colors"
            style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
          >
            + Add option
          </button>
        )}
      </div>

      {projectId && showAddForm && (
        <form
          data-testid="add-option-form"
          onSubmit={handleAddOption}
          className="mb-3 flex flex-wrap items-end gap-2 rounded-lg p-3"
          style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
            Option name
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Hollow tube"
              className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
              style={{ ...FIELD_STYLE, width: '160px' }}
            />
          </label>
          {TRADE_STUDY_CRITERIA.map((c) => (
            <label key={c} className="flex flex-col gap-1 text-xs text-on-surface-variant">
              {c}
              <input
                value={scores[c] ?? ''}
                onChange={(e) => setScores((s) => ({ ...s, [c]: e.target.value }))}
                type="number"
                className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
                style={{ ...FIELD_STYLE, width: '80px' }}
              />
            </label>
          ))}
          <label className="flex items-center gap-1 text-xs text-on-surface-variant">
            <input
              type="checkbox"
              checked={massIsGrounded}
              onChange={(e) => setMassIsGrounded(e.target.checked)}
            />
            mass_kg is a real measured value
          </label>
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={!canAddOption || addOption.isPending}>
              {addOption.isPending ? 'Adding…' : 'Add'}
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={() => setShowAddForm(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}

      {!options || options.length === 0 ? (
        <EmptyState
          title="No concept options recorded yet"
          description="Record 2-4 candidate architectures above, weigh the criteria that matter, then select one -- the selection is recorded as a real Decision with the rejected options as its alternatives."
        />
      ) : (
        <div
          className="rounded-lg overflow-hidden overflow-x-auto"
          style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          <table className="w-full text-left border-collapse" data-testid="trade-study-table">
            <thead>
              <tr style={{ background: 'var(--mf-c-191b22)' }}>
                <th className="px-3 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-left" style={{ height: 32 }}>
                  Criterion
                </th>
                <th className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-right" style={{ height: 32 }}>
                  Weight
                </th>
                {scored.map((o) => (
                  <th
                    key={o.id}
                    data-testid="trade-study-option-column"
                    className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-right"
                    style={{ height: 32 }}
                  >
                    {o.title}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {TRADE_STUDY_CRITERIA.map((c) => (
                <tr key={c} style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
                  <td className="px-3 py-1 text-xs text-on-surface">{c}</td>
                  <td className="px-2 py-1 text-right">
                    <input
                      value={weights[c] ?? 0}
                      onChange={(e) =>
                        setWeights((w) => ({ ...w, [c]: Number(e.target.value) || 0 }))
                      }
                      type="number"
                      step="0.01"
                      data-testid={`trade-study-weight-${c}`}
                      className="rounded px-1 py-0.5 text-xs text-on-surface text-right focus:outline-none"
                      style={{ ...FIELD_STYLE, width: '64px' }}
                    />
                  </td>
                  {scored.map((o) => {
                    const grounded = o.evidence_backed_criteria.includes(c);
                    return (
                      <td
                        key={o.id}
                        className="px-2 py-1 text-right font-mono text-xs text-on-surface-variant"
                        title={grounded ? 'from a real measured value' : 'asserted, not measured'}
                        style={grounded ? { color: 'var(--mf-c-86cfff, #86cfff)' } : undefined}
                      >
                        {o.criteria_scores[c] ?? '—'}
                      </td>
                    );
                  })}
                </tr>
              ))}
              <tr style={{ background: 'var(--mf-c-191b22)' }}>
                <td className="px-3 py-1 text-xs font-medium text-on-surface" colSpan={2}>
                  Weighted score
                </td>
                {scored.map((o) => (
                  <td
                    key={o.id}
                    data-testid="trade-study-weighted-score"
                    className="px-2 py-1 text-right font-mono text-xs font-medium text-on-surface"
                  >
                    {o.weighted_score.toFixed(3)}
                  </td>
                ))}
              </tr>
              <tr>
                <td className="px-3 py-2" colSpan={2 + scored.length}>
                  <div className="flex flex-wrap items-end gap-2">
                    <label className="flex flex-col gap-1 text-xs text-on-surface-variant">
                      Select
                      <select
                        value={selectedId ?? ''}
                        onChange={(e) => setSelectedId(e.target.value || null)}
                        data-testid="trade-study-select-option"
                        className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
                        style={{ ...FIELD_STYLE, width: '160px' }}
                      >
                        <option value="">choose an option…</option>
                        {scored.map((o) => (
                          <option key={o.id} value={o.id}>
                            {o.title}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="flex flex-col gap-1 text-xs text-on-surface-variant" style={{ flex: 1 }}>
                      Rationale
                      <input
                        value={rationale}
                        onChange={(e) => setRationale(e.target.value)}
                        placeholder="Why this option, over the others?"
                        className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
                        style={{ ...FIELD_STYLE, width: '100%' }}
                      />
                    </label>
                    <Button
                      size="sm"
                      data-testid="select-concept-button"
                      disabled={!canSelect || select.isPending}
                      onClick={handleSelect}
                    >
                      {select.isPending ? 'Recording…' : 'Select concept'}
                    </Button>
                  </div>
                </td>
              </tr>
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

      <CoverageHeatmapSection projectId={activeProjectId ?? undefined} />

      <RequirementMatrixSection projectId={activeProjectId ?? undefined} />

      <DesignLoopSection projectId={activeProjectId ?? undefined} />

      <GateReviewSection projectId={activeProjectId ?? undefined} requirements={requirements} />

      <ReleasePackagesSection projectId={activeProjectId ?? undefined} />

      <TestPlanSection projectId={activeProjectId ?? undefined} />

      <FeatureLibrarySection projectId={activeProjectId ?? undefined} />

      <TradeStudySection projectId={activeProjectId ?? undefined} />

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
