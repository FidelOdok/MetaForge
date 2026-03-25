import { useState } from 'react';
import { useProposals, useDecideProposal } from '../hooks/use-assistant';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { SkeletonCard } from '../components/ui/Skeleton';
import { useToast } from '../components/ui/Toast';
import { formatRelativeTime } from '../utils/format-time';
import { useScopedChat } from '../hooks/use-scoped-chat';
import { ApprovalChatPanel } from '../components/chat/integrations/ApprovalChatPanel';
import type { Proposal } from '../api/endpoints/assistant';

// ── Domain color mapping ──────────────────────────────────────────────────────

type BadgeVariant = 'default' | 'success' | 'warning' | 'error' | 'info';

function domainVariant(workProduct: string): BadgeVariant {
  const lower = workProduct.toLowerCase();
  if (lower.includes('mechanical') || lower.includes('cad') || lower.includes('stress')) return 'info';
  if (lower.includes('electronics') || lower.includes('schematic') || lower.includes('pcb') || lower.includes('erc') || lower.includes('drc')) return 'success';
  if (lower.includes('firmware') || lower.includes('software') || lower.includes('code')) return 'warning';
  return 'default';
}

function riskLevel(count: number): { label: string; variant: BadgeVariant } {
  if (count >= 3) return { label: 'High Risk', variant: 'error' };
  if (count === 2) return { label: 'Medium Risk', variant: 'warning' };
  return { label: 'Low Risk', variant: 'success' };
}

// ── Diff panel ────────────────────────────────────────────────────────────────

