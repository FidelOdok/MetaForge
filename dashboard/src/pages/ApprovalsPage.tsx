import { Link } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';

import { useProposals, useDecideProposal } from '../hooks/use-assistant';
import { usePendingToolApprovals, useDecideToolApproval } from '../hooks/use-tool-approvals';
import { useActiveProject } from '../hooks/use-active-project';
import { useRuns } from '../hooks/use-runs';
import { Button } from '../components/ui/Button';
import { EmptyState } from '../components/ui/EmptyState';
import { formatRelativeTime } from '../utils/format-time';
import type { Proposal } from '../api/endpoints/assistant';
import type { ToolApprovalRun } from '../api/endpoints/toolApprovals';

// ─── Run approval gates (design-flow runs paused at a gate) ──────────────────
function RunGatesQueue() {
  const runs = useRuns();
  const awaiting = (runs.data ?? []).filter((r) => r.status === 'awaiting_approval');

  return (
    <section className="context-panel run-review-queue" aria-labelledby="run-gates-heading">
      <div className="section-heading">
        <h2 id="run-gates-heading">Run approval gates</h2>
        <span className="eyebrow">ALL PROJECTS</span>
      </div>
      {runs.isLoading ? (
        <p className="review-queue-message" role="status">
          Loading run gates…
        </p>
      ) : runs.isError ? (
        <p className="review-queue-message" role="alert">
          Run gates could not be loaded. Check your gateway connection.
        </p>
      ) : awaiting.length ? (
        <div className="review-list">
          {awaiting.map((run) => (
            <Link key={run.id} to={`/runs/${run.id}`}>
              <strong>{String(run.request.goal ?? run.id)}</strong>
              <span>{run.approvalReason ?? 'Inspect the evidence before approving execution.'}</span>
              <span className="text-action">
                Inspect and decide
                <ArrowUpRight size={16} />
              </span>
            </Link>
          ))}
        </div>
      ) : (
        <p className="review-queue-message">No runs awaiting approval.</p>
      )}
    </section>
  );
}

// ─── Kinetic Console design tokens ──────────────────────────────────────────
const KC = {
  surface:          'var(--mf-c-111319)',
  surfaceLow:       'var(--mf-c-191b22)',
  surfaceContainer: 'var(--mf-c-1e1f26)',
  surfaceHigh:      'var(--mf-c-282a30)',
  surfaceHighest:   'var(--mf-c-33343b)',
  surfaceLowest:    'var(--mf-c-0c0e14)',
  onSurface:        'var(--mf-c-e2e2eb)',
  onSurfaceVariant: 'var(--mf-c-9a9aaa)',
  primary:          'var(--mf-c-ffb783)',
  primaryContainer: '#ff5a0a',
  error:            'var(--mf-c-ffb4ab)',
  success:          'var(--mf-c-3dd68c)',
  border:           'var(--mf-r-65-72-90-0p2)',
  glass:            'var(--mf-r-30-31-38-0p85)',
} as const;

// ─── Status dot ─────────────────────────────────────────────────────────────
function StatusDot({ status }: { status: string }) {
  const color =
    status === 'approved' ? KC.success :
    status === 'rejected' ? KC.error :
    KC.onSurfaceVariant;

  return (
    <span
      style={{
        display: 'inline-block',
        width: 6,
        height: 6,
        borderRadius: '50%',
        backgroundColor: color,
        flexShrink: 0,
      }}
    />
  );
}

