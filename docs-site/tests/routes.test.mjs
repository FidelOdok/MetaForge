import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const BUILD = join(dirname(fileURLToPath(import.meta.url)), '..', 'build');

/**
 * This site replaced a MkDocs Material build serving the same origin.
 *
 * Every path below was live at `https://fidelodok.github.io/MetaForge/...`
 * before the switch, and several are linked from places a docs build cannot
 * see: the dashboard's gateway-setup screen points at `/getting-started/`, the
 * console's own documentation page points at `/reference/openapi.json`, and
 * the README points at the root. A renamed file or a page dropped from the
 * sidebar breaks those silently — the build succeeds and the link 404s.
 *
 * So the list is checked against the built output, not against the config
 * that is supposed to produce it.
 */
const PUBLISHED = [
  '',
  'getting-started',
  'cli-reference',
  'dashboard-tour',
  'project-structure',
  'capability-matrix',
  'troubleshooting',
  'session-capture',
  'harness-codex-subscription',
  'knowledge/datasheet-ingestion',
  'research/simulator-landscape-catalog',
  'deployment/vercel',
  'deployment/cloud',
  'integrations/claude-code',
  'integrations/codex',
  'integrations/mcp-config-examples',
  'integrations/lightrag-ui',
  'reference/gateway-api',
  'architecture',
  'architecture/robust-harness-design',
  'architecture/design-flow-harness',
  'architecture/migrations',
  'roadmap',
  'skill_spec',
  'mcp_spec',
  'twin_schema',
  'testing-strategy',
  'governance',
];

/** Contributor / QA material that stays in the repo and off the site. */
const UNPUBLISHED = [
  'agents/electronics-context-spec',
  'plans/l1-implementation',
  'runbooks/gateway-down',
  'uat/kb-test-plan',
  'architecture/context-engineering',
  'architecture/neo4j-migration',
  'architecture/knowledge-ingestion-playbook',
];

test('build output exists', () => {
  assert.ok(
    existsSync(join(BUILD, 'index.html')),
    'no build/ — run `npm run build` before `npm test`',
  );
});

test('every previously published page still resolves', () => {
  const missing = PUBLISHED.filter((p) => !existsSync(join(BUILD, p, 'index.html')));
  assert.deepEqual(
    missing,
    [],
    `these URLs were live on the MkDocs site and are now 404s: ${missing.join(', ')}`,
  );
});

test('contributor-only pages are not published', () => {
  const leaked = UNPUBLISHED.filter((p) => existsSync(join(BUILD, p, 'index.html')));
  assert.deepEqual(leaked, [], `these should stay in-repo only: ${leaked.join(', ')}`);
});

test('the raw OpenAPI schema is served at the URL the console links to', () => {
  const spec = join(BUILD, 'reference', 'openapi.json');
  assert.ok(existsSync(spec), 'reference/openapi.json missing — did `npm run sync` run?');
  const parsed = JSON.parse(readFileSync(spec, 'utf-8'));
  assert.ok(parsed.openapi, 'served openapi.json is not an OpenAPI document');
  assert.ok(parsed.paths?.['/health'], 'served schema has no /health path');
});

test('the OpenAPI explorer renders', () => {
  assert.ok(existsSync(join(BUILD, 'reference', 'openapi', 'index.html')));
});

test('search is built, not silently skipped', () => {
  // The search plugin fails soft: no index means a search box that returns
  // nothing, which reads exactly like "the docs do not cover that".
  const indexes = readdirSync(BUILD).filter((f) => f.startsWith('search-index'));
  assert.ok(indexes.length > 0, 'no search-index-*.json in build/');
});

test('pages carry the docs styling, not a default Docusaurus shell', () => {
  const html = readFileSync(join(BUILD, 'getting-started', 'index.html'), 'utf-8');
  assert.match(html, /dx-sidebar-label/, 'sidebar furniture missing');
  assert.match(html, /dx-article-end/, 'article footer bar missing');
  assert.match(html, /ON THIS PAGE/, 'table-of-contents label missing');
});

test('the introduction keeps its landing-page blocks', () => {
  const html = readFileSync(join(BUILD, 'index.html'), 'utf-8');
  assert.match(html, /dx-start-links/);
  assert.match(html, /dx-loop/);
});
