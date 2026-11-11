/**
 * What a new studio revision changed, in plain words (MetaForge, 2026-11-11). Shown after
 * each regeneration so the person sees what the assistant did, with an undo. Computed from
 * the two configs only; nothing is inferred.
 */

type Rec = Record<string, unknown>;

const MAX_LINES = 8;

/** The other parts of a config, as the person knows them. */
const SECTION_LABEL: Record<string, string> = {
  product: 'product name and brief',
  process: 'manufacturing process',
  power: 'power',
  functions: 'what it does',
  requirements: 'requirements',
  programme: 'programme notes',
  scenarios: 'scenarios',
  environment: 'where it is used',
  context: 'components and context',
  catalogue: 'catalogue',
  color: 'colour',
  nominations: 'nominations',
  level: 'level',
  ambient: 'ambient',
  simulations: 'simulations',
  decisions: 'decisions',
};

function rec(x: unknown): Rec {
  return x && typeof x === 'object' && !Array.isArray(x) ? (x as Rec) : {};
}

function list(x: unknown): Rec[] {
  return Array.isArray(x) ? x.filter((v): v is Rec => !!v && typeof v === 'object') : [];
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** A code identifier as a label: case_d becomes "Case d" (as the studio shows it). */
function humanise(key: string): string {
  const s = key.replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function makeText(m: unknown): string {
  const r = rec(m);
  return [r.process, r.material, r.finish, r.color].filter((v) => typeof v === 'string' && v).join(' · ') || 'no make';
}

export function describeChanges(prevConfig: unknown, nextConfig: unknown): string[] {
  const prev = rec(prevConfig);
  const next = rec(nextConfig);
  const out: string[] = [];
  const pf = rec(prev.form);
  const nf = rec(next.form);

  const pp = rec(pf.params);
  const np = rec(nf.params);
  for (const k of Object.keys(pp)) {
    if (!(k in np)) out.push(`Removed size ${humanise(k)}`);
    else if (!same(pp[k], np[k])) out.push(`${humanise(k)}: ${String(pp[k])} → ${String(np[k])}`);
  }
  for (const k of Object.keys(np)) if (!(k in pp)) out.push(`New size ${humanise(k)}: ${String(np[k])}`);

  const parts = (f: Rec) => list(rec(f.authored).parts);
  const before = new Map(parts(pf).map((p) => [String(p.id), p]));
  const after = new Map(parts(nf).map((p) => [String(p.id), p]));
  const name = (p: Rec) => String(p.name || p.id);
  for (const [id, p] of after) if (!before.has(id)) out.push(`Added ${name(p)}`);
  for (const [id, p] of before) if (!after.has(id)) out.push(`Removed ${name(p)}`);
  for (const [id, p] of after) {
    const q = before.get(id);
    if (!q) continue;
    if (!same(q.code, p.code) || !same(q.uses, p.uses)) out.push(`Reshaped ${name(p)}`);
    if (!same(q.make, p.make)) {
      const a = makeText(q.make);
      const b = makeText(p.make);
      out.push(a === b ? `${name(p)}: ${b} (now ${rec(p.make).source === 'person' ? 'your choice' : 'proposed'})` : `${name(p)}: ${a} → ${b}`);
    }
  }

  const beh = (f: Rec) => new Map(list(rec(f.authored).behaviours).map((b) => [String(b.id), b]));
  const pb = beh(pf);
  const nb = beh(nf);
  for (const [id, b] of nb) {
    if (!pb.has(id)) out.push(`Added behaviour ${String(b.name || id)}`);
    else if (!same(pb.get(id), b)) out.push(`Changed behaviour ${String(b.name || id)}`);
  }
  for (const [id, b] of pb) if (!nb.has(id)) out.push(`Removed behaviour ${String(b.name || id)}`);

  const shown = out.length;
  if (!shown && !same(pf, nf)) out.push('Changed: the form');

  const keys = [...new Set([...Object.keys(prev), ...Object.keys(next)])].filter((k) => k !== 'form' && k !== 'engine').sort();
  for (const k of keys) if (!same(prev[k], next[k])) out.push(`Changed: ${SECTION_LABEL[k] ?? k}`);

  if (out.length > MAX_LINES + 1) {
    const rest = out.length - MAX_LINES;
    return [...out.slice(0, MAX_LINES), `and ${rest} more changes`];
  }
  return out;
}
