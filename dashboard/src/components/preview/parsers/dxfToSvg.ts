/**
 * DXF to SVG for the DXF preview engine: parse with dxf-parser, then draw the
 * 2D entities MetaForge's DXF sources produce (lines, polylines with bulges,
 * circles, arcs, ellipses, splines, points, text). Lazy-loaded with the
 * engine. The output is an SVG string shown through an <img>, so nothing in
 * the drawing can run script.
 */
import DxfParser from 'dxf-parser';

type Pt = { x: number; y: number };
type Entity = Record<string, unknown> & { type: string };

export interface DxfSvgResult {
  svg: string;
  entityCount: number;
  skipped: string[];
}

const STROKE = '#e2e2eb';
const TEXT_FILL = '#9a9aaa';

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function n(v: number): string {
  return Number.isFinite(v) ? (Math.round(v * 1000) / 1000).toString() : '0';
}

function pt(v: unknown): Pt | null {
  if (!v || typeof v !== 'object') return null;
  const o = v as { x?: unknown; y?: unknown };
  return typeof o.x === 'number' && typeof o.y === 'number' ? { x: o.x, y: o.y } : null;
}

function num(v: unknown, fallback = 0): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback;
}

/** Sample points along a bulged segment (bulge = tan(theta / 4)). */
function bulgePoints(a: Pt, b: Pt, bulge: number, steps = 16): Pt[] {
  // Signed included angle; positive sweeps counter-clockwise from a to b.
  const theta = 4 * Math.atan(bulge);
  const chord = Math.hypot(b.x - a.x, b.y - a.y);
  if (chord === 0 || theta === 0) return [b];
  const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  // The centre sits on the chord's left normal, (chord / 2) / tan(theta / 2) away.
  const d = chord / (2 * Math.tan(theta / 2));
  const ux = (b.x - a.x) / chord;
  const uy = (b.y - a.y) / chord;
  const c = { x: mid.x - uy * d, y: mid.y + ux * d };
  const radius = Math.hypot(a.x - c.x, a.y - c.y);
  const start = Math.atan2(a.y - c.y, a.x - c.x);
  const out: Pt[] = [];
  for (let k = 1; k <= steps; k++) {
    const t = start + (theta * k) / steps;
    out.push({ x: c.x + radius * Math.cos(t), y: c.y + radius * Math.sin(t) });
  }
  out[out.length - 1] = b;
  return out;
}

function arcPoints(c: Pt, r: number, start: number, end: number, steps = 48): Pt[] {
  let sweep = end - start;
  while (sweep <= 0) sweep += Math.PI * 2;
  const out: Pt[] = [];
  for (let k = 0; k <= steps; k++) {
    const t = start + (sweep * k) / steps;
    out.push({ x: c.x + r * Math.cos(t), y: c.y + r * Math.sin(t) });
  }
  return out;
}

