import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { LogOut, Settings, UserRound } from 'lucide-react';
import { useOptionalAuth } from '../../auth/AuthProvider';
import { logger } from '../../lib/logger';

/**
 * The account control in the top bar.
 *
 * Signed in (a gateway with an authentication provider): who you are, and a
 * way to sign out. Otherwise, as on every local gateway, it is the plain link
 * to Settings it always was: there is no account to show or leave.
 */
export function AccountMenu() {
  const auth = useOptionalAuth();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !ref.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', close);
    };
  }, [open]);

  if (!auth || auth.status !== 'signed-in') {
    return (
      <Link
        className="icon-control nav-account"
        to="/settings"
        aria-label="Account and API keys"
        title="Account and API keys"
      >
        <UserRound size={17} />
      </Link>
    );
  }

  const { user, signOut } = auth;
  const label = user?.email || 'Signed in';

  async function onSignOut() {
    setBusy(true);
    try {
      await signOut();
    } catch (err) {
      logger.error('auth_sign_out_failed', { error: String(err) });
    } finally {
      setBusy(false);
      setOpen(false);
    }
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        className="icon-control nav-account"
        aria-label={`Account: ${label}`}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <UserRound size={17} />
      </button>
      {open && (
        <div
          role="menu"
          aria-label="Account"
          className="absolute right-0 z-50 mt-2 w-60 rounded border bg-surface-container p-1 text-[13px] shadow-lg"
          style={{ borderColor: 'rgba(65,72,90,0.3)' }}
        >
          <p className="truncate px-3 py-2 text-on-surface-variant" title={label}>
            {label}
          </p>
          <Link
            role="menuitem"
            to="/settings"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 rounded px-3 py-2 text-on-surface hover:bg-surface-high"
          >
            <Settings size={14} aria-hidden="true" />
            Settings
          </Link>
          <button
            type="button"
            role="menuitem"
            onClick={onSignOut}
            disabled={busy}
            className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-on-surface hover:bg-surface-high"
          >
            <LogOut size={14} aria-hidden="true" />
            {busy ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      )}
    </div>
  );
}
