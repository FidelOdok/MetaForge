import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { VIEWER_GPU_PREFERENCE } from '../viewerColor';

/**
 * Every 3D surface must ask for the discrete GPU (FORGE-625).
 *
 * A WebGL context that says nothing gets `powerPreference: 'default'`, and on
 * a machine with two GPUs that is when a browser picks the integrated one.
 * Measured on two of this project's machines: both reported AMD integrated
 * graphics over D3D11, including the one with an RTX in it. Windows' per-app
 * graphics preference cannot rescue that, because it can only honour a
 * preference the application expressed.
 *
 * This walks the source rather than rendering, because the failure is a
 * missing prop on a Canvas that renders perfectly well without it -- there is
 * no behaviour to assert, only an omission to catch.
 */
// Resolve from this file rather than the working directory, which vitest
// does not guarantee. fileURLToPath handles the Windows/WSL path forms that a
// raw URL pathname gets wrong.
const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

function tsxFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return entry === 'node_modules' ? [] : tsxFiles(full);
    return full.endsWith('.tsx') ? [full] : [];
  });
}

describe('every Canvas asks for the discrete GPU (FORGE-625)', () => {
  const canvases = tsxFiles(SRC)
    .filter((f) => !f.includes('__tests__'))
    .map((f) => ({ file: relative(SRC, f), text: readFileSync(f, 'utf8') }))
    .filter(({ text }) => /<Canvas[\s>]/.test(text));

  it('finds the 3D surfaces to check', () => {
    // A guard on the guard: if the search stops matching, the test below would
    // pass by checking nothing.
    expect(canvases.length).toBeGreaterThanOrEqual(5);
  });

  it.each(canvases.map((c) => c.file))('%s passes VIEWER_GPU_PREFERENCE', (file) => {
    const { text } = canvases.find((c) => c.file === file)!;

    // The spread, not the bare name: importing it and then not spreading it
    // into the gl props is exactly the failure this guards against, and a
    // substring check on the name alone would pass on the import line.
    expect(text).toMatch(/\.\.\.VIEWER_GPU_PREFERENCE/);
  });

  it('asks for high performance, not merely something', () => {
    expect(VIEWER_GPU_PREFERENCE.powerPreference).toBe('high-performance');
  });
});
