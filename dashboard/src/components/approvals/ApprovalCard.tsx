import { useState } from 'react';

import { Button } from '../ui/Button';
import { useDecideApproval } from '../../hooks/use-approvals';
import { formatRelativeTime } from '../../utils/format-time';
import type {
  ApprovalDecision,
  ApprovalDecisionRecord,
  ApprovalFinding,
  ApprovalItem,
} from '../../types/approvals';

// Kinetic Console tokens (same set the old Approvals cards used).
const KC = {
  surfaceHigh: 'var(--mf-c-282a30)',
  surfaceLowest: 'var(--mf-c-0c0e14)',
  onSurface: 'var(--mf-c-e2e2eb)',
  onSurfaceVariant: 'var(--mf-c-9a9aaa)',
  primaryContainer: '#ff5a0a',
  surface: 'var(--mf-c-111319)',
  error: 'var(--mf-c-ffb4ab)',
  success: 'var(--mf-c-3dd68c)',
  warning: 'var(--mf-c-f59e0b)',
  border: 'var(--mf-r-65-72-90-0p2)',
  glass: 'var(--mf-r-30-31-38-0p85)',
} as const;

const KIND_LABELS: Record<ApprovalItem['kind'], string> = {
  gate: 'Gate',
  flow_proposal: 'Flow proposal',
  flow_version: 'Flow version',
  flow_patch: 'Flow patch',
  tool_call: 'Tool call',
  human_authority: 'Human authority',
  design_change: 'Design change',
  design_loop: 'Design loop',
  sketch: 'Sketch',
  drawing: 'Drawing',
};

const DECISION_LABELS: Record<ApprovalDecision, string> = {
  approve: 'Approve',
  reject: 'Reject',
  retry: 'Retry',
  rework: 'Rework',
};

const DECISION_ICONS: Record<ApprovalDecision, string> = {
  approve: 'check_circle',
  reject: 'cancel',
  retry: 'refresh',
  rework: 'undo',
};

const SEVERITY_COLOR: Record<ApprovalFinding['severity'], string> = {
  error: KC.error,
  warning: KC.warning,
  info: KC.onSurfaceVariant,
};

const DECISION_ORDER: ApprovalDecision[] = ['approve', 'reject', 'retry', 'rework'];

function Chip({ children, color }: { children: React.ReactNode; color?: string }) {
  return (
    <span
      className="font-mono rounded px-1.5 py-0.5"
      style={{ fontSize: 12, backgroundColor: KC.surfaceHigh, color: color ?? KC.onSurfaceVariant }}
    >
      {children}
    </span>
  );
}

function CodeBox({ lines }: { lines: { text: string; color?: string }[] }) {
  if (lines.length === 0) return null;
  return (
    <div
      className="rounded overflow-auto"
      style={{ backgroundColor: KC.surfaceLowest, border: `1px solid ${KC.border}`, maxHeight: 160 }}
    >
      <div className="font-mono text-xs p-3 space-y-0.5">
        {lines.map((l, i) => (
          <div key={i} style={{ color: l.color ?? KC.onSurfaceVariant }}>
            {l.text}
          </div>
        ))}
      </div>
    </div>
  );
}

function show(value: unknown): string {
  return typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value);
}

function entries(obj: unknown): [string, unknown][] {
  return obj && typeof obj === 'object' && !Array.isArray(obj)
    ? Object.entries(obj as Record<string, unknown>)
    : [];
}

