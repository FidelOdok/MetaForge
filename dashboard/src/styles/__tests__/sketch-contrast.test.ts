import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { compositeHex, contrastRatio } from '../../lib/contrast';

/**
 * Sketch strokes, fitted shapes and handles are readable on the sketch paper
 * in both themes. They used to be near-white on the light theme's near-white
 * paper (about 1.1:1). The bar here is 3:1 (WCAG non-text contrast); the
 * reference image and the trace still stay faint on purpose.
 */
const STYLES = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (name: string) => readFileSync(join(STYLES, name), 'utf8').replace(/\r\n/g, '\n');

function block(css: string, selector: string): Record<string, string> {
  const at = css.indexOf(`${selector} {`);
  if (at < 0) throw new Error(`no ${selector} block`);
  const body = css.slice(css.indexOf('{', at) + 1, css.indexOf('}', at));
  const out: Record<string, string> = {};
  for (const decl of body.split(';')) {
    const m = decl.match(/(--[\w-]+)\s*:\s*([^;]+)/);
    if (m) out[m[1]!] = m[2]!.trim();
  }
  return out;
}

function rule(css: string, selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at < 0) throw new Error(`no ${selector} rule`);
  return css.slice(at, css.indexOf('}', at));
}

const tokens = read('sketch-tokens.css');
const THEMES = {
  dark: block(tokens, ':root,:root.dark'),
  light: block(tokens, ':root[data-theme=light]'),
};

/** Stroke, shape and handle tokens, with the opacity their rule draws them at. */
const ON_PAPER: [token: string, opacity: number][] = [
  ['--mf-sketch-ink', 1],
  ['--mf-sketch-select', 1],
  ['--mf-sketch-shape', 0.85], // an unsure fitted shape is drawn at 0.85
  ['--mf-sketch-accept', 1],
  ['--mf-sketch-propose', 1],
  ['--mf-sketch-bad', 1],
  ['--mf-sketch-muted', 1],
  ['--mf-sketch-ghost', 0.75],
];

describe('sketch contrast tokens', () => {
  for (const theme of ['dark', 'light'] as const) {
    const t = THEMES[theme];
    it(`keeps strokes, shapes and handles at 3:1 or more on the ${theme} paper`, () => {
      const paper = t['--mf-sketch-paper']!;
      for (const [name, opacity] of ON_PAPER) {
        const ratio = contrastRatio(t[name]!, paper, opacity);
        expect(ratio, `${name} ${t[name]} on ${paper}`).toBeGreaterThanOrEqual(3);
      }
    });

    it(`keeps badge glyphs readable on their chips in the ${theme} theme`, () => {
      const paper = t['--mf-sketch-paper']!;
      // A proposed badge: its colour on the chip (composited on the paper).
      const chipHex = compositeHex(t['--mf-sketch-chip']!, paper);
      expect(contrastRatio(t['--mf-sketch-propose']!, chipHex)).toBeGreaterThanOrEqual(3);
      // An accepted badge: its glyph on the accept colour.
      expect(contrastRatio(t['--mf-sketch-on-accept']!, t['--mf-sketch-accept']!)).toBeGreaterThanOrEqual(3);
    });
  }

  it('draws every stroke, shape and handle through the tokens', () => {
    const sketch = read('sketch.css');
    expect(rule(sketch, '.sk-layer')).toContain('--sk-ink: var(--mf-sketch-ink)');
    expect(rule(sketch, '.sk-layer')).toContain('--sk-select: var(--mf-sketch-select)');
    expect(rule(sketch, '.sk-stroke,\n.sk-live')).toContain('stroke: var(--sk-ink)');

    const compiler = read('sketch-compiler.css');
    const overlay = rule(compiler, '.skc-overlay');
    for (const name of ['shape', 'accept', 'propose', 'bad']) {
      expect(overlay).toContain(`--skc-${name}: var(--mf-sketch-${name})`);
    }
    expect(rule(compiler, '.skc-shape')).toContain('stroke: var(--skc-shape)');
    expect(rule(compiler, '.skc-shape.is-construction')).toContain('var(--mf-sketch-muted)');
    expect(rule(compiler, '.skc-ghost')).toContain('var(--mf-sketch-ghost)');
    expect(rule(compiler, '.skc-badge')).toContain('background: var(--mf-sketch-chip)');

    const stage = read('create-stage.css');
    expect(rule(stage, '.cv-paper')).toContain('var(--mf-sketch-paper)');
    // No hard-coded near-white ink is left on the paper.
    expect(sketch).not.toMatch(/--sk-ink:\s*#/);
  });

  it('loads the sketch tokens, and never puts a view of the model under the paper', () => {
    const stage = read('create-stage.css');
    // Sketching lives in Imagine: no trace of the 3D view under the paper, no sketch over the model.
    expect(stage).not.toContain('.cv-trace');
    expect(stage).not.toContain('.cv-sketch-overlay');
    expect(read('../index.css')).toContain('@import "./styles/sketch-tokens.css";');
  });
});
