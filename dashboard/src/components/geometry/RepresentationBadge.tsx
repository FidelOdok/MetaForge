import type { RepresentationLevel, RepresentationView } from '../../types/twin';

/**
 * C3D slice 2 (SYNC-010): which representation is on screen, and from what.
 *
 * L3 is tessellated from a validated exact model; L2 is an exact model not
 * yet validated (or a preview); L1 is simple parts with estimated sizes; L0
 * is an AI 3D concept; a picture is a picture. The text is the meaning;
 * colour only repeats it. L0, L1 and pictures are never called exact or
 * validated, whatever a record says (the label guard).
 */

const LEVEL_TEXT: Record<RepresentationLevel, string> = {
  L3: 'L3 · from the validated exact model',
  L2: 'L2 · exact model, not yet validated',
  L1: 'L1 · simple parts, sizes estimated',
  L0: 'L0 · AI 3D concept',
  illustration_image: 'Picture · illustrative',
};

/** The most a level's label may say (the label guard). */
const LABEL_CEILING: Record<RepresentationLevel, string[]> = {
  L3: ['Illustrative', 'Preview', 'Exact', 'Validated'],
  L2: ['Illustrative', 'Preview', 'Exact'],
  L1: ['Illustrative', 'Preview'],
  L0: ['Illustrative'],
  illustration_image: ['Illustrative'],
};

/** A label the level allows, or the most it may claim. */
export function guardedLabel(level: RepresentationLevel, label: string): string {
  const allowed = LABEL_CEILING[level] ?? ['Illustrative'];
  return allowed.includes(label) ? label : (allowed[allowed.length - 1] ?? 'Illustrative');
}

export function representationText(rep: RepresentationView | null | undefined): string | null {
  if (!rep) return null;
  let text = LEVEL_TEXT[rep.level] ?? rep.level;
  if (rep.level === 'L2' && rep.authority === 'preview') text = 'L2 · preview, not exact';
  return rep.source_revision ? `${text} · from ${rep.source_revision}` : text;
}

const TONE: Record<string, string> = {
  Illustrative: '#f59e0b',
  Preview: '#86cfff',
  Exact: '#9a9aaa',
  Validated: '#3dd68c',
};

export function RepresentationBadge({ representation }: { representation?: RepresentationView | null }) {
  const text = representationText(representation);
  if (!representation || !text) return null;
  const label = guardedLabel(representation.level, representation.label);
  return (
    <span
      className="representation-badge"
      data-testid="representation-badge"
      data-level={representation.level}
      title={`Representation: ${text}`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '1px 8px',
        borderRadius: 999,
        fontSize: 11,
        boxShadow: `inset 0 0 0 1px ${TONE[label] ?? '#9a9aaa'}`,
      }}
    >
      {label} · {text}
    </span>
  );
}
