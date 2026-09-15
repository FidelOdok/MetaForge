import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../App';
import { CAPABILITIES, PHASES, PIPELINE, PRINCIPLES } from '../site';

/**
 * The landing page is where scope claims drift — it is written to persuade and
 * revised by whoever is closest to the launch. CLAUDE.md's "Critical
 * Constraints" name the specific overstatements this project must never make,
 * so they are asserted here rather than left to review.
 */

function pageText(): string {
  const { container } = render(<App />);
  return container.textContent ?? '';
}

/** Copy from every data source, whether or not a component renders it today. */
const ALL_COPY = [
  ...CAPABILITIES.map((c) => `${c.title} ${c.body} ${'tag' in c ? c.tag : ''}`),
  ...PHASES.map((p) => `${p.headline} ${p.points.join(' ')}`),
  ...PIPELINE.map((p) => `${p.name} ${p.detail}`),
  ...PRINCIPLES.map((p) => `${p.title} ${p.body}`),
].join(' ');

describe('phase scope claims', () => {
  it('never claims MetaForge generates KiCad schematics (that is Phase 2 write capability)', () => {
    // Word boundaries matter here: without one, "does not rewrite your
    // schematic" — a disclaimer — trips a /writes? …schematic/ pattern.
    const forbidden = [
      /\bgenerates? (a )?(kicad )?schematics?/i,
      /\bschematic generation\b/i,
      /\bauto-?(generate|create)s? .{0,20}schematic/i,
      /\bwrites? .{0,20}(your )?schematic/i,
    ];
    const haystack = `${ALL_COPY} ${pageText()}`;
    for (const pattern of forbidden) {
      expect(haystack, `marketing copy must not match ${pattern}`).not.toMatch(pattern);
    }
  });

  it('describes KiCad as read-only for Phase 1', () => {
    const kicad = CAPABILITIES.find((c) => c.body.includes('KiCad'));
    expect(kicad).toBeDefined();
    expect(kicad!.body).toMatch(/read-only/i);
  });

  it('states Phase 1 as 6-7 disciplines, not 12', () => {
    const phase1 = PHASES.find((p) => p.id === 'phase-1');
    expect(phase1!.headline).toMatch(/6[–-]7 specialist agents across 6[–-]7 core disciplines/);
    expect(`${ALL_COPY} ${pageText()}`).not.toMatch(/12 (disciplines|agents)/i);
  });

  it('states the Phase 1 timeline as six months total, never three to four', () => {
    const phase1 = PHASES.find((p) => p.id === 'phase-1');
    const timeline = phase1!.points.find((p) => /month/i.test(p));
    expect(timeline).toMatch(/six months total/i);
    // "3-4 months" is only ever admissible as the *core dev* sub-span.
    expect(timeline).toMatch(/three to four building/i);
  });

  it('keeps the phase ladder at 6-7 → 19 → 25', () => {
    expect(PHASES.find((p) => p.id === 'phase-2')!.headline).toMatch(/19 agents, 19 disciplines/);
    expect(PHASES.find((p) => p.id === 'phase-3')!.headline).toMatch(/25 disciplines/);
  });
});

describe('page structure', () => {
  it('renders one h1 carrying the product promise', () => {
    render(<App />);
    const headings = screen.getAllByRole('heading', { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]!.textContent).toMatch(/survives review/i);
  });

  it('states the prime rule verbatim', () => {
    expect(pageText()).toMatch(
      /If it can't be versioned, reviewed and built, MetaForge doesn't output it/,
    );
  });

  it('renders an anchor target for every in-page nav link', () => {
    const { container } = render(<App />);
    const inPage = Array.from(container.querySelectorAll('a[href^="#"]'))
      .map((a) => a.getAttribute('href')!)
      .filter((href) => href.length > 1);
    expect(inPage.length).toBeGreaterThan(0);
    for (const href of inPage) {
      expect(container.querySelector(href), `no element with id for ${href}`).not.toBeNull();
    }
  });

  it('opens every external link safely', () => {
    const { container } = render(<App />);
    const external = container.querySelectorAll('a[target="_blank"]');
    expect(external.length).toBeGreaterThan(0);
    external.forEach((a) => expect(a.getAttribute('rel')).toContain('noreferrer'));
  });

  it('warns that the gateway ships without authentication', () => {
    expect(pageText()).toMatch(/without authentication/i);
  });
});
