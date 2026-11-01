# MetaForge dashboard docs

Docusaurus site: per-page reference for the MetaForge engineering workspace
(`../dashboard/`). Lives in-repo
so pushing the parent carries it.

System architecture truth stays in `../docs/` (MkDocs). This site links
there and never duplicates it.

## Installation

```bash
npm install
```

## Local Development

```bash
npm run start
```

## Build

```bash
npm run build
```

## Deploy

Dedicated Vercel project (same pattern as `dashboard/` + `marketing/`, see
`../docs/deployment/vercel.md`). If deploying to GitHub Pages under the main
site instead, set `baseUrl: '/MetaForge/dashboard-docs/'` in
`docusaurus.config.ts`.
