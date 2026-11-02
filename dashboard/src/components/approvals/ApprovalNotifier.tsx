import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ShieldCheck, X } from 'lucide-react';

import { useApprovals } from '../../hooks/use-approvals';
import type { ApprovalItem } from '../../types/approvals';

/**
 * FORGE-723: pending approvals announced on every page, not only on /approvals.
 *
 * A gate or a held write waits for a person, and nothing told them unless they
 * happened to open the Approvals page. The card stays until it is dismissed
 * (a 4 s toast is too easy to miss for something that blocks a run) and comes
 * back when an approval arrives that has not been seen yet. Dismissed ids are
 * kept for the browser session, so moving between pages does not re-announce
 * the same items.
 */

const SEEN_KEY = 'metaforge.approvals.seen';
const SHOWN = 3;

const KIND_LABEL: Record<string, string> = {
  gate: 'Gate',
  tool_call: 'Held write',
  flow_proposal: 'Flow proposal',
  flow_version: 'Flow version',
  flow_patch: 'Flow patch',
  human_authority: 'Sign-off',
  design_change: 'Design change',
  design_loop: 'Design loop',
  sketch: 'Sketch',
  drawing: 'Drawing',
};

function readSeen(): Set<string> {
  try {
    const raw = sessionStorage.getItem(SEEN_KEY);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

function writeSeen(ids: Set<string>): void {
  try {
    sessionStorage.setItem(SEEN_KEY, JSON.stringify([...ids]));
  } catch {
    // Storage unavailable (private mode): the card simply re-announces.
  }
}

export function usePendingApprovals(): ApprovalItem[] {
  const { data } = useApprovals({ status: 'pending' });
  return useMemo(() => (data?.items ?? []).filter((i) => i.status === 'pending'), [data]);
}

export function ApprovalNotifier() {
  const pending = usePendingApprovals();
  const { pathname } = useLocation();
  const [seen, setSeen] = useState<Set<string>>(readSeen);

  // Forget ids that are no longer pending, so the stored set stays small.
  useEffect(() => {
    if (!pending.length && !seen.size) return;
    const live = new Set(pending.map((i) => i.id));
    const kept = new Set([...seen].filter((id) => live.has(id)));
    if (kept.size !== seen.size) {
      setSeen(kept);
      writeSeen(kept);
    }
  }, [pending, seen]);

  const unseen = pending.filter((i) => !seen.has(i.id));
  if (pathname.startsWith('/approvals') || unseen.length === 0) return null;

  const acknowledge = () => {
    const next = new Set([...seen, ...pending.map((i) => i.id)]);
    setSeen(next);
    writeSeen(next);
  };
  const total = pending.length;

  return (
    <section className="approval-notice" role="status" aria-live="polite" aria-label="Approvals waiting">
      <header>
        <ShieldCheck size={16} aria-hidden="true" />
        <strong>
          {total} approval{total === 1 ? '' : 's'} waiting for you
        </strong>
        <button type="button" className="icon-control" aria-label="Dismiss approvals notice" onClick={acknowledge}>
          <X size={14} aria-hidden="true" />
        </button>
      </header>
      <ul>
        {unseen.slice(0, SHOWN).map((item) => (
          <li key={item.id}>
            <span className="approval-notice-kind">{KIND_LABEL[item.kind] ?? item.kind}</span>
            <span className="approval-notice-title">{item.title}</span>
          </li>
        ))}
      </ul>
      {unseen.length > SHOWN && <p className="approval-notice-more">and {unseen.length - SHOWN} more</p>}
      <Link to="/approvals" className="text-action" onClick={acknowledge}>
        Review approvals
      </Link>
    </section>
  );
}
