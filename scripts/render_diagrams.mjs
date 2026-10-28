#!/usr/bin/env node
// Render docs/assets/diagrams/*.mmd to PNG, next to the source.
//
// The README and the docs show these as images, not as mermaid blocks: a PNG
// looks the same on GitHub, on the docs site, in an editor preview and in a
// package index, and mermaid does not. The .mmd file is the source; edit it and
// run this, then commit both.
//
//   cd dashboard && npm install     # once: provides mermaid and playwright-core
//   node scripts/render_diagrams.mjs            # every diagram
//   node scripts/render_diagrams.mjs how-it-works   # one, by name
//
// Needs a Chromium that playwright-core can launch (`npx playwright install chromium`).

import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dashboard = join(root, 'dashboard');
const require = createRequire(join(dashboard, 'package.json'));
const { chromium } = require('playwright-core');
const mermaidScript = join(dirname(require.resolve('mermaid/package.json')), 'dist', 'mermaid.min.js');

const dir = join(root, 'docs', 'assets', 'diagrams');
const only = process.argv.slice(2);
const sources = readdirSync(dir).filter((f) => f.endsWith('.mmd') && (!only.length || only.includes(f.slice(0, -4))));

const browser = await chromium.launch();
const page = await browser.newPage({ deviceScaleFactor: 2, viewport: { width: 1400, height: 900 } });
await page.setContent('<html><body style="margin:0;background:#fff"><div id="out" style="display:inline-block;padding:16px"></div></body></html>');
await page.addScriptTag({ path: mermaidScript });
await page.evaluate(() =>
  window.mermaid.initialize({
    startOnLoad: false,
    theme: 'base',
    flowchart: { htmlLabels: true, curve: 'basis', nodeSpacing: 36, rankSpacing: 44, useMaxWidth: false, wrappingWidth: 420 },
    sequence: { mirrorActors: false, useMaxWidth: false },
    themeVariables: {
      fontFamily: 'DM Sans, Helvetica, Arial, sans-serif',
      fontSize: '15px',
      primaryColor: '#fff4ec',
      primaryBorderColor: '#ff5a0a',
      primaryTextColor: '#1b1c20',
      lineColor: '#5b5f6b',
      secondaryColor: '#eef1f6',
      tertiaryColor: '#ffffff',
      clusterBkg: '#f7f8fa',
      clusterBorder: '#c9ced8',
    },
  }),
);

for (const file of sources) {
  const name = file.slice(0, -4);
  await page.setViewportSize({ width: 1600, height: 900 });
  const text = readFileSync(join(dir, file), 'utf8');
  const svg = await page.evaluate(async ([id, src]) => (await window.mermaid.render(id, src)).svg, [`d_${name.replace(/\W/g, '_')}`, text]);
  await page.evaluate((markup) => {
    document.getElementById('out').innerHTML = markup;
  }, svg);
  const box = await page.locator('#out').boundingBox();
  await page.setViewportSize({ width: Math.ceil(box.width) + 8, height: Math.ceil(box.height) + 8 });
  const png = await page.locator('#out').screenshot({ type: 'png' });
  writeFileSync(join(dir, `${name}.png`), png);
  console.log(`${name}.png  ${Math.round(box.width)}x${Math.round(box.height)}`);
}
await browser.close();
