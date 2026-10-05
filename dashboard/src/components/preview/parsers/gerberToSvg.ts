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
}

/** Copper-ish on the dark canvas; the renderer draws in `currentColor`. */
const LAYER_COLOR = '#e8b04a';

export function gerberToSvg(text: string): GerberSvgResult {
  const parser = createParser();
  parser.feed(text);
  const image = plot(parser.result());
  const tree = render(image) as unknown as HastNode;
  const viewBox = String(tree.properties?.viewBox ?? '');
  const empty = image.children.length === 0 || viewBox === '0 0 0 0';
  // Let the <img> size the drawing, and colour it.
  tree.properties = { ...tree.properties, width: '100%', height: '100%', style: `color:${LAYER_COLOR}` };
  return { svg: hastToSvg(tree), empty };
}
