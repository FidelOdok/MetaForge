import { useState } from 'react';
import { EmptyState } from '../components/ui/EmptyState';
import { Badge } from '../components/ui/Badge';
import { useToast } from '../components/ui/Toast';
import { useActiveProject } from '../hooks/use-active-project';
import { useRequirementQuality, useProposeRequirementFix } from '../hooks/use-requirements';
import type { PassFail, RequirementRecord } from '../types/requirements';

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
