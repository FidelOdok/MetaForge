import type { Session, User } from '@supabase/supabase-js';
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { logger } from '../lib/logger';
import { isSupabaseConfigured, supabase } from '../lib/supabase';
import { setAccessToken } from './accessToken';

/**
 * Supabase session state for the dashboard.
 *
 * Mounted unconditionally, including on local builds with no Supabase project
 * configured. In that case it settles immediately into
 * `{ status: 'unconfigured' }` and does nothing else — the provider existing is
 * not the same as authentication being required, and `AuthGate` decides the
 * latter by asking the gateway.
 */

export type AuthStatus =
  /** Restoring a persisted session; we do not yet know if there is one. */
  | 'loading'
  /** This build has no Supabase credentials. Local deployments live here. */
  | 'unconfigured'
  | 'signed-in'
  | 'signed-out';

export interface AuthContextValue {
  status: AuthStatus;
  session: Session | null;
  user: User | null;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<{ needsConfirmation: boolean }>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<AuthStatus>(
    isSupabaseConfigured ? 'loading' : 'unconfigured',
  );

  useEffect(() => {
    if (!supabase) return;

    let active = true;

    const apply = (next: Session | null) => {
      if (!active) return;
      setSession(next);
      // Mirror into the module-level holder the Axios interceptor reads. Done
      // here rather than in a separate effect so the token can never lag the
      // session it came from.
      setAccessToken(next?.access_token ?? null);
      setStatus(next ? 'signed-in' : 'signed-out');
    };

    supabase.auth
      .getSession()
      .then(({ data }) => apply(data.session))
      .catch((err) => {
        logger.error('auth_session_restore_failed', { error: String(err) });
        apply(null);
      });

    // Fires for sign-in, sign-out, silent token refresh, and for a sign-out
    // performed in another tab.
    const { data: subscription } = supabase.auth.onAuthStateChange((event, next) => {
      logger.debug('auth_state_change', { event });
      apply(next);
    });

    return () => {
      active = false;
      subscription.subscription.unsubscribe();
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    if (!supabase) throw new Error('This dashboard build has no Supabase configuration.');
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) throw new Error(error.message);
  }, []);

  const signUp = useCallback(async (email: string, password: string) => {
    if (!supabase) throw new Error('This dashboard build has no Supabase configuration.');
    const { data, error } = await supabase.auth.signUp({ email, password });
    if (error) throw new Error(error.message);
    // With email confirmation on, Supabase returns a user but no session. The
    // caller has to say so rather than appearing to succeed into a blank app.
    return { needsConfirmation: Boolean(data.user) && !data.session };
  }, []);

  const signOut = useCallback(async () => {
    if (!supabase) return;
    const { error } = await supabase.auth.signOut();
    if (error) throw new Error(error.message);
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
