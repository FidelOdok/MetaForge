import type { GeometryStateName, GeometryStateView } from '../../types/twin';

/**
 * CDE slice 6 (UI-004): a compact geometry status badge, Concept / Preview /
 * Exact / Validated, with the revision it belongs to. The words carry the
 * meaning; colour only repeats it. A concept is never called manufacturable.
 */

const WORDS: Record<GeometryStateName, string> = {
  concept: 'Concept',
  preview: 'Preview',
  exact_ready: 'Exact',
  validation_pending: 'Exact',
  validated: 'Validated',
  failed: 'Exact',
  stale: 'Exact',
  released: 'Validated',
};

/** A qualifier the badge adds after its word, when the state needs one. */
const QUALIFIER: Partial<Record<GeometryStateName, string>> = {
  concept: 'illustrative',
  validation_pending: 'checking',
  failed: 'check failed',
  stale: 'out of date',
  released: 'released',
};

const TONE: Record<string, string> = {
  Concept: '#f59e0b',
  Preview: '#86cfff',
  Exact: '#9a9aaa',
  Validated: '#3dd68c',
};

export function geometryBadgeWord(state: GeometryStateName | string | null | undefined): string | null {
  if (!state) return null;
  return WORDS[state as GeometryStateName] ?? null;
}

export function geometryBadgeText(state: GeometryStateName | string | null | undefined, revision?: number | null): string | null {
  const word = geometryBadgeWord(state);
  if (!word) return null;
  const qualifier = QUALIFIER[state as GeometryStateName];
  const parts = [qualifier ? `${word} (${qualifier})` : word];
  if (revision != null) parts.push(`revision ${revision}`);
  return parts.join(' · ');
}

interface GeometryStatusBadgeProps {
  /** The node's state as the twin reports it. */
  geometry?: GeometryStateView | null;
  /** A state given directly (from the exports view), with its revision. */
  state?: GeometryStateName | string | null;
  revision?: number | null;
  className?: string;
}

export function GeometryStatusBadge({ geometry, state, revision, className }: GeometryStatusBadgeProps) {
  const name = state ?? geometry?.state ?? null;
  const rev = revision ?? geometry?.sourceRevision ?? null;
  const text = geometryBadgeText(name, rev);
  if (!text) return null;
  const word = geometryBadgeWord(name) ?? '';
  return (
    <span
      className={className ?? 'geometry-badge'}
      data-geometry-state={name ?? undefined}
      title={`Geometry: ${text}`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '1px 8px',
        borderRadius: 999,
        fontSize: 11,
        boxShadow: `inset 0 0 0 1px ${TONE[word] ?? '#9a9aaa'}`,
      }}
    >
      {text}
    </span>
  );
}
