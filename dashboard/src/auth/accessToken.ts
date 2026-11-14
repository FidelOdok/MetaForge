/**
 * The current access token, held outside React.
 *
 * The Axios request interceptor needs the token synchronously, on every
 * request. Session restore is async in every auth client we expect, and making
 * the interceptor async would put a promise in front of every API call in the
 * app. Reading from React state is not an option either — the interceptor is a
 * plain module-level function with no access to a hook.
 *
 * So `AuthProvider` mirrors the session's token here whenever it changes,
 * including on a provider's own silent refresh, and the interceptor reads it.
 * `null` means "no session", which is the correct state for a local gateway.
 */

let current: string | null = null;

/** Called by `AuthProvider` on every auth state change. */
export function setAccessToken(token: string | null): void {
  current = token;
}

/** The current bearer token, or `null`. */
export function getAccessToken(): string | null {
  return current;
}

/**
 * Who to tell when the gateway rejects a token we sent (HTTP 401).
 *
 * A provider refreshes its own tokens, so a 401 on a request that carried one
 * means the session is over once a refresh (below) has also failed: revoked,
 * expired past refresh, or the account was removed. `AuthProvider` registers here and signs the user out with the
 * reason, rather than leaving every panel to fail on its own.
 */
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

/** Called by the API client on a 401 to a request that carried a token. */
export function notifyUnauthorized(): void {
  onUnauthorized?.();
}

/**
 * How to renew the session when the gateway says a token has expired.
 *
 * A browser throttles timers in a background tab, so a provider's scheduled
 * refresh can run late and a request goes out with a token that just expired.
 * The API client asks for a fresh one here before it ends the session.
 * Concurrent callers share one refresh.
 */
let refresher: (() => Promise<string | null>) | null = null;
let inFlight: Promise<string | null> | null = null;

export function setTokenRefresher(fn: (() => Promise<string | null>) | null): void {
  refresher = fn;
  inFlight = null;
}

/** A fresh token, or `null` when there is no refresher or it could not renew. */
export function refreshAccessToken(): Promise<string | null> {
  if (!refresher) return Promise.resolve(null);
  if (!inFlight) {
    const run = refresher;
    inFlight = run()
      .catch(() => null)
      .finally(() => {
        inFlight = null;
      });
  }
  return inFlight;
}
