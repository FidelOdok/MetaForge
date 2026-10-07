import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { authClient } from 'metaforge:auth';
import { logger } from '../lib/logger';
import { setAccessToken } from './accessToken';
import type { AuthSession, AuthStatus, AuthUser } from './types';

/**
 * Session state for the dashboard, from whichever auth client this build has.
 *
 * Mounted unconditionally, including on local builds with no client at all
 * (`metaforge:auth` → `src/auth/none.ts`). In that case it settles immediately
 * into `{ status: 'unconfigured' }` and does nothing else — the provider
 * existing is not the same as authentication being required, and `AuthGate`
 * decides the latter by asking the gateway.
 */

export type { AuthStatus } from './types';

export interface AuthContextValue {
  status: AuthStatus;
  session: AuthSession | null;
  user: AuthUser | null;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<{ needsConfirmation: boolean }>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const NO_CLIENT = 'This dashboard build has no authentication provider.';

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [status, setStatus] = useState<AuthStatus>(
    authClient?.isConfigured ? 'loading' : 'unconfigured',
  );

  useEffect(() => {
    if (!authClient?.isConfigured) return;
    const client = authClient;

    let active = true;

    const apply = (next: AuthSession | null) => {
      if (!active) return;
      setSession(next);
      // Mirror into the module-level holder the Axios interceptor reads. Done
      // here rather than in a separate effect so the token can never lag the
      // session it came from.
      setAccessToken(next?.accessToken ?? null);
      setStatus(next ? 'signed-in' : 'signed-out');
    };

    client
      .restore()
      .then(apply)
      .catch((err) => {
        logger.error('auth_session_restore_failed', { error: String(err) });
        apply(null);
      });

    // Fires for sign-in, sign-out, silent token refresh, and for a sign-out
    // performed in another tab.
    const unsubscribe = client.subscribe(apply);

    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    if (!authClient) throw new Error(NO_CLIENT);
    await authClient.signIn(email, password);
  }, []);

  const signUp = useCallback(async (email: string, password: string) => {
    if (!authClient) throw new Error(NO_CLIENT);
    return authClient.signUp(email, password);
  }, []);

  const signOut = useCallback(async () => {
    if (!authClient) return;
    await authClient.signOut();
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, session, user: session?.user ?? null, signIn, signUp, signOut }),
    [status, session, signIn, signUp, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider');
  return ctx;
}
