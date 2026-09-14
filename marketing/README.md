# MetaForge marketing site

The public landing page at the apex domain. A small Vite + React + Tailwind app,
deployed as its own Vercel project — separate from `dashboard/`, so a copy edit
does not rebuild the 3D viewer and the landing page does not carry its bundle.

```bash
npm install
npm run dev        # http://localhost:5174 (5173 belongs to the dashboard)
npm run build      # tsc -b && vite build → dist/
npm run preview    # serve the production build
npm test           # vitest
npm run lint
```

## Where the copy lives

All of it is in [`src/site.ts`](src/site.ts) — nav links, the pipeline steps, the
capability cards, the principles and the roadmap. Components render that data and
hold no prose of their own, so a copy change is a one-file edit.

## The claims test

[`src/__tests__/claims.test.tsx`](src/__tests__/claims.test.tsx) asserts the
scope claims that `CLAUDE.md` marks as things this project must never overstate:

- Phase 1 is 6–7 disciplines, never 12
- KiCad is read-only until Phase 2 — the site must never say MetaForge generates
  schematics
- the Phase 1 timeline is six months total, not three to four

A landing page is exactly where such claims drift, because it is written to
persuade and revised in a hurry. The test fails the build instead.

## Design system

Kinetic Console, shared with the dashboard. The tokens in
[`src/index.css`](src/index.css) mirror `dashboard/src/index.css` — keep the two
palettes in step so the site and the product look like one thing.

## Deployment

Vercel, root directory `marketing`, config in [`vercel.json`](vercel.json). Set
`VITE_DASHBOARD_URL` to wherever the dashboard is hosted; it is what the
"Open the dashboard" buttons point at, and it falls back to the docs site when
unset. Full instructions: [`docs/deployment/vercel.md`](../docs/deployment/vercel.md).
