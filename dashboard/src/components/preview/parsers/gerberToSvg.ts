/**
 * Gerber / NC drill to SVG for the Gerber preview engine, via tracespace
 * (parser, plotter, renderer). Lazy-loaded with the engine. The renderer
 * returns a hast tree; it is serialised here to an SVG string shown through
 * an <img>, so nothing in the file can run script.
 */
import { createParser } from '@tracespace/parser';
import { plot } from '@tracespace/plotter';
import { render } from '@tracespace/renderer';

interface HastNode {
  type: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
  value?: string;
}

// hast property names that do not round-trip by a plain camelCase-to-kebab rule.
const ATTR_NAMES: Record<string, string> = {
  viewBox: 'viewBox',
  xmlnsXLink: 'xmlns:xlink',
  xLinkHref: 'xlink:href',
  strokeLineCap: 'stroke-linecap',
  strokeLineJoin: 'stroke-linejoin',
  strokeMiterLimit: 'stroke-miterlimit',
  strokeDashArray: 'stroke-dasharray',
  clipPathUnits: 'clipPathUnits',
  maskUnits: 'maskUnits',
  preserveAspectRatio: 'preserveAspectRatio',
};

function attrName(prop: string): string {
  return ATTR_NAMES[prop] ?? prop.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function hastToSvg(node: HastNode): string {
  if (node.type === 'text') return esc(node.value ?? '');
  if (node.type !== 'element' || !node.tagName) return (node.children ?? []).map(hastToSvg).join('');
  const attrs = Object.entries(node.properties ?? {})
    .filter(([, v]) => v !== undefined && v !== null && v !== false)
    .map(([k, v]) => `${attrName(k)}="${esc(Array.isArray(v) ? v.join(' ') : String(v))}"`)
    .join(' ');
  const inner = (node.children ?? []).map(hastToSvg).join('');
  return `<${node.tagName}${attrs ? ` ${attrs}` : ''}>${inner}</${node.tagName}>`;
}

export interface GerberSvgResult {
  svg: string;
  empty: boolean;
  /** FORGE-687: [x, y, width, height] in the file's units, for stacking layers. */
  viewBox: [number, number, number, number] | null;
}

/** Copper-ish on the dark canvas; the renderer draws in `currentColor`. */
const LAYER_COLOR = '#e8b04a';

export function gerberToSvg(text: string, color: string = LAYER_COLOR): GerberSvgResult {
  const parser = createParser();
  parser.feed(text);
  const image = plot(parser.result());
  const tree = render(image) as unknown as HastNode;
  const viewBox = String(tree.properties?.viewBox ?? '');
  const empty = image.children.length === 0 || viewBox === '0 0 0 0';
  const box = viewBox.split(/[\s,]+/).map(Number);
  // Let the <img> size the drawing, and colour it.
  tree.properties = { ...tree.properties, width: '100%', height: '100%', style: `color:${color}` };
  return {
    svg: hastToSvg(tree),
    empty,
    viewBox: box.length === 4 && box.every(Number.isFinite) ? (box as [number, number, number, number]) : null,
  };
}

/**
 * FORGE-687: one box around every layer, so layers drawn in it line up. The
 * renderer only negates y (no per-layer offset), so a shared viewBox stacks
 * a board's gerbers exactly.
 */
export function unionViewBox(
  boxes: ([number, number, number, number] | null)[],
): [number, number, number, number] | null {
  const real = boxes.filter((b): b is [number, number, number, number] => b !== null && b[2] > 0 && b[3] > 0);
  if (!real.length) return null;
  const x0 = Math.min(...real.map((b) => b[0]));
  const y0 = Math.min(...real.map((b) => b[1]));
  const x1 = Math.max(...real.map((b) => b[0] + b[2]));
  const y1 = Math.max(...real.map((b) => b[1] + b[3]));
  return [x0, y0, x1 - x0, y1 - y0];
}

/** The same drawing in another viewBox (the first viewBox attribute is the root's). */
export function withViewBox(svg: string, box: [number, number, number, number]): string {
  return svg.replace(/viewBox="[^"]*"/, `viewBox="${box.join(' ')}"`);
}
