import { useMemo, useState } from 'react';
import { useImpact } from '../../hooks/use-impact';
import { rerunApprovalId, type ImpactResult, type ProposedRerun } from '../../types/impact';
import type { OperationDomain } from '../../types/operations';
import { InlineApproval } from './InlineApproval';
import { domainLabel } from './labels';

/**
 * What a design change affects across domains (FORGE-793), inside the
 * activity card of its `job:impact-<id>` operation. No permanent panel.
 *
 * Create (UXS-015) gets one plain sentence, "This may affect the PCB fit",
 * and a single next action. Engineering (UI-008) can expand the exact detail:
 * the affected items by domain with the rationale, the results now out of
 * date with the revision they were for, and the proposed re-checks, each
 * reviewed through the existing approval card.
 */
export function ImpactPanel({ projectId, impactId, detail }: { projectId: string; impactId: string; detail: boolean }) {
  const impact = useImpact(projectId, impactId);
  if (!impact.data) {
    return impact.isError ? <p className="act-muted">The impact of this change could not be loaded.</p> : null;
  }
  return detail ? <ImpactDetail impact={impact.data} /> : <ImpactSummary impact={impact.data} />;
}

function waitingReruns(impact: ImpactResult): ProposedRerun[] {
  return impact.proposed_reruns.filter((r) => r.status === 'proposed' && r.disposition === 'requires_approval');
}

/** Create: one sentence and one next action. */
export function ImpactSummary({ impact }: { impact: ImpactResult }) {
  const [open, setOpen] = useState(false);
  const first = waitingReruns(impact)[0];
  return (
    <div className="act-impact" data-testid="impact-summary">
      <p className="act-impact-line">{impact.summary}</p>
      {first && impact.id ? (
        <>
          <button type="button" className="act-btn is-primary" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {impact.next_action.replace(/\.$/, '')}
          </button>
          {open && <InlineApproval id={rerunApprovalId(impact.id, first.id)} />}
        </>
      ) : (
        <p className="act-muted act-impact-next">{impact.next_action}</p>
      )}
    </div>
  );
}

const STATUS_WORDS: Record<ProposedRerun['status'], string> = {
  proposed: 'waiting for your decision',
  queued: 'can run without a decision',
  approved: 'approved',
  rejected: 'rejected',
};

/** Engineering: affected entities, stale results and proposed re-checks. */
export function ImpactDetail({ impact }: { impact: ImpactResult }) {
  const [openApproval, setOpenApproval] = useState<string | null>(null);
  const domains = useMemo(() => {
    const by = new Map<string, ImpactResult['impacted']>();
    for (const entity of impact.impacted) by.set(entity.domain, [...(by.get(entity.domain) ?? []), entity]);
    return [...by.entries()];
  }, [impact.impacted]);
  const change = `${impact.change.item_key}@${impact.change.old_revision ?? '?'} to @${impact.change.new_revision ?? '?'}`;

  return (
    <div className="act-impact is-detail" data-testid="impact-detail">
      <p className="act-impact-line">{impact.summary}</p>
      <details className="act-detail">
        <summary>
          Impact of {change}: {impact.impacted.length} affected, {impact.stale_results.length} out of date
        </summary>

        {domains.length > 0 && (
          <section className="act-section" aria-label="Affected by domain">
            <h3 className="act-label">Affected</h3>
            {domains.map(([domain, entities]) => (
              <div key={domain} className="act-impact-domain" data-domain={domain}>
                <h4>{domainLabel(domain as OperationDomain, true)}</h4>
                <ul>
                  {entities.map((e) => (
                    <li key={e.ref} data-ref={e.ref}>
                      <code>{e.revision ? `${e.ref}@${e.revision}` : e.ref}</code> {e.label}
                      {!e.direct && <span className="act-muted"> (indirect, depth {e.depth})</span>}
                      {!e.certain && <span className="act-badge">suggested link, not confirmed</span>}
                      <ul className="act-impact-why">
                        {e.couplings.map((c) => (
                          <li key={c.edge_id}>
                            <span className="act-muted">{c.edge_type}</span> {c.rationale}
                          </li>
                        ))}
                      </ul>
                      {e.modules.length > 0 && <p className="act-muted">Files: {e.modules.join(', ')}</p>}
                      <p className="act-muted">Next: {e.proposed_action}</p>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </section>
        )}

        {impact.stale_results.length > 0 && (
          <section className="act-section" aria-label="Results out of date">
            <h3 className="act-label">Out of date (kept as history)</h3>
            <ul>
              {impact.stale_results.map((s) => (
                <li key={s.record_id} data-record={s.record_id}>
                  {s.name} <span className="act-muted">({s.check_kind})</span>: was {s.previous_status}, now{' '}
                  <strong>{s.status}</strong>. It was for <code>{s.pinned_ref}</code>
                  {s.current_ref ? (
                    <>
                      , now <code>{s.current_ref}</code>
                    </>
                  ) : null}
                  .
                </li>
              ))}
            </ul>
          </section>
        )}

        {impact.proposed_reruns.length > 0 && (
          <section className="act-section" aria-label="Proposed re-checks">
            <h3 className="act-label">Proposed re-checks</h3>
            <ul>
              {impact.proposed_reruns.map((r) => {
                const approvalId = impact.id ? rerunApprovalId(impact.id, r.id) : null;
                const waiting = r.status === 'proposed' && r.disposition === 'requires_approval';
                return (
                  <li key={r.id} data-rerun={r.id} data-status={r.status}>
                    {r.label} <span className="act-muted">({STATUS_WORDS[r.status]})</span>
                    {r.disposition_reason && <p className="act-muted">{r.disposition_reason}</p>}
                    {waiting && approvalId && (
                      <button
                        type="button"
                        className="act-btn is-primary"
                        aria-expanded={openApproval === approvalId}
                        onClick={() => setOpenApproval((cur) => (cur === approvalId ? null : approvalId))}
                      >
                        Approve or reject
                      </button>
                    )}
                    {openApproval === approvalId && approvalId && <InlineApproval id={approvalId} />}
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        {impact.not_affected.length > 0 && (
          <section className="act-section" aria-label="Not affected">
            <h3 className="act-label">Not affected</h3>
            <ul>
              {impact.not_affected.map((n) => (
                <li key={n.ref}>
                  <code>{n.ref}</code> <span className="act-muted">{n.reason}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        {(impact.cycles.length > 0 || impact.depth_limited) && (
          <p className="act-muted">
            {impact.cycles.length > 0 && `Cycle found: ${impact.cycles.map((c) => c.join(' > ')).join('; ')}. `}
            {impact.depth_limited && `The walk stopped at depth ${impact.max_depth}.`}
          </p>
        )}
      </details>
    </div>
  );
}
