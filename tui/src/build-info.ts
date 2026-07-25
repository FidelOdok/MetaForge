/**
 * Build stamp. Committed with dev defaults; `npm run stamp` (run by the build /
 * bundle scripts) rewrites it from git + date so a shipped artifact reports
 * exactly which commit it was built from — the missing signal behind the
 * stale-CLI trap.
 */
export const BUILD = {
  version: "0.1.0",
  commit: "dev",
  date: "",
} as const;
