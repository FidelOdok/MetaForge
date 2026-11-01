import { authClient } from 'metaforge:auth';
import { useHealth } from '../hooks/use-health';
import { getGatewayBase } from '../lib/gatewayConfig';
import { useAuth } from './AuthProvider';

/**
 * Decides whether the app needs a signed-in user, by asking the gateway.
 *
 * The dashboard is one static build serving two deployments, so this cannot be
 * a build-time flag. It reads `auth_mode` from `GET /health` — the running
 * gateway's own answer — and only then decides whether to show the app or a
 * sign-in screen.
 *
 * The bias is deliberately towards showing the app. If health has not loaded,
 * or the gateway is too old to report `auth_mode`, or the gateway is
 * unreachable, we render children. Blocking on an unanswered probe would put a
 * login wall in front of every local user whose gateway happens to be down,
 * and a local gateway will reject nothing anyway. The cost of being wrong in
 * this direction is a 401 from the gateway; in the other it is a dashboard
 * that cannot be used offline.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const { data: health, isLoading: healthLoading } = useHealth();
  const { status } = useAuth();

  const gatewayMode = health?.auth_mode;
  const gatewayRequiresAuth = Boolean(gatewayMode) && gatewayMode !== 'off';

  if (!gatewayRequiresAuth) {
    // Local gateway, unknown, or unreachable. Also covers the window before
    // the first health response — see the note above on which way to fail.
    return <>{children}</>;
  }

  // The gateway wants a token and this build cannot produce one. Say so
  // plainly: the alternative is an endless 401 loop with no explanation.
  const mismatch = describeMismatch(gatewayMode!);
  if (mismatch) {
    return <MisconfiguredBuild hint={mismatch} gatewayLabel={getGatewayBase() || 'same origin'} />;
  }

  if (status === 'loading' || healthLoading) {
    return <RestoringSession />;
  }

  if (status === 'signed-out') {
    const SignInView = authClient!.SignInView;
    return <SignInView gatewayLabel={getGatewayBase() || undefined} />;
  }

  return <>{children}</>;
}

/**
 * Why this build cannot satisfy `gatewayMode`, or `null` if it can.
 *
 * Three distinct ways to be unable to sign in, and conflating them costs an
 * operator real time: no provider compiled in at all, the wrong provider, or
 * the right provider without its credentials.
 */
function describeMismatch(gatewayMode: string): string | null {
  if (!authClient) {
    return (
      `This dashboard was built without an authentication provider, so it has no way ` +
      `to obtain a ${gatewayMode} token.`
    );
  }
  if (authClient.mode !== gatewayMode) {
    return (
      `This dashboard was built for ${authClient.mode} authentication, but the gateway ` +
      `is using ${gatewayMode}.`
    );
  }
  if (!authClient.isConfigured) {
    return authClient.configHint();
  }
  return null;
}

function RestoringSession() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-surface">
      <p className="text-[13px] text-on-surface-variant">Restoring session…</p>
    </div>
  );
}

function MisconfiguredBuild({ hint, gatewayLabel }: { hint: string; gatewayLabel: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-surface px-6">
      <div
        className="max-w-md rounded border p-5"
        style={{ borderColor: 'rgba(255,180,171,0.2)', background: 'rgba(255,180,171,0.06)' }}
      >
        <h1 className="text-sm font-semibold text-on-surface">Cannot sign in to this gateway</h1>
        <p className="mt-2 text-[13px] leading-relaxed text-on-surface-variant">
          The gateway at <code className="text-on-surface">{gatewayLabel}</code> is running with
          authentication enabled. {hint}
        </p>
        <p className="mt-3 text-[13px] leading-relaxed text-on-surface-variant">
          Either rebuild the dashboard against that provider, or point it at a gateway running{' '}
          <code className="text-on-surface">METAFORGE_AUTH_MODE=off</code> from Settings.
        </p>
      </div>
    </div>
  );
}
