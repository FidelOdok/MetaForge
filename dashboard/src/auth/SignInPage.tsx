import { useState } from 'react';
import { Button } from '../components/ui/Button';
import { useAuth } from './AuthProvider';

type Mode = 'sign-in' | 'sign-up';

/**
 * Sign-in / sign-up for a gateway running `auth_mode: supabase`.
 *
 * Shown instead of the app shell, not inside it: an unauthenticated user can
 * reach no data, so a nav rail and breadcrumbs around an empty page would be
 * furniture pretending to be a product.
 */
export function SignInPage({ gatewayLabel }: { gatewayLabel?: string }) {
  const { signIn, signUp } = useAuth();
  const [mode, setMode] = useState<Mode>('sign-in');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === 'sign-in') {
        await signIn(email, password);
      } else {
        const { needsConfirmation } = await signUp(email, password);
        if (needsConfirmation) {
          setNotice(`Check ${email} for a confirmation link, then sign in.`);
          setMode('sign-in');
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const inputClass =
    'w-full rounded border bg-surface-lowest px-3 py-2 text-sm text-on-surface ' +
    'placeholder:text-on-surface-variant focus:outline-none focus:border-primary-container';

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface px-6">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center gap-2.5">
          <img
            src="/logo/metaforge-mark-dark.svg"
            alt=""
            aria-hidden="true"
            width={28}
            height={28}
            className="h-7 w-auto"
          />
          <span className="text-[15px] font-semibold tracking-tight text-on-surface">
            MetaForge
          </span>
        </div>

        <h1 className="text-xl font-semibold tracking-tight text-on-surface">
          {mode === 'sign-in' ? 'Sign in' : 'Create an account'}
        </h1>
        <p className="mt-1.5 text-[13px] text-on-surface-variant">
          {gatewayLabel
            ? `This gateway (${gatewayLabel}) requires an account.`
            : 'This gateway requires an account.'}
        </p>

        <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs text-on-surface-variant">Email</span>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={inputClass}
              style={{ borderColor: 'rgba(65,72,90,0.3)' }}
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-xs text-on-surface-variant">Password</span>
            <input
              type="password"
              required
              minLength={8}
              autoComplete={mode === 'sign-in' ? 'current-password' : 'new-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={inputClass}
              style={{ borderColor: 'rgba(65,72,90,0.3)' }}
            />
          </label>

          {error && (
            <p role="alert" className="text-[13px] text-error">
              {error}
            </p>
          )}
          {notice && (
            <p role="status" className="text-[13px] text-success">
              {notice}
            </p>
          )}

          <Button type="submit" size="lg" disabled={busy} className="mt-1 w-full">
            {busy ? 'Working…' : mode === 'sign-in' ? 'Sign in' : 'Create account'}
          </Button>
        </form>

        <button
          type="button"
          onClick={() => {
            setMode(mode === 'sign-in' ? 'sign-up' : 'sign-in');
            setError(null);
            setNotice(null);
          }}
          className="mt-4 text-[13px] text-on-surface-variant transition-colors hover:text-on-surface"
        >
          {mode === 'sign-in'
            ? 'No account? Create one'
            : 'Already have an account? Sign in'}
        </button>
      </div>
    </div>
  );
}
