/* FORGE-526: shared labels and number formatting for the item views. */

const TYPE_LABELS: Record<string, string> = {
  cad_model: 'Parts',
  assembly: 'Assemblies',
  constraint_set: 'Requirements',
  intent: 'Intent',
  stakeholder_need: 'Needs',
  objective: 'Objectives',
  bom: 'BOM',
  component_selection: 'Components',
};

export function itemTypeLabel(itemType: string): string {
  return TYPE_LABELS[itemType] ?? itemType.replace(/_/g, ' ');
}

export const GEOMETRY_TYPES = new Set(['cad_model', 'assembly']);

export function fmtNum(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '';
  const abs = Math.abs(value);
  if (abs !== 0 && (abs < 0.001 || abs >= 1e6)) return value.toExponential(2);
  return String(Number(value.toFixed(digits)));
}

export function fmtDelta(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined) return '';
  const text = fmtNum(value, digits);
  return value > 0 ? `+${text}` : text;
}

export function fmtValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${k} ${typeof v === 'number' ? fmtNum(v) : String(v)}`)
      .join(', ');
  }
  return String(value);
}

export function shortId(id: string | null | undefined): string {
  if (!id) return '';
  return id.length > 12 ? `${id.slice(0, 8)}` : id;
}

export const LABEL_STYLE: React.CSSProperties = {
  fontSize: '10px',
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
  color: 'var(--mf-c-9a9aaa)',
};

export const PANEL_STYLE: React.CSSProperties = {
  background: 'var(--mf-r-30-31-38-0p85)',
  border: '1px solid var(--mf-r-65-72-90-0p2)',
};

export const CELL_BORDER = '1px solid var(--mf-r-65-72-90-0p1)';
