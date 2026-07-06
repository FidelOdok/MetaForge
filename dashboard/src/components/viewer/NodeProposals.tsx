import { useQueryClient } from '@tanstack/react-query';
import { Check, X, GitPullRequest } from 'lucide-react';
import { useProposals, useDecideProposal, assistantKeys } from '../../hooks/use-assistant';
import { twinKeys } from '../../hooks/use-twin';
import type { Proposal } from '../../api/endpoints/assistant';

interface NodeProposalsProps {
  /** Twin node id — only proposals affecting this node are shown. */
  nodeId?: string;
  /** Fired after a proposal is approved (apply ran) — e.g. reload the model. */
  onApplied?: () => void;
}

/**
 * In-twin proposal review (MET-548, Phase 3c).
 *
 * Surfaces pending design-change proposals that target the open node right
 * inside the twin panel — the designer approves/rejects without leaving the
 * viewer. Approving runs the gated apply path server-side (propose → approve →
 * apply); on success we invalidate the twin queries so the node refreshes and
 * the caller can reload the 3D model.
 */
export function NodeProposals({ nodeId, onApplied }: NodeProposalsProps) {
  const { data } = useProposals();
  const decide = useDecideProposal();
  const queryClient = useQueryClient();

  const pending: Proposal[] = (data?.proposals ?? []).filter(
    (p) => p.status === 'pending' && (!nodeId || p.work_products_affected.includes(nodeId)),
  );

  if (pending.length === 0) return null;

  const act = (changeId: string, decision: 'approve' | 'reject') => {
    decide.mutate(
      {
        changeId,
        decision,
        reason: decision === 'approve' ? 'Approved from twin viewer' : 'Rejected from twin viewer',
        reviewer: 'twin-user',
      },
      {
        onSuccess: () => {
          // Refresh proposals (drop the decided one) and the twin (apply may
          // have mutated the node / its geometry).
          void queryClient.invalidateQueries({ queryKey: assistantKeys.all });
          void queryClient.invalidateQueries({ queryKey: twinKeys.all });
          if (decision === 'approve') onApplied?.();
        },
      },
    );
  };

  return (
    <div className="border-b border-zinc-200 p-4 dark:border-zinc-700">
      <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-amber-600 dark:text-amber-400">
        <GitPullRequest size={12} />
        Pending changes ({pending.length})
      </h4>
      <ul className="space-y-2">
        {pending.map((p) => {
          const action = typeof p.diff?.action === 'string' ? (p.diff.action as string) : null;
          return (
            <li
              key={p.change_id}
              className="rounded-md border border-amber-300/60 bg-amber-50/60 p-2.5 dark:border-amber-500/30 dark:bg-amber-500/10"
            >
              <p className="text-xs font-medium text-zinc-900 dark:text-zinc-100">
                {p.description}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-zinc-500">
                <span className="rounded bg-zinc-200 px-1.5 py-0.5 font-mono dark:bg-zinc-700">
                  {p.agent_code}
                </span>
                {action && (
                  <span className="rounded bg-zinc-200 px-1.5 py-0.5 font-mono dark:bg-zinc-700">
                    {action}
                  </span>
                )}
              </div>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  onClick={() => act(p.change_id, 'approve')}
                  disabled={decide.isPending}
                  className="flex flex-1 items-center justify-center gap-1 rounded bg-emerald-600 px-2 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  <Check size={12} />
                  Approve
                </button>
                <button
                  type="button"
                  onClick={() => act(p.change_id, 'reject')}
                  disabled={decide.isPending}
                  className="flex flex-1 items-center justify-center gap-1 rounded border border-zinc-300 px-2 py-1 text-xs font-medium text-zinc-600 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-800"
                >
                  <X size={12} />
                  Reject
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
