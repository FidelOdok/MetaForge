import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Brand assets referenced by the app must exist and must be the corrected
 * logo.
 *
 * Both failures this guards against have already happened here.
 *
 * The sign-in and first-run screens shipped pointing at
 * `/logo/metaforge-mark-dark.svg` — the *marketing site's* layout, which does
 * not exist in this package. It rendered as a broken image rather than a 404,
 * because the SPA catch-all rewrite answered the request with `index.html`
 * and a 200. That is the same mechanism that made `/api/v1/projects` look
 * like a successful response whose body happened to be HTML.
 *
 * Separately, this package's own lockups carried the strapline "From Intent
 * Hardware" — a word-order scramble — for weeks after it was fixed elsewhere,
 * because the fix was applied to `marketing/` and nobody checked here.
 */

const PUBLIC = join(__dirname, '..', '..', 'public');
const SRC = join(__dirname, '..');

/** Every root-absolute asset path referenced from a `src=` in the app. */
function referencedAssets(): string[] {
  const found = new Set<string>();
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== 'node_modules') walk(full);
      } else if (/\.(ts|tsx)$/.test(entry.name)) {
        const text = readFileSync(full, 'utf-8');
        for (const m of text.matchAll(/src="(\/[^"]+\.[a-z0-9]+)"/gi)) {
          if (m[1]) found.add(m[1]);
        }
      }
    }
  };
  walk(SRC);
  return [...found];
}

describe('brand assets', () => {
  it('references only assets that exist in public/', () => {
    const refs = referencedAssets();
    expect(refs.length, 'no asset references found — has the scan broken?').toBeGreaterThan(0);
    const missing = refs.filter((ref) => !existsSync(join(PUBLIC, ref.replace(/^\//, ''))));
    expect(
      missing,
      `referenced but absent from dashboard/public — the SPA rewrite will serve index.html ` +
        `for these and they will render as broken images: ${missing.join(', ')}`,
    ).toEqual([]);
  });

  it('ships the corrected strapline, not the scrambled one', () => {
    for (const name of readdirSync(PUBLIC)) {
      if (!name.endsWith('.svg')) continue;
      const svg = readFileSync(join(PUBLIC, name), 'utf-8');
      expect(svg, `${name} carries the scrambled strapline`).not.toMatch(
        /From Intent Hardware/i,
      );
    }
  });

  it('keeps the light and dark lockups on opposite inks', () => {
    // They are swapped by CSS on `:root[data-theme]`. Two files with the same
    // ink would leave one theme with an invisible or low-contrast logo.
    const dark = readFileSync(join(PUBLIC, 'metaforge-logo.svg'), 'utf-8');
    const light = readFileSync(join(PUBLIC, 'metaforge-logo-light.svg'), 'utf-8');
    expect(dark).toMatch(/fill="#FFFFFF"/i);
    expect(light).toMatch(/fill="#111319"/i);
    expect(dark).not.toMatch(/fill="#111319"/i);
  });
});

describe('SPA rewrite', () => {
  const rewrite = JSON.parse(
    readFileSync(join(__dirname, '..', '..', 'vercel.json'), 'utf-8'),
  ).rewrites[0].source as string;
  const matches = (path: string) => new RegExp(`^${rewrite}$`).test(path);

  it('rewrites app routes to the SPA', () => {
    for (const path of ['/', '/projects', '/twin', '/projects/abc-123']) {
      expect(matches(path), `${path} should reach the SPA`).toBe(true);
    }
  });

  it('never swallows a request for a file', () => {
    // The whole point: a missing asset must 404 rather than return the app
    // shell with a 200, which is indistinguishable from success to an <img>.
    for (const path of [
      '/favicon.svg',
      '/metaforge-symbol-dark.svg',
      '/assets/index-abc.js',
      '/logo/does-not-exist.svg',
    ]) {
      expect(matches(path), `${path} must not be rewritten`).toBe(false);
    }
  });

  it('never swallows a gateway path', () => {
    for (const path of ['/api/v1/projects', '/v1/projects', '/health']) {
      expect(matches(path), `${path} must reach the gateway, not the SPA`).toBe(false);
    }
  });
});