/** The part of the card that depends on `kind`. */
function KindDetail({ item }: { item: ApprovalItem }) {
  const d = item.detail ?? {};
  switch (item.kind) {
    case 'tool_call':
    case 'human_authority': {
      const args = entries(d.arguments);
      return (
        <div className="space-y-1.5" data-testid="approval-detail">
          <div className="font-mono" style={{ color: KC.onSurface }}>
            {String(d.tool ?? '')}
          </div>
          <CodeBox lines={args.map(([k, v]) => ({ text: `${k}: ${show(v)}` }))} />
        </div>
      );
    }
    case 'gate':
      return (
        <div className="flex flex-wrap gap-1.5" data-testid="approval-detail">
          <Chip>run {String(d.run_id ?? '')}</Chip>
          <Chip>phase {String(d.phase ?? '')}</Chip>
          {d.gate != null && <Chip>gate {String(d.gate)}</Chip>}
          <Chip>attempt {String(d.attempt ?? 0)}</Chip>
          <Chip>{String(d.retries_left ?? 0)} retries left</Chip>
          <Chip>{String(d.rework_cycles_left ?? 0)} rework cycles left</Chip>
        </div>
      );
    case 'flow_proposal':
    case 'flow_version': {
      const changes = Array.isArray(d.changes) ? d.changes : [];
      return (
        <div className="space-y-1.5" data-testid="approval-detail">
          {d.intent != null && (
            <p style={{ color: KC.onSurfaceVariant, margin: 0 }}>{String(d.intent)}</p>
          )}
          <CodeBox lines={changes.map((c) => ({ text: show(c) }))} />
        </div>
      );
    }
    case 'flow_patch': {
      // FORGE-539: a change to a running flow. What it re-runs and what it
      // keeps is the decision, so both are shown, not just the diff.
      const rerun = Array.isArray(d.rerun) ? d.rerun : [];
      const kept = Array.isArray(d.preserved) ? d.preserved : [];
      const changes = Array.isArray(d.changes) ? d.changes : [];
      return (
        <div className="space-y-1.5" data-testid="approval-detail">
          {d.reason != null && (
            <p style={{ color: KC.onSurfaceVariant, margin: 0 }}>{String(d.reason)}</p>
          )}
          <div className="flex flex-wrap gap-1.5">
            <Chip>run {String(d.run_id ?? '')}</Chip>
            {rerun.map((p) => (
              <Chip key={`rerun-${String(p)}`}>re-run {String(p)}</Chip>
            ))}
            {kept.map((p) => (
              <Chip key={`keep-${String(p)}`}>keep {String(p)}</Chip>
            ))}
          </div>
          <CodeBox lines={changes.map((c) => ({ text: show(c) }))} />
        </div>
      );
    }
    case 'design_change': {
      const diff = entries(d.diff);
      const affected = Array.isArray(d.affected) ? d.affected : [];
      return (
        <div className="space-y-1.5" data-testid="approval-detail">
          {affected.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {affected.map((a) => (
                <Chip key={String(a)}>{String(a)}</Chip>
              ))}
            </div>
          )}
          <CodeBox
            lines={diff.map(([k, v]) => {
              const added = k.startsWith('+') || k === 'added';
              const removed = k.startsWith('-') || k === 'removed';
              return {
                text: `${added ? '+ ' : removed ? '- ' : '  '}${k}: ${show(v)}`,
                color: added ? KC.success : removed ? KC.error : undefined,
              };
            })}
          />
        </div>
      );
    }
    default: {
      const ref = d.node_id ?? d.loop_id;
      return ref != null ? (
        <div data-testid="approval-detail">
          <Chip>{String(ref)}</Chip>
        </div>
      ) : null;
    }
  }
}

export interface ApprovalCardProps {
  item: ApprovalItem;
  /** Called after a decision is accepted by the gateway. */
  onDecided?: (updated: ApprovalItem) => void;
}

/**
 * One card for every kind of approval (FORGE-508). Decision controls are
 * exactly `allowed_decisions`; a reason box appears for decisions listed in
 * `reason_required_for`; the whole control row is disabled, with the gateway's
 * own explanation, when the item is not decidable.
 */
/** Where a decision was taken, in words (FORGE-583). `unknown` says nothing. */
const SURFACE_WORDS: Record<string, string> = {
  dashboard: 'in the dashboard',
  cli: 'from the CLI',
  agent: 'through an agent',
  chat: "in the client's chat",
};

