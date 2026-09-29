import { useRelatedDecisions } from '../../hooks/use-decisions';
import type { Decision } from '../../types/decision';

/** FORGE-289 (gap G-G3): "Decision cards linked from hierarchy nodes and
 * iterations" -- one shared component for both call sites (StructureView's
 * selected hierarchy node, and DesignLoopSection's converged winner), since
 * both are the same question: "what decisions touch this node." */

function DecisionCard({ decision }: { decision: Decision }) {
  return (
    <div
      data-testid="decision-card"
      className="rounded-md p-3 flex flex-col gap-1"
      style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
    >
      <span className="text-sm font-medium text-on-surface">{decision.title}</span>
      <p className="font-mono text-xs text-on-surface-variant line-clamp-2">{decision.rationale}</p>
      {decision.alternatives.length > 0 && (
        <span
          className="font-mono text-[10px] uppercase tracking-widest text-on-surface-variant"
          title={decision.alternatives.map((a) => `${a.option}: ${a.reason_rejected}`).join('\n')}
        >
          {decision.alternatives.length} alternative{decision.alternatives.length === 1 ? '' : 's'}{' '}
          considered
        </span>
      )}
      {decision.evidence_refs.length > 0 && (
        <span className="font-mono text-[10px] uppercase tracking-widest" style={{ color: 'var(--mf-c-3dd68c, #3dd68c)' }}>
          Supported by {decision.evidence_refs.length} evidence record
          {decision.evidence_refs.length === 1 ? '' : 's'}
        </span>
      )}
    </div>
  );
}

export function DecisionList({ nodeId, heading }: { nodeId?: string; heading?: string }) {
  const { data: decisions, isLoading } = useRelatedDecisions(nodeId);

  if (!nodeId || isLoading) return null;
  if (!decisions || decisions.length === 0) return null;

  return (
    <div data-testid="decision-list" className="flex flex-col gap-2">
      {heading && (
        <span className="font-mono text-[10px] uppercase tracking-widest text-on-surface-variant">
          {heading}
        </span>
      )}
      {decisions.map((decision) => (
        <DecisionCard key={decision.id} decision={decision} />
      ))}
    </div>
  );
}
