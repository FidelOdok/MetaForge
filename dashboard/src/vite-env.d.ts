/// <reference types="vite/client" />

/**
 * Build-time configuration.
 *
 * Declared explicitly rather than relying on Vite's `[key: string]: any`
 * index signature, so a typo in a variable name is a type error rather than
 * a silent `undefined` at runtime.
 *
 * All are optional: the default local build sets none of them.
 */
interface ImportMetaEnv {
  /** Gateway to use when the user has not configured one in Settings. */
  readonly VITE_GATEWAY_URL?: string;
  /** Supabase project URL. Required only for gateways with auth enabled. */
  readonly VITE_SUPABASE_URL?: string;
  /** Supabase anon/publishable key. Safe to ship — RLS is the boundary. */
  readonly VITE_SUPABASE_ANON_KEY?: string;
  /** Opt-in Grafana Faro real-user monitoring. */
  readonly VITE_RUM_ENABLED?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
