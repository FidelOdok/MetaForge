import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect, type Frame, type Page } from '@playwright/test';

/**
 * The formgraph engine as Create hosts it (FORGE-826): the real engine files,
 * a product config embedded the way the gateway embeds it, in a scripts-only
 * sandboxed frame. Nothing here needs the gateway or the dashboard.
 */

const ENGINE = resolve(dirname(fileURLToPath(import.meta.url)), '../../api_gateway/imagine/engine');
const HOST = 'http://host.test';
const PAGE = 'http://engine.test';

const LAMP = {
  engine: 'formgraph@2',
  product: { id: 'desk-lamp', name: 'USB-C desk lamp' },
  form: { template: 'enclosure' },
  process: { id: 'im', material: 'abs' },
  power: { board: 'custom', battery: 'none', custom: { L: 60, W: 40, H: 15, P: 3, M: 8 } },
  functions: ['e_led', 'e_usbc', 'e_btn'],
};
const SHAPED = { ...LAMP, form: { template: 'enclosure', params: { W: 140, H: 40 } } };

function enginePage(config: object): string {
  const json = JSON.stringify(config).replace(/<\//g, '<\\/');
  return readFileSync(resolve(ENGINE, 'engine.html'), 'utf8')
    .replace('<script id="fg-config" type="application/json">{}</script>', () => `<script id="fg-config" type="application/json">${json}</script>`)
    .replace(/\{\{asset:([A-Za-z0-9_.-]+)\}\}/g, (_m, name: string) => `${PAGE}/assets/${name}`);
}

async function host(page: Page, config: object, width: number, height = 760): Promise<Frame> {
  await page.route(`${PAGE}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/engine') return route.fulfill({ contentType: 'text/html', body: enginePage(config) });
    const name = url.pathname.replace('/assets/', '');
    const file = ['engine.js', 'engine.css'].includes(name) ? resolve(ENGINE, name) : resolve(ENGINE, 'vendor', name);
    return route.fulfill({
      contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript',
      headers: { 'access-control-allow-origin': '*' },
      body: readFileSync(file, 'utf8'),
    });
  });
  await page.route(`${HOST}/**`, (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: `<!doctype html><body style="margin:0"><iframe id="f" sandbox="allow-scripts" style="border:0;width:${width}px;height:${height}px" src="${PAGE}/engine"></iframe>
<script>window.heights=[];addEventListener('message',e=>{if(e.data&&e.data.type==='fg:height')heights.push(e.data.px);});</script></body>`,
    }),
  );
  await page.setViewportSize({ width: width + 40, height: Math.max(height, 600) + 40 });
  await page.goto(`${HOST}/`);
  const frame = page.frames().find((f) => f.url().startsWith(PAGE));
  if (!frame) throw new Error('the engine frame did not load');
  await frame.waitForFunction(() => typeof (window as unknown as { __fgView?: unknown }).__fgView === 'function');
  await page.waitForTimeout(500);
  return frame;
}

type View = { w: number; h: number; aspect: number; shown: boolean; fits: boolean | null };
const view = (f: Frame) => f.evaluate(() => (window as unknown as { __fgView: () => View }).__fgView());

test.describe('the hosted engine', () => {
  test('with a config that sets no form, shows an empty state and no sample labels or geometry', async ({ page }) => {
    const f = await host(page, LAMP, 1000);
    await expect(f.locator('#viewEmpty')).toBeVisible();
    expect((await view(f)).shown).toBe(false);
    await expect(f.locator('#dims')).toBeHidden();
    await expect(f.locator('.pp summary span').first()).toHaveText('Shell');
    expect(await f.locator('body').innerText()).not.toMatch(/fuselage/i);
    // Shaping it here draws the part.
    await f.locator('#quick .rng').first().fill('120');
    await expect(f.locator('#viewEmpty')).toBeHidden();
    expect((await view(f)).shown).toBe(true);
  });

  test('frames the configured part and keeps it framed through a resize', async ({ page }) => {
    const f = await host(page, SHAPED, 1000);
    let v = await view(f);
    expect(v.shown).toBe(true);
    expect(v.fits).toBe(true);
    expect(v.h).toBeLessThanOrEqual(560);
    expect(v.aspect).toBeCloseTo(v.w / v.h, 2);
    await page.locator('#f').evaluate((el: HTMLIFrameElement) => (el.style.width = '620px'));
    await page.waitForTimeout(600);
    v = await view(f);
    expect(v.w).toBeLessThanOrEqual(620);
    expect(v.fits).toBe(true);
    expect(v.aspect).toBeCloseTo(v.w / v.h, 2);
  });

  test('lays out in two columns when wide and one below 900 px, with no sideways scroll', async ({ page }) => {
    const f = await host(page, SHAPED, 1000);
    const box = async (sel: string) => f.locator(sel).boundingBox();
    let g = await box('#graphPane');
    let w = await box('#viewPane');
    expect(g!.x).toBeLessThan(w!.x);
    expect(Math.abs(g!.y - w!.y)).toBeLessThan(2);
    await expect(f.locator('.brand')).toBeHidden();
    await page.locator('#f').evaluate((el: HTMLIFrameElement) => (el.style.width = '700px'));
    await page.waitForTimeout(400);
    g = await box('#graphPane');
    w = await box('#viewPane');
    expect(w!.y + w!.height).toBeLessThanOrEqual(g!.y + 1);
    expect(await f.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });

  test('asks its host for a bounded height that does not grow with the frame', async ({ page }) => {
    await host(page, SHAPED, 1000);
    const asked = async () => page.evaluate(() => (window as unknown as { heights: number[] }).heights.slice());
    const first = await asked();
    expect(first.length).toBeGreaterThan(0);
    const h = first[first.length - 1]!;
    expect(h).toBeGreaterThanOrEqual(480);
    expect(h).toBeLessThanOrEqual(960);
    await page.locator('#f').evaluate((el: HTMLIFrameElement, px) => (el.style.height = `${px}px`), h + 400);
    await page.waitForTimeout(600);
    const after = await asked();
    expect(after[after.length - 1]).toBe(h);
  });
});