function DiffPanel({ diff, description }: { diff: Record<string, unknown>; description: string }) {
  const entries = Object.entries(diff);

  if (entries.length === 0) {
    return (
      <div className="rounded-md bg-zinc-50 p-3 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
        <p className="font-medium text-zinc-700 dark:text-zinc-300">Change description</p>
        <p className="mt-1">{description}</p>
      </div>
    );
  }

  // Try to interpret diff entries as before/after pairs.
  // Expected shape: { field: { before: ..., after: ... } } or { field: [before, after] }
  const rows: { field: string; before: string; after: string }[] = entries.map(([field, value]) => {
    if (
      value !== null &&
      typeof value === 'object' &&
      !Array.isArray(value) &&
      'before' in value &&
      'after' in value
    ) {
      const typed = value as { before: unknown; after: unknown };
      return {
        field,
        before: String(typed.before ?? '—'),
        after: String(typed.after ?? '—'),
      };
    }
    if (Array.isArray(value) && value.length >= 2) {
      return { field, before: String(value[0] ?? '—'), after: String(value[1] ?? '—') };
    }
    return { field, before: '—', after: String(value ?? '—') };
  });

  return (
    <div className="overflow-x-auto rounded-md border border-zinc-200 dark:border-zinc-700">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-zinc-50 dark:bg-zinc-900">
            <th className="px-3 py-1.5 text-left font-medium text-zinc-500 dark:text-zinc-400">Field</th>
            <th className="px-3 py-1.5 text-left font-medium text-red-600 dark:text-red-400">Before</th>
            <th className="px-3 py-1.5 text-left font-medium text-green-600 dark:text-green-400">After</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.field}
              className="border-t border-zinc-100 dark:border-zinc-800"
            >
              <td className="px-3 py-1.5 font-medium text-zinc-700 dark:text-zinc-300">{row.field}</td>
              <td className="px-3 py-1.5 font-mono text-red-700 dark:text-red-400">{row.before}</td>
              <td className="px-3 py-1.5 font-mono text-green-700 dark:text-green-400">{row.after}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── ProposalCard ──────────────────────────────────────────────────────────────

interface ProposalCardProps {
  proposal: Proposal;
  selected: boolean;
  onToggleSelect: (id: string) => void;
}

function ProposalCard({ proposal, selected, onToggleSelect }: ProposalCardProps) {
  const decide = useDecideProposal();
  const toast = useToast();
  const [chatOpen, setChatOpen] = useState(false);
  const [diffOpen, setDiffOpen] = useState(false);
  const [confirmState, setConfirmState] = useState<'idle' | 'confirm-approve' | 'confirm-reject'>('idle');
  const isPending = proposal.status === 'pending';

  const chat = useScopedChat({
    scopeKind: 'approval',
    entityId: proposal.change_id,
    defaultAgentCode: proposal.agent_code,
  });

  function handleDecision(decision: 'approve' | 'reject') {
    decide.mutate(
      {
        changeId: proposal.change_id,
        decision,
        reason: decision === 'approve' ? 'Approved via dashboard' : 'Rejected via dashboard',
        reviewer: 'dashboard-user',
      },
      {
        onSuccess: () => {
          toast.success(decision === 'approve' ? 'Proposal approved.' : 'Proposal rejected.');
          setConfirmState('idle');
        },
        onError: (err) => {
          toast.error((err as Error)?.message ?? 'Failed to process decision.');
          setConfirmState('idle');
        },
      },
    );
  }

  const risk = riskLevel(proposal.work_products_affected.length);
  const hasDiff = Object.keys(proposal.diff).length > 0;

  return (
    <Card className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        {/* Checkbox for bulk selection */}
        {isPending && (
          <input
            type="checkbox"
            aria-label={`Select proposal ${proposal.change_id}`}
            checked={selected}
            onChange={() => onToggleSelect(proposal.change_id)}
            className="mt-1 h-4 w-4 flex-shrink-0 cursor-pointer rounded border-zinc-300 accent-blue-600"
          />
        )}

        <div className="flex-1">
          <div className="font-medium text-zinc-900 dark:text-zinc-100">
            {proposal.description}
          </div>
          <div className="mt-1 text-xs text-zinc-400">
            Agent: <span className="font-medium">{proposal.agent_code}</span>
            {' \u00B7 '}
            Created {formatRelativeTime(proposal.created_at)}
          </div>
        </div>
        <StatusBadge status={proposal.status} />
      </div>

      {/* Impact summary: work product badges + risk badge */}
      {proposal.work_products_affected.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {proposal.work_products_affected.map((wp) => (
            <Badge key={wp} variant={domainVariant(wp)}>
              {wp}
            </Badge>
          ))}
          <Badge variant={risk.variant}>{risk.label}</Badge>
        </div>
      )}

      {/* View Changes toggle */}
      <div>
        <button
          type="button"
          onClick={() => setDiffOpen((prev) => !prev)}
          className="flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300"
        >
          <span>{diffOpen ? '▾' : '▸'}</span>
          {diffOpen ? 'Hide Changes' : 'View Changes'}
          {hasDiff && (
            <span className="ml-1 rounded bg-blue-100 px-1 py-0.5 text-[10px] text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">
              {Object.keys(proposal.diff).length} field{Object.keys(proposal.diff).length !== 1 ? 's' : ''}
            </span>
          )}
        </button>

        {diffOpen && (
          <div className="mt-2">
            <DiffPanel diff={proposal.diff} description={proposal.description} />
          </div>
        )}
      </div>

      {/* Inline confirm / action buttons */}
      {isPending && (
        <>
          {confirmState === 'idle' && (
            <div className="flex gap-2 pt-1">
              <Button
                variant="primary"
                size="sm"
                onClick={() => setConfirmState('confirm-approve')}
                disabled={decide.isPending}
              >
                Approve
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={() => setConfirmState('confirm-reject')}
                disabled={decide.isPending}
              >
                Reject
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setChatOpen(!chatOpen)}
              >
                {chatOpen ? 'Hide Chat' : 'Discuss'}
              </Button>
            </div>
          )}

          {confirmState === 'confirm-approve' && (
            <div className="flex items-center gap-2 rounded-md bg-green-50 px-3 py-2 dark:bg-green-900/20">
              <span className="text-xs font-medium text-green-800 dark:text-green-300">
                Confirm approve?
              </span>
              <Button
                variant="primary"
                size="sm"
                onClick={() => handleDecision('approve')}
                disabled={decide.isPending}
              >
                Yes, approve
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmState('idle')}
                disabled={decide.isPending}
              >
                Cancel
              </Button>
            </div>
          )}

          {confirmState === 'confirm-reject' && (
            <div className="flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 dark:bg-red-900/20">
              <span className="text-xs font-medium text-red-800 dark:text-red-300">
                Confirm reject?
              </span>
              <Button
                variant="danger"
                size="sm"
                onClick={() => handleDecision('reject')}
                disabled={decide.isPending}
              >
                Yes, reject
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmState('idle')}
                disabled={decide.isPending}
              >
                Cancel
              </Button>
            </div>
          )}
        </>
      )}

      {proposal.decided_at && (
        <div className="text-xs text-zinc-400">
          Decided {formatRelativeTime(proposal.decided_at)}
          {proposal.reviewer && ` by ${proposal.reviewer}`}
          {proposal.decision_reason && ` \u2014 ${proposal.decision_reason}`}
        </div>
      )}

      {chatOpen && (
        <div className="mt-2">
          <ApprovalChatPanel
            approvalId={proposal.change_id}
            agentCode={proposal.agent_code}
            thread={chat.thread}
            messages={chat.messages}
            isTyping={chat.isTyping}
            onSendMessage={chat.sendMessage}
            onCreateThread={chat.createThread}
          />
        </div>
      )}
    </Card>
  );
}

// ── Tab types ─────────────────────────────────────────────────────────────────

type StatusTab = 'pending' | 'approved' | 'rejected';

const TAB_LABELS: Record<StatusTab, string> = {
  pending: 'Pending',
  approved: 'Approved',
  rejected: 'Rejected',
};

// ── ApprovalsPage ─────────────────────────────────────────────────────────────