/** "by user:fidel (unverified) in the client's chat", or null when nothing is known. */
export function decidedBy(record: ApprovalDecisionRecord): string | null {
  const parts: string[] = [];
  if (record.approver) {
    parts.push(`by ${record.approver}${record.approver_verified ? '' : ' (unverified)'}`);
  }
  const where = record.surface ? SURFACE_WORDS[record.surface] : undefined;
  if (where) parts.push(where);
  if (record.surface === 'agent' && record.agent) {
    parts.push(`(${record.agent}${record.on_behalf_of ? ` for ${record.on_behalf_of}` : ''})`);
  }
  return parts.length ? parts.join(' ') : null;
}

export function ApprovalCard({ item, onDecided }: ApprovalCardProps) {
  const decide = useDecideApproval();
  const [choice, setChoice] = useState<ApprovalDecision | null>(null);
  const [reason, setReason] = useState('');
  const [toPhase, setToPhase] = useState('');

  const isPending = item.status === 'pending';
  const decisions = DECISION_ORDER.filter((d) => item.allowed_decisions.includes(d));
  const reasonRequired = choice !== null && item.reason_required_for.includes(choice);
  const needsPhase = choice === 'rework';
  const missingReason = reasonRequired && reason.trim() === '';
  const missingPhase = needsPhase && toPhase === '';
  const blocked = !item.decidable || decide.isPending;

  function submit() {
    if (!choice || missingReason || missingPhase) return;
    const body: { decision: ApprovalDecision; reason?: string; to_phase?: string } = {
      decision: choice,
    };
    if (reason.trim()) body.reason = reason.trim();
    if (needsPhase) body.to_phase = toPhase;
    decide.mutate(
      { id: item.id, body },
      {
        onSuccess: (updated) => {
          setChoice(null);
          setReason('');
          setToPhase('');
          onDecided?.(updated);
        },
      },
    );
  }

  const errorStatus = (decide.error as { response?: { status?: number } } | null)?.response?.status;
  const errorText =
    errorStatus === 409
      ? 'This item can no longer be decided that way. Refresh to see its current state.'
      : errorStatus === 422
        ? 'The gateway rejected that decision. Check the reason and phase.'
        : 'The decision could not be saved. Check the connection and try again.';

  const statusColor =
    item.status === 'approved' ? KC.success : item.status === 'rejected' ? KC.error : KC.onSurfaceVariant;

  return (
    <article
      className="rounded-lg space-y-3 p-4"
      aria-label={item.title}
      data-testid="approval-card"
      style={{
        background: KC.glass,
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        border: `1px solid ${KC.border}`,
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium leading-snug" style={{ color: KC.onSurface }}>
            {item.title}
          </div>
          {item.summary && (
            <div style={{ color: KC.onSurfaceVariant, fontSize: 13 }}>{item.summary}</div>
          )}
          <div className="mt-1 flex items-center gap-2 flex-wrap">
            <Chip>{KIND_LABELS[item.kind] ?? item.kind}</Chip>
            {item.project_id && <Chip>project {item.project_id}</Chip>}
            <span className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>
              {formatRelativeTime(item.created_at)}
            </span>
            {item.deadline && (
              <span className="font-mono" style={{ fontSize: 12, color: KC.warning }}>
                deadline {new Date(item.deadline).toLocaleString()}
              </span>
            )}
          </div>
        </div>
        <Chip color={statusColor}>{item.status}</Chip>
      </div>

      {item.reason_held && (
        <p style={{ margin: 0, color: KC.onSurface }} data-testid="approval-reason-held">
          <strong>Why held:</strong> {item.reason_held}
        </p>
      )}

      {item.findings.length > 0 && (
        <ul className="space-y-1" style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
          {item.findings.map((f, i) => (
            <li
              key={i}
              data-severity={f.severity}
              style={{ color: SEVERITY_COLOR[f.severity], fontSize: 13 }}
            >
              <span className="font-mono" style={{ textTransform: 'uppercase', marginRight: 6 }}>
                {f.severity}
              </span>
              <span className="font-mono" style={{ color: KC.onSurfaceVariant, marginRight: 6 }}>
                {f.kind.replace(/_/g, ' ')}
              </span>
              {f.message}
            </li>
          ))}
        </ul>
      )}

      <KindDetail item={item} />

      {isPending && (
        <div className="space-y-2 pt-1">
          {!item.decidable && (
            <p role="status" style={{ margin: 0, color: KC.warning }} data-testid="not-decidable">
              {item.not_decidable_reason ?? 'This item cannot be decided right now.'}
            </p>
          )}
          <div className="flex items-center gap-2 flex-wrap">
            {decisions.map((d) => (
              <Button
                key={d}
                variant={d === 'approve' ? 'primary' : 'ghost'}
                size="sm"
                aria-pressed={choice === d}
                disabled={blocked}
                onClick={() => {
                  setChoice(choice === d ? null : d);
                  decide.reset();
                }}
                className="gap-1.5"
                style={
                  d === 'approve'
                    ? { backgroundColor: KC.primaryContainer, color: KC.surface, border: 'none' }
                    : d === 'reject'
                      ? {
                          backgroundColor: 'rgba(255,180,171,0.10)',
                          color: KC.error,
                          border: '1px solid rgba(255,180,171,0.20)',
                        }
                      : { border: `1px solid ${KC.border}` }
                }
              >
                <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
                  {DECISION_ICONS[d]}
                </span>
                {DECISION_LABELS[d]}
              </Button>
            ))}
          </div>

          {choice && (
            <div className="space-y-2">
              {needsPhase && (
                <label className="block" style={{ color: KC.onSurfaceVariant, fontSize: 13 }}>
                  Rework from phase
                  <select
                    aria-label="Rework phase"
                    value={toPhase}
                    onChange={(e) => setToPhase(e.target.value)}
                    className="block rounded mt-1 p-1.5"
                    style={{
                      background: KC.surfaceLowest,
                      color: KC.onSurface,
                      border: `1px solid ${KC.border}`,
                    }}
                  >
                    <option value="">Select a phase…</option>
                    {item.rework_targets.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="block" style={{ color: KC.onSurfaceVariant, fontSize: 13 }}>
                Reason{reasonRequired ? ' (required)' : ' (optional)'}
                <textarea
                  aria-label="Decision reason"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  rows={2}
                  className="block w-full rounded mt-1 p-2"
                  style={{
                    background: KC.surfaceLowest,
                    color: KC.onSurface,
                    border: `1px solid ${KC.border}`,
                  }}
                />
              </label>
              <Button
                variant="primary"
                size="sm"
                disabled={blocked || missingReason || missingPhase}
                onClick={submit}
                style={{ backgroundColor: KC.primaryContainer, color: KC.surface, border: 'none' }}
              >
                {decide.isPending ? 'Submitting…' : `Confirm ${DECISION_LABELS[choice].toLowerCase()}`}
              </Button>
            </div>
          )}
        </div>
      )}

      {decide.isError && (
        <p role="alert" style={{ margin: 0, color: KC.error }}>
          {errorText}
        </p>
      )}

      {item.decision && (
        <div
          className="font-mono space-y-0.5"
          style={{ fontSize: 12, color: KC.onSurfaceVariant }}
          data-testid="approval-decision"
        >
          <div>
            {item.decision.decision} {formatRelativeTime(item.decision.decided_at)}
            {item.decision.reason && `: ${item.decision.reason}`}
          </div>
          {decidedBy(item.decision) && <div data-testid="approval-decided-by">{decidedBy(item.decision)}</div>}
        </div>
      )}
    </article>
  );
}
