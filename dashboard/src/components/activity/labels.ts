import type { OperationView } from '../../lib/operations-reducer';
import type { ArtifactRef, OperationDomain, OperationState } from '../../types/operations';

/**
 * The words the activity overlay uses (RT-022). Every label is derived from
 * the operation's backend state; nothing here reads a clock to decide status.
 */

export type StatusLabel =
  | 'Queued'
  | 'Working'
  | 'Waiting for a decision'
  | 'Checking'
  | 'Ready'
  | 'Needs attention'
  | 'Unable to complete'
  | 'Cancelled'
  | 'Replaced by a newer request'
  | 'Out of date';

const CHECKING = /check|valid|verif|drc|erc|test|inspect/i;

function isChecking(op: OperationView): boolean {
  const stage = op.stages.find((s) => s.state === 'running');
  return CHECKING.test(op.progress?.unit ?? '') || CHECKING.test(stage?.stage_id ?? '') || CHECKING.test(stage?.stage_title ?? '');
}

export function statusLabel(op: OperationView): StatusLabel {
  switch (op.state) {
    case 'queued':
      return 'Queued';
    case 'running':
      return isChecking(op) ? 'Checking' : 'Working';
    case 'waiting_for_user':
      return 'Waiting for a decision';
    case 'blocked':
      return 'Needs attention';
    case 'completed':
      return 'Ready';
    case 'failed':
      // A failure the person can act on needs their attention; one with no
      // recovery path is reported as such rather than dressed up.
      return op.action_refs.some((a) => a.kind === 'retry' || a.kind === 'open' || a.kind === 'approval')
        ? 'Needs attention'
        : 'Unable to complete';
    case 'cancelled':
      return 'Cancelled';
    case 'superseded':
      return 'Replaced by a newer request';
    case 'stale':
      return 'Out of date';
  }
}

/** A short plain-language tone for styling; colour is never the only signal. */
export function statusTone(state: OperationState): 'neutral' | 'active' | 'attention' | 'ok' | 'bad' | 'muted' {
  switch (state) {
    case 'queued':
      return 'neutral';
    case 'running':
      return 'active';
    case 'waiting_for_user':
    case 'blocked':
      return 'attention';
    case 'completed':
      return 'ok';
    case 'failed':
      return 'bad';
    default:
      return 'muted';
  }
}

const DOMAIN_WORDS: Record<OperationDomain, string> = {
  concept: 'Concept',
  sketch: 'Sketch',
  mechanical: 'Shape',
  electronics: 'Electronics',
  firmware: 'Firmware',
  simulation: 'Simulation',
  manufacturing: 'Making',
  cross_domain: 'Coordination',
  general: 'General',
};

const DOMAIN_ENGINEERING: Record<OperationDomain, string> = {
  concept: 'Concept',
  sketch: 'Sketch',
  mechanical: 'CAD',
  electronics: 'EDA',
  firmware: 'Firmware',
  simulation: 'Simulation',
  manufacturing: 'Manufacturing',
  cross_domain: 'Cross-domain',
  general: 'General',
};

export function domainLabel(domain: OperationDomain, detail: boolean): string {
  return (detail ? DOMAIN_ENGINEERING : DOMAIN_WORDS)[domain] ?? domain;
}

/** The step being worked on now, in the server's own words. */
export function currentStep(op: OperationView): string {
  const running = [...op.stages].reverse().find((s) => s.state === 'running' || s.state === 'waiting_for_user');
  return running?.stage_title || op.message || op.title;
}

/** One next-action sentence (RT-006). */
export function nextAction(op: OperationView): string {
  const approval = op.action_refs.find((a) => a.kind === 'approval');
  switch (op.state) {
    case 'waiting_for_user':
      return approval ? `Your decision is needed: ${approval.label}` : 'Your decision is needed to continue.';
    case 'blocked':
      return op.message || 'This is blocked until the issue shown is resolved.';
    case 'failed':
      return op.action_refs.some((a) => a.kind === 'retry')
        ? 'Inspect the issue, then retry when ready.'
        : 'Inspect the issue. This could not be completed.';
    case 'completed':
      return 'Done. You can keep working.';
    case 'queued':
      return 'Waiting to start. You can keep working.';
    case 'running':
      return 'Nothing is needed from you. You can keep working.';
    case 'cancelled':
      return 'Cancelled. Nothing further will happen.';
    case 'superseded':
      return 'A newer request replaced this one.';
    case 'stale':
      return op.target_revision
        ? `This result belongs to revision ${op.target_revision}.`
        : 'This result belongs to an earlier revision.';
  }
}

/** "1m 05s", "12s", "2h 03m". */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`;
  return `${s}s`;
}

/** A measured percentage, only when the backend gave a total (RT-021, ASVG-016). */
export function progressText(op: OperationView): string | null {
  const p = op.progress;
  if (!p || !(p.total > 0)) return null;
  const pct = Math.round((Math.min(p.done, p.total) / p.total) * 100);
  return `${p.done} of ${p.total} ${p.unit} complete (${pct}%)`;
}

/**
 * The first artifact that is a real result, not an illustration (ASVG-004).
 * Newest first, so the latest preview wins.
 */
export function realArtifact(op: OperationView): ArtifactRef | null {
  for (let i = op.artifact_refs.length - 1; i >= 0; i -= 1) {
    const ref = op.artifact_refs[i];
    if (ref && ref.authority !== 'illustrative') return ref;
  }
  return null;
}

export function authorityLabel(ref: ArtifactRef): string {
  switch (ref.authority) {
    case 'validated':
      return 'Validated result';
    case 'exact':
      return 'Exact result';
    case 'preview':
      return 'Preview';
    default:
      return 'Generated result';
  }
}

export interface InspectTarget {
  href: string;
  label: string;
  external: boolean;
}

/**
 * Where clicking a depicted element goes (ASVG-015): the newest artifact the
 * operation produced (a twin work product opens in the twin viewer, anything
 * else at its own url), else the run. `null` means: show the stage's detail.
 */
export function inspectTarget(op: Pick<OperationView, 'artifact_refs' | 'run_id' | 'title'>): InspectTarget | null {
  for (let i = op.artifact_refs.length - 1; i >= 0; i -= 1) {
    const ref = op.artifact_refs[i];
    if (!ref) continue;
    const label = ref.label || op.title;
    if (ref.kind === 'work_product') return { href: `/twin?node=${encodeURIComponent(ref.id)}`, label, external: false };
    if (ref.url) return { href: ref.url, label, external: /^https?:/i.test(ref.url) };
  }
  if (op.run_id) return { href: `/runs/${encodeURIComponent(op.run_id)}`, label: op.title, external: false };
  return null;
}
