import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Mermaid diagrams must not clip their own labels.
 *
 * This is checked in a real browser because it cannot be checked anywhere
 * else. Mermaid renders client-side, so the built HTML contains an empty
 * placeholder; and the failure is geometric — the diagram is present, the
 * text is present, the box is simply too short for it.
 *
 * The bug this exists for: mermaid measures each label, then draws it into a
 * `foreignObject` sized to that measurement. The label is real HTML, so the
 * article's `line-height: 1.85` and `font-size: 16px` applied to text that
 * had been measured at `line-height: 1.5`. Eight nodes on the introduction
 * page rendered taller than their boxes and were clipped mid-word. Nothing
 * failed: the build was green, the page was green, the diagram was wrong.
 *
 * Asserting that the CSS reset exists would only restate the fix. This
 * measures the rendered result instead, which is the thing that was broken.
 *
 * If Chrome is unavailable this FAILS rather than skips. A skipped visual
 * check is indistinguishable from a passing one, which is the trap that let
 * a bun regression test sit unrun in CI for months (FORGE-308).
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const BUILD = join(HERE, '..', 'build');

/** Every page that contains a ```mermaid fence, as a built URL path. */
const PAGES = ['/', '/getting-started/', '/architecture/', '/dashboard-tour/', '/project-structure/'];

const CHROME =
  process.env.CHROME_PATH ??
  ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium'].find(existsSync);

const TYPES = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
};

let server;
let origin;
let chrome;
let ws;
let nextId = 1;

/** Serve build/ the way GitHub Pages does: /foo/ -> build/foo/index.html. */
function startServer() {
  return new Promise((resolve) => {
    server = createServer(async (req, res) => {
      let path = decodeURIComponent(new URL(req.url, 'http://x').pathname);
      path = path.replace(/^\/MetaForge/, '') || '/';
      if (path.endsWith('/')) path += 'index.html';
      const file = join(BUILD, normalize(path).replace(/^(\.\.[/\\])+/, ''));
      try {
        const body = await readFile(file);
        res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' });
        res.end(body);
      } catch {
        res.writeHead(404).end('not found');
      }
    });
    server.listen(0, '127.0.0.1', () => {
      origin = `http://127.0.0.1:${server.address().port}`;
      resolve();
    });
  });
}

/** Resolve when `method` arrives on `sessionId`. */
function waitFor(method, sessionId, timeoutMs = 30_000) {
  return new Promise((resolve, reject) => {
    const onMessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.method !== method || msg.sessionId !== sessionId) return;
      ws.removeEventListener('message', onMessage);
      clearTimeout(timer);
      resolve(msg.params);
    };
    const timer = setTimeout(() => {
      ws.removeEventListener('message', onMessage);
      reject(new Error(`${method} never fired`));
    }, timeoutMs);
    ws.addEventListener('message', onMessage);
  });
}

function send(method, params = {}, sessionId) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params, sessionId }));
  return new Promise((resolve, reject) => {
    const onMessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id !== id) return;
      ws.removeEventListener('message', onMessage);
      msg.error ? reject(new Error(`${method}: ${msg.error.message}`)) : resolve(msg.result);
    };
    ws.addEventListener('message', onMessage);
    setTimeout(() => reject(new Error(`${method} timed out`)), 90_000);
  });
}

