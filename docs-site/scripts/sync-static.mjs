/**
 * Copy build-time inputs that live outside `docs-site/` into `static/`.
 *
 * Only `openapi.json` so far. It is generated from the live FastAPI app by
 * `python scripts/gen_openapi.py` and committed under `docs/reference/`, and
 * the published site has to keep serving it verbatim at
 * `/MetaForge/reference/openapi.json` — the console's docs page links straight
 * to that URL, and Redoc reads it at runtime.
 *
 * Copying rather than committing a second copy: two files would drift, and the
 * one nobody looks at would be the one the site serves.
 */
import { copyFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');

const COPIES = [['../docs/reference/openapi.json', 'static/reference/openapi.json']];

for (const [from, to] of COPIES) {
  const src = join(root, from);
  const dest = join(root, to);
  if (!existsSync(src)) {
    console.error(`sync-static: missing ${from} — run \`python scripts/gen_openapi.py\` first.`);
    process.exit(1);
  }
  mkdirSync(dirname(dest), { recursive: true });
  copyFileSync(src, dest);
  console.log(`sync-static: ${from} -> ${to}`);
}