export function dxfToSvg(text: string): DxfSvgResult {
  const parsed = new DxfParser().parseSync(text);
  const entities = ((parsed?.entities ?? []) as unknown as Entity[]).filter((e) => e && typeof e.type === 'string');
  const paths: Pt[][] = [];
  const closed: boolean[] = [];
  const circles: { c: Pt; r: number }[] = [];
  const texts: { p: Pt; h: number; s: string }[] = [];
  const skipped = new Set<string>();

  const addPath = (p: Pt[], isClosed = false) => {
    if (p.length >= 2) {
      paths.push(p);
      closed.push(isClosed);
    }
  };

  for (const e of entities) {
    switch (e.type) {
      case 'LINE': {
        const v = (e.vertices as unknown[] | undefined)?.map(pt).filter((p): p is Pt => !!p) ?? [];
        addPath(v);
        break;
      }
      case 'LWPOLYLINE':
      case 'POLYLINE': {
        const verts = (e.vertices as Record<string, unknown>[] | undefined) ?? [];
        const isClosed = e.shape === true;
        const pts: Pt[] = [];
        verts.forEach((v, i) => {
          const p = pt(v);
          if (!p) return;
          if (pts.length === 0) pts.push(p);
          const next = pt(verts[i + 1]) ?? (isClosed ? pt(verts[0]) : null);
          const bulge = num(v.bulge);
          if (next) pts.push(...(bulge ? bulgePoints(p, next, bulge) : [next]));
        });
        addPath(pts, isClosed);
        break;
      }
      case 'CIRCLE': {
        const c = pt(e.center);
        if (c) circles.push({ c, r: num(e.radius) });
        break;
      }
      case 'ARC': {
        const c = pt(e.center);
        if (c) addPath(arcPoints(c, num(e.radius), num(e.startAngle), num(e.endAngle)));
        break;
      }
      case 'ELLIPSE': {
        const c = pt(e.center);
        const major = pt(e.majorAxisEndPoint);
        if (!c || !major) break;
        const ratio = num(e.axisRatio, 1);
        const a = Math.hypot(major.x, major.y);
        const rot = Math.atan2(major.y, major.x);
        const start = num(e.startAngle, 0);
        let end = num(e.endAngle, Math.PI * 2);
        if (end <= start) end += Math.PI * 2;
        const pts: Pt[] = [];
        for (let k = 0; k <= 64; k++) {
          const t = start + ((end - start) * k) / 64;
          const x = a * Math.cos(t);
          const y = a * ratio * Math.sin(t);
          pts.push({ x: c.x + x * Math.cos(rot) - y * Math.sin(rot), y: c.y + x * Math.sin(rot) + y * Math.cos(rot) });
        }
        addPath(pts);
        break;
      }
      case 'SPLINE': {
        const src = ((e.fitPoints as unknown[]) ?? (e.controlPoints as unknown[]) ?? []).map(pt);
        addPath(src.filter((p): p is Pt => !!p), e.closed === true);
        break;
      }
      case 'POINT': {
        const p = pt(e.position);
        if (p) circles.push({ c: p, r: 0 });
        break;
      }
      case 'TEXT':
      case 'MTEXT': {
        const p = pt(e.startPoint) ?? pt(e.position);
        const s = typeof e.text === 'string' ? e.text.replace(/\\P/g, ' ').replace(/\\[A-Za-z][^;]*;/g, '') : '';
        if (p && s) texts.push({ p, h: num(e.textHeight, num(e.height, 2.5)), s });
        break;
      }
      default:
        skipped.add(e.type);
    }
  }

  // Bounds over everything drawn.
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const grow = (p: Pt, pad = 0) => {
    minX = Math.min(minX, p.x - pad);
    minY = Math.min(minY, p.y - pad);
    maxX = Math.max(maxX, p.x + pad);
    maxY = Math.max(maxY, p.y + pad);
  };
  paths.forEach((p) => p.forEach((q) => grow(q)));
  circles.forEach(({ c, r }) => grow(c, r));
  texts.forEach(({ p, h, s }) => {
    grow(p);
    grow({ x: p.x + h * 0.6 * s.length, y: p.y + h });
  });

  const drawn = paths.length + circles.length + texts.length;
  if (drawn === 0 || !Number.isFinite(minX)) {
    return { svg: '', entityCount: 0, skipped: [...skipped] };
  }

  const w = Math.max(maxX - minX, 1e-6);
  const h = Math.max(maxY - minY, 1e-6);
  const pad = Math.max(w, h) * 0.04;
  // DXF is y-up; SVG is y-down. Flip every y as it is written.
  const fy = (y: number) => -y;
  const viewBox = [minX - pad, fy(maxY) - pad, w + pad * 2, h + pad * 2].map(n).join(' ');

  const body: string[] = [];
  paths.forEach((p, i) => {
    const d = p.map((q, k) => `${k === 0 ? 'M' : 'L'}${n(q.x)} ${n(fy(q.y))}`).join(' ') + (closed[i] ? ' Z' : '');
    body.push(`<path d="${d}"/>`);
  });
  circles.forEach(({ c, r }) => {
    body.push(
      r > 0
        ? `<circle cx="${n(c.x)}" cy="${n(fy(c.y))}" r="${n(r)}"/>`
        : `<circle cx="${n(c.x)}" cy="${n(fy(c.y))}" r="${n(pad / 8)}" fill="${STROKE}"/>`,
    );
  });
  const textBody = texts
    .map(({ p, h: th, s }) => `<text x="${n(p.x)}" y="${n(fy(p.y))}" font-size="${n(th)}">${esc(s)}</text>`)
    .join('');

  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" preserveAspectRatio="xMidYMid meet">` +
    `<g fill="none" stroke="${STROKE}" stroke-width="1" vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round">` +
    body.map((b) => b.replace('/>', ' vector-effect="non-scaling-stroke"/>')).join('') +
    `</g><g fill="${TEXT_FILL}" font-family="Roboto Mono, monospace">${textBody}</g></svg>`;

  return { svg, entityCount: drawn, skipped: [...skipped] };
}