async function startChrome() {
  const port = 9222 + (process.pid % 500);
  chrome = spawn(
    CHROME,
    [
      '--headless=new',
      '--disable-gpu',
      '--no-sandbox',
      '--hide-scrollbars',
      `--remote-debugging-port=${port}`,
      '--window-size=1440,1200',
      'about:blank',
    ],
    { stdio: 'ignore' },
  );

  // Poll for the DevTools endpoint rather than sleeping a fixed interval.
  const deadline = Date.now() + 30_000;
  let target;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/version`);
      target = (await res.json()).webSocketDebuggerUrl;
      break;
    } catch {
      await new Promise((r) => setTimeout(r, 200));
    }
  }
  assert.ok(target, 'Chrome never exposed a DevTools endpoint');

  ws = new WebSocket(target);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', reject, { once: true });
  });
}

/**
 * Load a page, wait for mermaid to draw, and measure every label against the
 * box it was drawn into.
 */
async function measure(path) {
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
  // A diagram that never appears is usually a chunk that never executed,
  // and the DOM afterwards looks exactly like a page with no fence in it.
  // Collecting the page's own exceptions is the difference between a flake
  // report and a cause.
  const pageErrors = [];
  const onException = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.sessionId !== sessionId) return;
    if (msg.method === 'Runtime.exceptionThrown') {
      const d = msg.params?.exceptionDetails;
      pageErrors.push(d?.exception?.description || d?.text || 'unknown exception');
    }
  };
  ws.addEventListener('message', onException);
  try {
    await send('Page.enable', {}, sessionId);
    await send('Runtime.enable', {}, sessionId);
    // `Page.navigate` resolves when navigation *starts*. Evaluating straight
    // after it runs against the previous (about:blank) execution context,
    // which has no diagram and never will — the first version of this test
    // reported "no diagram rendered" on every page for exactly that reason.
    // The URL has to carry the site's baseUrl. Served at bare `/`, the
    // client router decides the path is outside the site, never hydrates,
    // and mermaid never runs — which looks identical to a missing diagram.
    const loaded = waitFor('Page.loadEventFired', sessionId);
    await send('Page.navigate', { url: `${origin}/MetaForge${path}` }, sessionId);
    await loaded;

    const expression = `
      new Promise((resolve) => {
        const deadline = Date.now() + 60000;
        const poll = () => {
          const labels = [...document.querySelectorAll(
            '.docusaurus-mermaid-container foreignObject'
          )];
          if (!labels.length) {
            if (Date.now() > deadline) {
              // Distinguish the two ways this ends up empty. A missing
              // container means mermaid never ran at all — a fence that
              // stopped being a diagram, which is a different bug from a
              // diagram that is merely slow.
              const container = document.querySelector('.docusaurus-mermaid-container');
              return resolve({
                error: container
                  ? 'diagram container present but drew no labels within 45s'
                  : 'no .docusaurus-mermaid-container on the page — the mermaid '
                    + 'fence did not become a diagram',
              });
            }
            return setTimeout(poll, 250);
          }
          resolve({
            labels: labels.map((fo) => {
              const inner = fo.firstElementChild;
              return {
                text: (inner.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 48),
                boxHeight: fo.height.baseVal.value,
                contentHeight: inner.scrollHeight,
                boxWidth: fo.width.baseVal.value,
                contentWidth: inner.scrollWidth,
              };
            }),
          });
        };
        // Wait for hydration before starting the clock. Mermaid runs from
        // React, so polling a server-rendered shell just burns the deadline
        // and then reports "the fence did not become a diagram" — which is
        // the one message here that should only ever mean a real regression.
        //
        // This check used to look for .theme-doc-markdown and
        // #__docusaurus, both of which Docusaurus server-renders into the
        // HTML. It was therefore true on the very first tick, before any
        // client JS had run, and the wait did nothing — which is how this
        // test came to fail intermittently on a page whose diagram was fine.
        // window.docusaurus is set by the client entry bundle, so it is
        // true only once React is actually driving the page.
        // (No backticks in here: this whole block is a template literal.)
        const hydrated = () =>
          typeof window.docusaurus !== 'undefined' &&
          document.querySelector('.theme-doc-markdown') !== null;
        const whenHydrated = (attempt = 0) => {
          if (hydrated() || attempt > 300) return Promise.resolve();
          return new Promise((r) => setTimeout(r, 100)).then(() => whenHydrated(attempt + 1));
        };

        whenHydrated()
          // Fonts settle next: mermaid measures with whatever is loaded, so
          // a late webfont is itself a way this breaks.
          .then(() => (document.fonts ? document.fonts.ready : Promise.resolve()))
          .then(() => setTimeout(poll, 400));
      })
    `;
    const { result } = await send(
      'Runtime.evaluate',
      { expression, awaitPromise: true, returnByValue: true },
      sessionId,
    );
    const value = result.value;
    if (value.error && pageErrors.length) {
      value.error += ` (page threw: ${pageErrors.join('; ')})`;
    }
    return value;
  } finally {
    ws.removeEventListener('message', onException);
    await send('Target.closeTarget', { targetId });
  }
}

before(async () => {
  assert.ok(
    existsSync(join(BUILD, 'index.html')),
    'no build/ — run `npm run build` before `npm test`',
  );
  assert.ok(
    CHROME,
    'no Chrome found. Set CHROME_PATH. This check fails rather than skips on ' +
      'purpose: a visual test that quietly does not run reads exactly like one that passes.',
  );
  await startServer();
  await startChrome();
});

after(async () => {
  ws?.close();
  chrome?.kill();
  server?.close();
});

for (const path of PAGES) {
  test(`diagrams on ${path} fit inside their boxes`, async () => {
    let measured = await measure(path);
    if (measured.error) {
      // One retry, in a fresh target. A stylesheet regression — the thing
      // this test exists to catch — fails both attempts; a chunk that lost
      // a race on a loaded machine does not. The retry is announced so a
      // page that needs it every time is visible rather than silently
      // absorbed.
      console.error(`retrying ${path} after: ${measured.error}`);
      measured = await measure(path);
    }
    assert.ok(!measured.error, `${path}: ${measured.error}`);
    assert.ok(measured.labels.length > 0, `${path}: no labels measured`);

    // 1px of tolerance for sub-pixel rounding; the real failure overflowed
    // by a whole line, not a fraction of one.
    const clipped = measured.labels.filter(
      (l) => l.contentHeight > l.boxHeight + 1 || l.contentWidth > l.boxWidth + 1,
    );

    assert.deepEqual(
      clipped.map((l) => `"${l.text}" needs ${l.contentHeight}px in a ${l.boxHeight}px box`),
      [],
      `${path}: mermaid labels are being clipped — something in the article ` +
        `stylesheet is changing text metrics inside .docusaurus-mermaid-container`,
    );
  });
}
