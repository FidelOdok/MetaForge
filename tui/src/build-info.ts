/**
 * Build stamp. Committed with dev defaults; `npm run stamp` (run by the bundle
 * scripts) rewrites it from git + date so a shipped binary reports exactly which
 * commit it was built from — the missing signal behind the stale-CLI trap.
 */
export const BUILD = {
  version: "0.3.91",
  commit: "d605cc1",
  date: "2026-08-07",
} as const;