export function ApprovalsPage() {
  const { data, isLoading, isError, refetch } = useProposals();
  const decide = useDecideProposal();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<StatusTab>('pending');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkPending, setBulkPending] = useState(false);

  function toggleSelect(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  async function handleBulkDecision(decision: 'approve' | 'reject') {
    const ids = Array.from(selectedIds);
    setBulkPending(true);

    let successCount = 0;
    let failCount = 0;

    for (const changeId of ids) {
      await new Promise<void>((resolve) => {
        decide.mutate(
          {
            changeId,
            decision,
            reason: decision === 'approve' ? 'Bulk approved via dashboard' : 'Bulk rejected via dashboard',
            reviewer: 'dashboard-user',
          },
          {
            onSuccess: () => {
              successCount++;
              resolve();
            },
            onError: () => {
              failCount++;
              resolve();
            },
          },
        );
      });
    }

    setBulkPending(false);
    setSelectedIds(new Set());

    if (failCount === 0) {
      toast.success(
        `${successCount} proposal${successCount !== 1 ? 's' : ''} ${decision === 'approve' ? 'approved' : 'rejected'}.`
      );
    } else {
      toast.warning(
        `${successCount} succeeded, ${failCount} failed.`
      );
    }
  }

  if (isLoading) {
    return (
      <div data-testid="loading-skeleton">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
            Approvals
          </h2>
        </div>
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div>
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
            Approvals
          </h2>
        </div>
        <Card className="flex flex-col items-center py-12 text-center">
          <p className="text-base font-medium text-red-600 dark:text-red-400">
            Failed to load proposals
          </p>
          <p className="mt-1 text-sm text-zinc-500">
            There was a problem fetching pending approvals.
          </p>
          <Button variant="secondary" className="mt-4" onClick={() => void refetch()}>
            Retry
          </Button>
        </Card>
      </div>
    );
  }

  const allProposals = data?.proposals ?? [];
  const filtered = allProposals.filter((p) => p.status === activeTab);

  const tabCounts: Record<StatusTab, number> = {
    pending: allProposals.filter((p) => p.status === 'pending').length,
    approved: allProposals.filter((p) => p.status === 'approved').length,
    rejected: allProposals.filter((p) => p.status === 'rejected').length,
  };

  // Only show bulk action bar for pending proposals with 2+ selected
  const pendingSelected = Array.from(selectedIds).filter((id) =>
    allProposals.find((p) => p.change_id === id && p.status === 'pending')
  );
  const showBulkBar = pendingSelected.length >= 2;

  const emptyMessages: Record<StatusTab, { title: string; description: string }> = {
    pending: {
      title: 'No pending approvals',
      description: 'Agent proposals requiring review will appear here.',
    },
    approved: {
      title: 'No approved proposals',
      description: 'Proposals that have been approved will appear here.',
    },
    rejected: {
      title: 'No rejected proposals',
      description: 'Proposals that have been rejected will appear here.',
    },
  };

  return (
    <div>
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
          Approvals
        </h2>
        <span className="text-sm text-zinc-500">{allProposals.length} total</span>
      </div>

      {/* Status filter tabs */}
      <div className="mb-4 flex gap-1 border-b border-zinc-200 dark:border-zinc-700">
        {(Object.keys(TAB_LABELS) as StatusTab[]).map((tab) => (
          <button
            key={tab}
            type="button"
            onClick={() => {
              setActiveTab(tab);
              setSelectedIds(new Set());
            }}
            className={[
              'flex items-center gap-1.5 px-4 py-2 text-sm font-medium transition-colors',
              activeTab === tab
                ? 'border-b-2 border-blue-600 text-blue-600 dark:text-blue-400'
                : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200',
            ].join(' ')}
          >
            {TAB_LABELS[tab]}
            {tabCounts[tab] > 0 && (
              <span
                className={[
                  'rounded-full px-1.5 py-0.5 text-[10px] font-semibold',
                  activeTab === tab
                    ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
                    : 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
                ].join(' ')}
              >
                {tabCounts[tab]}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Bulk action bar */}
      {showBulkBar && (
        <div
          data-testid="bulk-action-bar"
          className="mb-4 flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2.5 dark:border-blue-800 dark:bg-blue-900/20"
        >
          <span className="flex-1 text-sm font-medium text-blue-800 dark:text-blue-300">
            {pendingSelected.length} selected
          </span>
          <Button
            variant="primary"
            size="sm"
            disabled={bulkPending}
            onClick={() => void handleBulkDecision('approve')}
          >
            Approve selected ({pendingSelected.length})
          </Button>
          <Button
            variant="danger"
            size="sm"
            disabled={bulkPending}
            onClick={() => void handleBulkDecision('reject')}
          >
            Reject selected ({pendingSelected.length})
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={bulkPending}
            onClick={() => setSelectedIds(new Set())}
          >
            Clear
          </Button>
        </div>
      )}

      {/* Proposal list or empty state */}
      {filtered.length === 0 ? (
        <EmptyState
          title={emptyMessages[activeTab].title}
          description={emptyMessages[activeTab].description}
        />
      ) : (
        <div className="space-y-3">
          {filtered.map((proposal) => (
            <ProposalCard
              key={proposal.change_id}
              proposal={proposal}
              selected={selectedIds.has(proposal.change_id)}
              onToggleSelect={toggleSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}
