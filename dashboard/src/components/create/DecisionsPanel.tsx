import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronDown, ExternalLink } from 'lucide-react';
import { useApprovals, useDecideApproval } from '../../hooks/use-approvals';
import { refusalOf } from '../../api/endpoints/create';
import type { ApprovalDecision, ApprovalItem } from '../../types/approvals';
import { COMMITMENT_COPY, commitmentOf } from './copy';

const DECISION_LABEL: Record<ApprovalDecision, string> = {
  approve: 'Approve',
  reject: 'Reject',
  retry: 'Try again',
  rework: 'Revise',
};

interface DecisionsPanelProps {
  projectId: string;
}

/**
 * The decisions waiting on this creation, in plain words, each labelled with
 * the kind of commitment it is. The actions are exactly the ones the gateway
 * allows for that item; the full evidence stays one click away.
 */
export function DecisionsPanel({ projectId }: DecisionsPanelProps) {
  const pending = useApprovals({ status: 'pending', projectId });
  const items = pending.data?.items ?? [];

  if (pending.isLoading) return <p className="cv-muted">Loading decisions…</p>;
  if (pending.isError) return <p className="cv-error">Decisions could not be loaded: {refusalOf(pending.error)}</p>;
  if (!items.length) {
    return (
      <div className="cv-panel-body">
        <p className="cv-muted">Nothing is waiting for you.</p>
        <p className="cv-fineprint">
          Changes, spending, sharing files and machine actions each ask here first. Releasing a build and accepting it
          are on the Make and Inspect tabs.
        </p>
      </div>
    );
  }
  return (
    <div className="cv-panel-body">
      {items.map((item) => (
        <DecisionCard key={item.id} item={item} />
      ))}
    </div>
  );
}

function DecisionCard({ item }: { item: ApprovalItem }) {
  const decide = useDecideApproval();
  const [reason, setReason] = useState('');
  const [open, setOpen] = useState(false);
  const commitment = commitmentOf(item.kind, item.detail);
  const needsReason = (d: ApprovalDecision) => item.reason_required_for.includes(d);

  return (
    <article className={`cv-card cv-decision is-${commitment}`}>
      <p className="cv-eyebrow">{COMMITMENT_COPY[commitment]}</p>
      <h4>{item.title}</h4>
      {item.summary && <p className="cv-muted">{item.summary}</p>}
      {item.findings.length > 0 && (
        <button type="button" className="cv-link" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <ChevronDown size={13} className={open ? 'cv-rot' : ''} /> {item.findings.length} finding
          {item.findings.length === 1 ? '' : 's'}
        </button>
      )}
      {open && (
        <ul className="cv-findings">
          {item.findings.map((f, i) => (
            <li key={i} className={`is-${f.severity}`}>
              {f.message}
            </li>
          ))}
        </ul>
      )}
      {item.decidable ? (
        <>
          {item.allowed_decisions.some(needsReason) && (
            <input
              className="cv-input"
              aria-label="Reason"
              placeholder="Reason (needed to reject or revise)"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          )}
          <div className="cv-actions">
            {item.allowed_decisions.map((d) => (
              <button
                key={d}
                type="button"
                className={d === 'approve' ? 'cv-btn cv-btn-primary' : 'cv-btn'}
                disabled={decide.isPending || (needsReason(d) && !reason.trim())}
                onClick={() =>
                  decide.mutate({
                    id: item.id,
                    body: {
                      decision: d,
                      ...(reason.trim() ? { reason: reason.trim() } : {}),
                      // RT-030: a proposal that changed since it was shown is refused.
                      ...(item.base_revision ? { base_revision: item.base_revision } : {}),
                    },
                  })
                }
              >
                {DECISION_LABEL[d]}
              </button>
            ))}
          </div>
        </>
      ) : (
        <p className="cv-fineprint">{item.not_decidable_reason ?? 'This one cannot be decided here.'}</p>
      )}
      {decide.isError && <p className="cv-error">{refusalOf(decide.error)}</p>}
      <Link className="cv-link" to="/approvals">
        Full evidence in Engineering <ExternalLink size={12} />
      </Link>
    </article>
  );
}
