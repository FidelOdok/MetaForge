# MetaForge documentation site

The renderer for [`../docs`](../docs). Published to GitHub Pages at
**https://fidelodok.github.io/MetaForge/** by
[`.github/workflows/docs.yml`](../.github/workflows/docs.yml) on every push to
`main` that touches `docs/` or `docs-site/`.

The markdown is **not** duplicated here. `docusaurus.config.js` points the docs
plugin at `../docs`, so contributors keep editing the files they already edit
and GitHub keeps rendering them in-repo.

## Working on it

```bash
cd docs-site
npm install
npm start          # http://localhost:3000/MetaForge/
```

Before opening a PR that touches `docs/`:

```bash
npm run build && npm test
```

`npm run build` is what CI runs. `onBrokenLinks` and `onBrokenMarkdownLinks`
are both `throw`, so it fails on a broken cross-reference the same way
`mkdocs build --strict` used to. `npm test` then checks the built output
against the list of URLs that were live before the MkDocs site was replaced —
see `tests/routes.test.mjs` for why that list is written down.

## What lives where

| Path | Purpose |
|---|---|
| `docusaurus.config.js` | Site config. The `UNPUBLISHED` list is the one that decides what stays in-repo. |
| `sidebars.js` | The nav. Every published page appears exactly once. |
| `src/css/custom.css` | The whole visual design, ported from the console's `/docs` surface. |
| `src/theme/` | Four swizzles: code-block card, TOC rail, article footer bar, sidebar furniture. |
| `scripts/sync-static.mjs` | Copies `../docs/reference/openapi.json` into `static/` so the raw URL keeps working. |
| `tests/routes.test.mjs` | URL-stability guard over `build/`. |

## Writing pages

Pages are plain CommonMark (`markdown.format: 'detect'`), so `<project-id>`
and `{project_id}` are safe to write inline. A page that needs components —
today only `docs/index.mdx` — uses the `.mdx` extension and gets MDX.

Admonitions use Docusaurus syntax:

```markdown
:::note[Optional title]

Body.

:::
```

## Regenerating the API reference

The gateway spec is generated from the live FastAPI app, not written by hand:

```bash
python scripts/gen_openapi.py     # from the repo root
```

Commit the regenerated `docs/reference/openapi.json`. The site picks it up on
the next build, serving it both as raw JSON and through the Redoc explorer at
`/reference/openapi/`.
