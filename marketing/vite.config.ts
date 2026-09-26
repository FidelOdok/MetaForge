import { defineConfig } from 'vite';

/**
 * The marketing site is static HTML, CSS and classic scripts — no framework.
 *
 * Everything the browser loads lives in `public/` rather than being imported
 * from `index.html`, which is deliberate: the four scripts are classic
 * `<script src>` tags that share state through `window` (see
 * `cross-visuals.js` reading `window.refreshCrossVisuals`). Bundling them as
 * ES modules would scope those globals away and break the page silently.
 * Vite copies `public/` verbatim, so they ship exactly as written.
 *
 * Vite is kept for the dev server and for `npm run build` producing the
 * `dist/` that vercel.json expects — not for transforming anything.
 */
export default defineConfig({
  server: {
    port: 5174, // 5173 belongs to the dashboard dev server
    watch: {
      // WSL2: inotify events don't cross the Windows <-> Linux boundary.
      usePolling: !!process.env.CHOKIDAR_USEPOLLING,
      interval: 1000,
    },
  },
});