// ─── Diff panel ─────────────────────────────────────────────────────────────
function DiffPanel({ diff }: { diff: Record<string, unknown> }) {
  const entries = Object.entries(diff);
  if (entries.length === 0) return null;

  return (
    <div
      className="rounded overflow-auto"
      style={{
        backgroundColor: KC.surfaceLowest,
        border: `1px solid ${KC.border}`,
        maxHeight: 160,
      }}
    >
      <div className="font-mono text-xs p-3 space-y-0.5">
        {entries.map(([key, value]) => {
          const raw = typeof value === 'object' ? JSON.stringify(value) : String(value);
          const isAdded   = key.startsWith('+') || key === 'added';
          const isRemoved = key.startsWith('-') || key === 'removed';
          const lineColor = isAdded ? KC.success : isRemoved ? KC.error : KC.onSurfaceVariant;
          const prefix    = isAdded ? '+ ' : isRemoved ? '- ' : '  ';
          return (
            <div key={key} style={{ color: lineColor }}>
              {prefix}{key}: {raw}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── ProposalCard ────────────────────────────────────────────────────────────
function ProposalCard({ proposal }: { proposal: Proposal }) {
  const decide = useDecideProposal();
  const isPending = proposal.status === 'pending';

  function handleDecision(decision: 'approve' | 'reject') {
    decide.mutate({
      changeId: proposal.change_id,
      decision,
      reason: decision === 'approve' ? 'Approved via dashboard' : 'Rejected via dashboard',
      reviewer: 'dashboard-user',
    });
  }

  const hasDiff = Object.keys(proposal.diff).length > 0;

  return (
    <div
      className="rounded-lg space-y-3 p-4"
      style={{
        background: KC.glass,
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        border: `1px solid ${KC.border}`,
      }}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2 min-w-0">
          <StatusDot status={proposal.status} />
          <div className="min-w-0">
            <div
              className="font-medium leading-snug"
              style={{ color: KC.onSurface }}
            >
              {proposal.description}
            </div>
            <div className="mt-1 flex items-center gap-2 flex-wrap">
              <span
                className="font-mono rounded px-1.5 py-0.5"
                style={{
                  fontSize: 12,
                  backgroundColor: KC.surfaceHigh,
                  color: KC.onSurfaceVariant,
                }}
              >
                {proposal.agent_code}
              </span>
              <span
                className="font-mono"
                style={{ fontSize: 12, color: KC.onSurfaceVariant }}
              >
                {formatRelativeTime(proposal.created_at)}
              </span>
            </div>
          </div>
        </div>

        {/* Status label */}
        <span
          className="font-mono shrink-0 rounded px-1.5 py-0.5"
          style={{
            fontSize: 12,
            backgroundColor:
              proposal.status === 'approved' ? 'rgba(61,214,140,0.12)' :
              proposal.status === 'rejected' ? 'rgba(255,180,171,0.12)' :
              KC.surfaceHigh,
            color:
              proposal.status === 'approved' ? KC.success :
              proposal.status === 'rejected' ? KC.error :
              KC.onSurfaceVariant,
          }}
        >
          {proposal.status}
        </span>
      </div>

      {/* Affected work products */}
      {proposal.work_products_affected.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {proposal.work_products_affected.map((wp) => (
            <span
              key={wp}
              className="rounded px-1.5 py-0.5 font-mono"
              style={{
                fontSize: 12,
                backgroundColor: KC.surfaceHigh,
                color: KC.onSurfaceVariant,
                border: `1px solid ${KC.border}`,
              }}
            >
              {wp}
            </span>
          ))}
        </div>
      )}

      {/* Diff panel */}
      {hasDiff && <DiffPanel diff={proposal.diff} />}

      {/* Action buttons — pending only */}
      {isPending && (
        <div className="flex items-center gap-2 pt-1">
          <Button
            variant="primary"
            size="sm"
            onClick={() => handleDecision('approve')}
            disabled={decide.isPending}
            className="gap-1.5"
            style={{
              backgroundColor: KC.primaryContainer,
              color: KC.surface,
              border: 'none',
            }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>check_circle</span>
            Approve
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => handleDecision('reject')}
            disabled={decide.isPending}
            className="gap-1.5"
            style={{
              backgroundColor: 'rgba(255,180,171,0.10)',
              color: KC.error,
              border: `1px solid rgba(255,180,171,0.20)`,
            }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>cancel</span>
            Reject
          </Button>
        </div>
      )}

      {decide.isError && (
        <p role="alert" style={{ color: KC.error }}>
          The decision could not be saved. Check the connection and try again.
        </p>
      )}

      {/* Decision metadata */}
      {proposal.decided_at && (
        <div
          className="font-mono"
          style={{ fontSize: 12, color: KC.onSurfaceVariant }}
        >
          Decided {formatRelativeTime(proposal.decided_at)}
          {proposal.reviewer && ` · ${proposal.reviewer}`}
          {proposal.decision_reason && ` — ${proposal.decision_reason}`}
        </div>
      )}
    </div>
  );
}

// ─── ToolApprovalCard ────────────────────────────────────────────────────────
// FORGE-33: a `requires_approval` tool call (twin.commit_geometry,
// twin.record_decision, project.create/update/delete) paused mid-chat-turn.
// Unlike a Proposal, the list this renders is already filtered to pending —
// there's no history to show, just the tool + a compact arguments preview.
function ToolApprovalCard({ approval }: { approval: ToolApprovalRun }) {
  const decide = useDecideToolApproval();
  const argEntries = Object.entries(approval.request.arguments ?? {});

  function handleDecision(decision: 'approve' | 'reject') {
    decide.mutate({ runId: approval.id, decision });
  }

  return (
    <div
      className="rounded-lg space-y-3 p-4"
      style={{
        background: KC.glass,
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        border: `1px solid ${KC.border}`,
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-2 min-w-0">
          <StatusDot status="pending" />
          <div className="min-w-0">
            <div className="font-mono font-medium leading-snug" style={{ color: KC.onSurface }}>
              {approval.request.tool}
            </div>
            <div className="mt-1">
              <span className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>
                {formatRelativeTime(new Date(approval.created_at * 1000).toISOString())}
              </span>
            </div>
          </div>
        </div>
        <span
          className="font-mono shrink-0 rounded px-1.5 py-0.5"
          style={{ fontSize: 12, backgroundColor: KC.surfaceHigh, color: KC.onSurfaceVariant }}
        >
          awaiting approval
        </span>
      </div>

      {argEntries.length > 0 && (
        <div
          className="rounded overflow-auto"
          style={{
            backgroundColor: KC.surfaceLowest,
            border: `1px solid ${KC.border}`,
            maxHeight: 160,
          }}
        >
          <div className="font-mono text-xs p-3 space-y-0.5">
            {argEntries.map(([key, value]) => {
              const raw = typeof value === 'object' ? JSON.stringify(value) : String(value);
              return (
                <div key={key} style={{ color: KC.onSurfaceVariant }}>
                  {key}: {raw}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <Button
          variant="primary"
          size="sm"
          onClick={() => handleDecision('approve')}
          disabled={decide.isPending}
          className="gap-1.5"
          style={{ backgroundColor: KC.primaryContainer, color: KC.surface, border: 'none' }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>check_circle</span>
          Approve
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => handleDecision('reject')}
          disabled={decide.isPending}
          className="gap-1.5"
          style={{
            backgroundColor: 'rgba(255,180,171,0.10)',
            color: KC.error,
            border: `1px solid rgba(255,180,171,0.20)`,
          }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>cancel</span>
          Reject
        </Button>
      </div>
    </div>
  );
}

// ─── ApprovalsPage ───────────────────────────────────────────────────────────
export function ApprovalsPage() {
  const { activeProjectId } = useActiveProject();
  const { data, isLoading, isError } = useProposals(activeProjectId ?? undefined);
  const { data: toolApprovalsData } = usePendingToolApprovals();
  const pendingToolApprovals = toolApprovalsData?.runs ?? [];

  if (isError) {
    return (
      <div>
        <div className="page-heading">
          <div>
            <p className="eyebrow">HUMAN REVIEW</p>
            <h1>Approvals.</h1>
          </div>
        </div>
        <RunGatesQueue />
        {/* Tool calls come from a separate endpoint; a paused chat turn is
            still actionable even when proposals fail to load. */}
        {pendingToolApprovals.length > 0 && (
          <div className="space-y-3" style={{ marginBottom: 16 }}>
            {pendingToolApprovals.map((approval) => (
              <ToolApprovalCard key={approval.id} approval={approval} />
            ))}
          </div>
        )}
        <div className="workspace-empty" role="alert">
          <h2>Proposals could not be loaded</h2>
          <p>Check your gateway connection and try again.</p>
          <Link className="text-action" to="/settings">
            Connection settings
          </Link>
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="space-y-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="rounded-lg h-24 animate-pulse"
            style={{
              background: KC.glass,
              border: `1px solid ${KC.border}`,
            }}
          />
        ))}
      </div>
    );
  }

  const proposals = data?.proposals ?? [];
  const total = proposals.length;
  const pendingCount  = proposals.filter((p) => p.status === 'pending').length;
  const approvedCount = proposals.filter((p) => p.status === 'approved').length;
  const rejectedCount = proposals.filter((p) => p.status === 'rejected').length;

  const pendingPct  = total > 0 ? (pendingCount  / total) * 100 : 0;
  const approvedPct = total > 0 ? (approvedCount / total) * 100 : 0;
  const rejectedPct = total > 0 ? (rejectedCount / total) * 100 : 0;

  const glassPanel: React.CSSProperties = {
    background: KC.glass,
    backdropFilter: 'blur(16px)',
    WebkitBackdropFilter: 'blur(16px)',
    border: `1px solid ${KC.border}`,
    borderRadius: 4,
  };

  return (
    <div>
      {/* ── Page header ─────────────────────────────────────────────────── */}
      <div className="mb-5 flex items-start justify-between">
        <div>
          <h1
            className="text-lg font-medium leading-tight"
            style={{ color: KC.onSurface }}
          >
            Approvals
          </h1>
          <span
            className="font-mono"
            style={{ fontSize: 12, color: KC.onSurfaceVariant }}
          >
            Human-in-the-loop review · {total} proposal{total !== 1 ? 's' : ''}
          </span>
        </div>
      </div>

      <RunGatesQueue />

      {/* ── 3-column regime cards ────────────────────────────────────────── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr 1fr',
          gap: 12,
          marginBottom: 16,
        }}
      >
        {/* PENDING */}
        <div
          style={{
            ...glassPanel,
            padding: 16,
            borderLeft: '2px solid var(--mf-c-f59e0b)',
          }}
        >
          <div className="mb-2">
            <span
              className="font-mono"
              style={{ fontSize: 12, color: KC.onSurfaceVariant, letterSpacing: '0.06em' }}
            >
              PENDING
            </span>
          </div>
          <div
            className="font-medium"
            style={{ fontSize: 20, color: KC.onSurface, lineHeight: 1.2, marginBottom: 8 }}
          >
            {pendingCount}
            <span
              className="font-mono"
              style={{ fontSize: 12, color: KC.onSurfaceVariant, marginLeft: 6, fontWeight: 400 }}
            >
              proposals
            </span>
          </div>
          <div
            style={{
              height: 4,
              background: 'var(--mf-r-154-154-170-0p15)',
              borderRadius: 2,
              marginBottom: 6,
            }}
          >
            <div
              style={{
                height: '100%',
                width: `${pendingPct}%`,
                background: 'var(--mf-c-f59e0b)',
                borderRadius: 2,
                transition: 'width 0.4s ease',
              }}
            />
          </div>
          <span
            className="font-mono"
            style={{ fontSize: 12, color: KC.onSurfaceVariant }}
          >
            awaiting review
          </span>
        </div>

        {/* APPROVED */}
        <div
          style={{
            ...glassPanel,
            padding: 16,
            borderLeft: `2px solid ${KC.success}`,
          }}
        >
          <div className="mb-2">
            <span
              className="font-mono"
              style={{ fontSize: 12, color: KC.onSurfaceVariant, letterSpacing: '0.06em' }}
            >
              APPROVED
            </span>
          </div>
          <div
            className="font-medium"
            style={{ fontSize: 20, color: KC.onSurface, lineHeight: 1.2, marginBottom: 8 }}
          >
            {approvedCount}
            <span
              className="font-mono"
              style={{ fontSize: 12, color: KC.onSurfaceVariant, marginLeft: 6, fontWeight: 400 }}
            >
              proposals
            </span>
          </div>
          <div
            style={{
              height: 4,
              background: 'var(--mf-r-154-154-170-0p15)',
              borderRadius: 2,
              marginBottom: 6,
            }}
          >
            <div
              style={{
                height: '100%',
                width: `${approvedPct}%`,
                background: KC.success,
                borderRadius: 2,
                transition: 'width 0.4s ease',
              }}
            />
          </div>
          <span
            className="font-mono"
            style={{ fontSize: 12, color: KC.onSurfaceVariant }}
          >
            gate passed
          </span>
        </div>

        {/* REJECTED */}
        <div
          style={{
            ...glassPanel,
            padding: 16,
            borderLeft: `2px solid ${KC.error}`,
          }}
        >
          <div className="mb-2">
            <span
              className="font-mono"
              style={{ fontSize: 12, color: KC.onSurfaceVariant, letterSpacing: '0.06em' }}
            >
              REJECTED
            </span>
          </div>
          <div
            className="font-medium"
            style={{ fontSize: 20, color: KC.onSurface, lineHeight: 1.2, marginBottom: 8 }}
          >
            {rejectedCount}
            <span
              className="font-mono"
              style={{ fontSize: 12, color: KC.onSurfaceVariant, marginLeft: 6, fontWeight: 400 }}
            >
              proposals
            </span>
          </div>
          <div
            style={{
              height: 4,
              background: 'var(--mf-r-154-154-170-0p15)',
              borderRadius: 2,
              marginBottom: 6,
            }}
          >
            <div
              style={{
                height: '100%',
                width: `${rejectedPct}%`,
                background: KC.error,
                borderRadius: 2,
                transition: 'width 0.4s ease',
              }}
            />
          </div>
          <span
            className="font-mono"
            style={{ fontSize: 12, color: KC.onSurfaceVariant }}
          >
            changes required
          </span>
        </div>
      </div>

      {/* ── PENDING TOOL CALLS panel (FORGE-33) ────────────────────────────
          A live chat turn (twin.commit_geometry/record_decision, project
          writes) paused mid-flight, waiting on this exact decision — distinct
          from a Proposal, which nothing downstream is actively blocked on. */}
      <div style={{ marginBottom: 16 }}>
        <div style={glassPanel}>
          <div
            style={{
              padding: '10px 16px',
              borderBottom: `1px solid ${KC.border}`,
            }}
          >
            <span
              className="font-mono"
              style={{ fontSize: 12, color: KC.onSurfaceVariant, letterSpacing: '0.06em' }}
            >
              PENDING TOOL CALLS
            </span>
          </div>
          <div style={{ padding: 12 }}>
            {pendingToolApprovals.length === 0 ? (
              <EmptyState
                title="No tool calls awaiting approval"
                description="A live chat turn paused on twin.commit_geometry, twin.record_decision, or a project write will appear here."
                icon={
                  <span
                    className="material-symbols-outlined"
                    style={{ fontSize: 40, color: KC.onSurfaceVariant }}
                  >
                    pending_actions
                  </span>
                }
              />
            ) : (
              <div className="space-y-3">
                {pendingToolApprovals.map((approval) => (
                  <ToolApprovalCard key={approval.id} approval={approval} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── PROPOSALS panel ──────────────────────────────────────────────── */}
      <div style={{ marginBottom: 16 }}>
        <div style={glassPanel}>
          <div
            style={{
              padding: '10px 16px',
              borderBottom: `1px solid ${KC.border}`,
            }}
          >
            <span
              className="font-mono"
              style={{ fontSize: 12, color: KC.onSurfaceVariant, letterSpacing: '0.06em' }}
            >
              PROPOSALS
            </span>
          </div>
          <div style={{ padding: 12 }}>
            {proposals.length === 0 ? (
              <EmptyState
                title="No pending proposals"
                description="Agent proposals requiring review will appear here."
                icon={
                  <span
                    className="material-symbols-outlined"
                    style={{ fontSize: 40, color: KC.onSurfaceVariant }}
                  >
                    inbox
                  </span>
                }
              />
            ) : (
              <div className="space-y-3">
                {proposals.map((proposal) => (
                  <ProposalCard key={proposal.change_id} proposal={proposal} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
