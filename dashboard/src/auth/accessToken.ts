/**
 * The current access token, held outside React.
 *
 * The Axios request interceptor needs the token synchronously, on every
 * request. Supabase's own `getSession()` is async, and making the interceptor
 * async would put a promise in front of every API call in the app. Reading
 * from React state is not an option either — the interceptor is a plain
 * module-level function with no access to a hook.
 *
 * So `AuthProvider` mirrors the session's token here whenever it changes,
 * including on Supabase's own silent refresh, and the interceptor reads it.
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
