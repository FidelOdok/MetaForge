import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The landing page is where scope claims drift — it is written to persuade and
 * revised by whoever is closest to the launch. `CLAUDE.md`'s "Critical
 * Constraints" name the specific overstatements this project must never make,
 * so they are asserted here rather than left to review.
 *
 * This replaces the React version of the same guard. The site is now static
 * HTML, so instead of rendering `<App/>` and reading `container.textContent`,
 * the assertions run against the shipped files directly: the markup, the
 * stylesheet, and the four scripts that inject copy at runtime.
 *
 * Reading the scripts matters. `depth.js` and `intent.js` hold scenario text
 * in JS string literals that never appears in `index.html`, so an HTML-only
 * check would pass over the copy most likely to make an engineering claim.
 */

const ROOT = join(__dirname, '..', '..');
const PUBLIC = join(ROOT, 'public');

/** Every file whose text ships to a visitor. */
function shippedCopy(): string {
  const parts = [readFileSync(join(ROOT, 'index.html'), 'utf-8')];
  for (const name of readdirSync(PUBLIC)) {
    if (name.endsWith('.js') || name.endsWith('.css')) {
      parts.push(readFileSync(join(PUBLIC, name), 'utf-8'));
    }
  }
  return parts.join('\n');
}

const COPY = shippedCopy();

/** Visible prose only — tag names and attributes would create false hits. */
const TEXT = COPY.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

describe('phase scope claims', () => {
  it('never claims MetaForge generates KiCad schematics (Phase 2 write capability)', () => {
    // Word boundaries matter: "does not rewrite your schematic" is a
    // disclaimer and must not trip a /writes? …schematic/ pattern.
    const forbidden = [
      /\bgenerates? (a )?(kicad )?schematics?/i,
      /\bschematic generation\b/i,
      /\bauto-?(generate|create)s? .{0,20}schematic/i,
      /\bwrites? .{0,20}(your )?schematic/i,
    ];
    for (const pattern of forbidden) {
      expect(COPY, `marketing copy must not match ${pattern}`).not.toMatch(pattern);
    }
  });

  it('never states Phase 1 as 12 disciplines', () => {
    expect(TEXT).not.toMatch(/\b12 (disciplines|agents)\b/i);
  });

  it('never states the Phase 1 timeline as three to four months', () => {
    expect(TEXT).not.toMatch(/\b(three to four|3\s*[-–]\s*4) months\b/i);
  });

  it('never presents Phase 2 or Phase 3 agent counts as available today', () => {
    // 19 is Phase 2, 25 is Phase 3. Either one stated flatly on a landing
    // page reads as a description of what exists.
    expect(TEXT).not.toMatch(/\b(19|25) (disciplines|agents)\b/i);
  });
});

describe('shipped artefacts', () => {
  it('references only assets that exist', () => {
    const html = readFileSync(join(ROOT, 'index.html'), 'utf-8');
    const refs = [...html.matchAll(/(?:href|src)="(\/[^"]+)"/g)]
      .map((m) => m[1] ?? '')
      .filter((ref) => ref !== '' && !ref.startsWith('//'));
    expect(refs.length).toBeGreaterThan(0);
    for (const ref of refs) {
      const onDisk = join(PUBLIC, ref.replace(/^\//, '').split('?')[0] ?? '');
      expect(() => readFileSync(onDisk), `missing asset: ${ref}`).not.toThrow();
    }
  });

  it('ships the corrected logo, not the scrambled one', () => {
    // The reference design shipped a logo whose strapline read "From Intent
    // Hardware". It is a word-order scramble, it was fixed once already, and
    // re-importing the design is exactly how it would come back.
    const logo = readFileSync(join(PUBLIC, 'logo', 'metaforge-lockup-light.svg'), 'utf-8');
    expect(logo).toContain('Hardware from intent');
    expect(logo).not.toMatch(/From Intent Hardware/i);

    const html = readFileSync(join(ROOT, 'index.html'), 'utf-8');
    expect(html).not.toMatch(/assets\/logo\.svg/);
  });

  it('uses ink that is visible on the light background the site sets', () => {
    // --bg is #fff. A white-ink logo would leave only the orange half of the
    // wordmark showing, which is the failure mode this brand already had.
    const css = readFileSync(join(PUBLIC, 'style.css'), 'utf-8');
    expect(css).toMatch(/--bg:\s*#fff/i);
    const logo = readFileSync(join(PUBLIC, 'logo', 'metaforge-lockup-light.svg'), 'utf-8');
    expect(logo).not.toMatch(/fill="#FFFFFF"/i);
  });

  it('declares a social card, so a shared link is not bare', () => {
    const html = readFileSync(join(ROOT, 'index.html'), 'utf-8');
    expect(html).toMatch(/property="og:image"/);
    expect(html).toMatch(/property="og:url"/);
    expect(() => readFileSync(join(PUBLIC, 'og-card.png'))).not.toThrow();
  });
});

describe('routes to the product', () => {
  const html = readFileSync(join(ROOT, 'index.html'), 'utf-8');

  it('links to the dashboard', () => {
    // The site shipped with no route to the app at all: every outbound link
    // went to GitHub. A landing page that cannot reach the product is the
    // one failure a marketing test should catch.
    expect(html).toMatch(/href="https:\/\/app\.metaforge\.uk"/);
  });

  it('offers the dashboard in the header, not only buried at the bottom', () => {
    const header = html.match(/<header[\s\S]*?<\/header>/)?.[0] ?? '';
    expect(header).toMatch(/app\.metaforge\.uk/);
  });

  it('never points a dashboard link at the docs', () => {
    // The previous React site derived its CTA as
    // `VITE_DASHBOARD_URL || DOCS_URL`, so an unset variable silently sent
    // every "Open the dashboard" button to the documentation instead.
    const ctas = [...html.matchAll(/<a[^>]*href="([^"]+)"[^>]*>(\s*Open the dashboard[^<]*)</gi)];
    expect(ctas.length).toBeGreaterThan(0);
    for (const [, href, label] of ctas) {
      expect(href, `"${label?.trim()}" points at docs`).not.toMatch(/docs|github/i);
    }
  });

});
