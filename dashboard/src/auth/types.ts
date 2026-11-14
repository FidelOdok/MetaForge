import type { ComponentType } from 'react';

/**
 * Where an authentication implementation plugs into the dashboard (FORGE-540).
 *
 * The mirror of `api_gateway/auth/provider.py` on the browser side. The
 * dashboard knows that a gateway *may* demand a token, what a signed-in user
 * looks like, and what to render while there isn't one. It does not know how
 * any particular identity provider issues a session.
 *
 * An implementation is supplied at build time: `metaforge:auth` resolves to
 * `src/auth/none.ts` — no client, local-only — unless the build points
 * `METAFORGE_AUTH_MODULE` at a module exporting one. The hosted implementation
 * ships separately with MetaForge Cloud.
 */

/** The signed-in user, reduced to what the dashboard actually reads. */
export interface AuthUser {
  id: string;
  email?: string | null;
}

/** A live session, with the token the API client attaches to every request. */
export interface AuthSession {
  accessToken: string;
  user: AuthUser;
}

/** Why the user is looking at the sign-in screen, when it is not obvious. */
export type SignedOutReason =
  /** The gateway stopped accepting the session's token mid-use. */
  'expired';

export type AuthStatus =
  /** Restoring a persisted session; we do not yet know if there is one. */
  | 'loading'
  /** This build carries no credentials for its provider. Local lives here. */
  | 'unconfigured'
  | 'signed-in'
  | 'signed-out';

export interface AuthClient {
  /**
   * The gateway `auth_mode` this client answers to.
   *
   * Compared against what the running gateway reports, so that a dashboard
   * built for one provider and pointed at a gateway using another says so
   * instead of failing every request with an unexplained 401.
   */
  readonly mode: string;

  /** False when the build is missing the provider's own configuration. */
  readonly isConfigured: boolean;

  /** Why it is not configured, phrased for the user. Empty when it is. */
  configHint(): string;

  /** Restore a persisted session, if there is one. */
  restore(): Promise<AuthSession | null>;

  /**
   * Observe session changes — sign-in, sign-out, silent token refresh, and a
   * sign-out performed in another tab. Returns an unsubscribe function.
   */
  subscribe(onChange: (session: AuthSession | null) => void): () => void;

  signIn(email: string, password: string): Promise<void>;

  /** `needsConfirmation` when the provider created a user but no session. */
  signUp(email: string, password: string): Promise<{ needsConfirmation: boolean }>;

  signOut(): Promise<void>;

  /**
   * Get a fresh session now, or `null` when the session cannot be renewed.
   *
   * Optional. A provider refreshes its tokens on a timer, but a browser
   * throttles timers in a background tab, so an access token can expire
   * before the refresh runs. The API client calls this once on a 401 and
   * retries the request with the new token before it ends the session.
   */
  refresh?(): Promise<AuthSession | null>;

  /** The sign-in screen. Owned by the client because its copy and its fields
   *  are provider-specific. */
  SignInView: ComponentType<{ gatewayLabel?: string; reason?: SignedOutReason }>;
}
