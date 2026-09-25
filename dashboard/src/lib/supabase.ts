import { createClient, type SupabaseClient } from '@supabase/supabase-js';

/**
 * The Supabase client, or `null` when the dashboard was built without one.
 *
 * Null is a supported, common state — not an error. The dashboard is a single
 * static build that has to serve two very different deployments:
 *
 *   - a laptop running `docker compose up`, pointed at a local gateway with
 *     `auth_mode: off`. There is no Supabase project, no account, nothing to
 *     sign into, and requiring configuration here would break the default
 *     local experience for everyone.
 *   - the hosted dashboard, pointed at a gateway running `auth_mode: supabase`.
 *
 * So configuration is optional at build time, and whether it is *needed* is
 * decided at runtime by asking the gateway (`GET /health` → `auth_mode`). The
 * mismatch that matters — gateway wants auth, dashboard cannot provide it — is
 * surfaced to the user by `AuthGate` rather than failing here, because a
 * module-load throw in a static SPA is a blank page with no explanation.
 */

const url = import.meta.env.VITE_SUPABASE_URL?.trim();
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY?.trim();

export const isSupabaseConfigured = Boolean(url && anonKey);

export const supabase: SupabaseClient | null = isSupabaseConfigured
  ? createClient(url!, anonKey!, {
      auth: {
        // Survive a refresh, and pick up a sign-out performed in another tab.
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  : null;

/** Human-readable reason the client is absent, for the UI to show. */
export function supabaseConfigHint(): string {
  const missing = [
    !url && 'VITE_SUPABASE_URL',
    !anonKey && 'VITE_SUPABASE_ANON_KEY',
  ].filter(Boolean);
  return missing.length
    ? `This dashboard build is missing ${missing.join(' and ')}.`
    : '';
}
