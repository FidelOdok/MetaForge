import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AuthClient } from '../types';

/**
 * The gate decides whether the dashboard needs a signed-in user, by asking the
 * gateway rather than assuming from build config.
 *
 * The assertion that matters most is the first one: a local user, on a gateway
 * with no authentication, must never be shown a login for an account that does
 * not exist and that they have no way to create. Getting that wrong breaks the
 * default experience for everyone running `docker compose up`.
 *
 * Everything here runs against a stand-in client, never a real provider — the
 * gate is open-core machinery and must not learn about any particular one.
 */

const mockHealth = vi.hoisted(() => vi.fn());
vi.mock('../../hooks/use-health', () => ({ useHealth: mockHealth }));

const fake = vi.hoisted(() => ({
  client: null as AuthClient | null,
}));
vi.mock('metaforge:auth', () => ({
  get authClient() {
    return fake.client;
  },
}));

const mockAuth = vi.hoisted(() => ({ status: 'signed-out' as string }));
vi.mock('../AuthProvider', () => ({
  useAuth: () => mockAuth,
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

const { AuthGate } = await import('../AuthGate');

function health(data: unknown, isLoading = false) {
  mockHealth.mockReturnValue({ data, isLoading });
}

function client(overrides: Partial<AuthClient> = {}): AuthClient {
  return {
    mode: 'stand-in',
    isConfigured: true,
    configHint: () => 'STAND-IN HINT',
    restore: async () => null,
    subscribe: () => () => {},
    signIn: async () => {},
    signUp: async () => ({ needsConfirmation: false }),
    signOut: async () => {},
    SignInView: () => <div>SIGN IN SCREEN</div>,
    ...overrides,
  };
}

function app() {
  return render(
    <AuthGate>
      <div>APP</div>
    </AuthGate>,
  );
}

describe('AuthGate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fake.client = client();
    mockAuth.status = 'signed-out';
  });

  it('renders the app on a local gateway, with no sign-in screen', () => {
    health({ auth_mode: 'off' });
    app();
    expect(screen.getByText('APP')).toBeInTheDocument();
    expect(screen.queryByText('SIGN IN SCREEN')).not.toBeInTheDocument();
  });

  it('renders the app when the gateway is too old to report auth_mode', () => {
    health({ status: 'healthy' });
    app();
    expect(screen.getByText('APP')).toBeInTheDocument();
  });

  it('renders the app while health is still loading', () => {
    // Biased towards showing the app: blocking here would put a login wall in
    // front of every local user whose gateway is briefly slow.
    health(undefined, true);
    app();
    expect(screen.getByText('APP')).toBeInTheDocument();
  });

  it('renders the app when the gateway is unreachable', () => {
    health(undefined);
    app();
    expect(screen.getByText('APP')).toBeInTheDocument();
  });

  it('shows the sign-in screen when the gateway requires auth', () => {
    health({ auth_mode: 'stand-in' });
    app();
    expect(screen.getByText('SIGN IN SCREEN')).toBeInTheDocument();
    expect(screen.queryByText('APP')).not.toBeInTheDocument();
  });

  it('renders the app once signed in', () => {
    health({ auth_mode: 'stand-in' });
    mockAuth.status = 'signed-in';
    app();
    expect(screen.getByText('APP')).toBeInTheDocument();
  });

  it('explains a build with no auth provider at all', () => {
    // The open-core default pointed at a gateway that wants tokens. Silence
    // here would be an unexplained wall of failed requests.
    health({ auth_mode: 'stand-in' });
    fake.client = null;
    app();
    expect(screen.getByText(/Cannot sign in to this gateway/i)).toBeInTheDocument();
    expect(screen.getByText(/without an authentication provider/i)).toBeInTheDocument();
    expect(screen.queryByText('SIGN IN SCREEN')).not.toBeInTheDocument();
  });

  it('explains a build carrying the wrong provider', () => {
    health({ auth_mode: 'some-other-provider' });
    app();
    expect(screen.getByText(/built for stand-in authentication/i)).toBeInTheDocument();
  });

  it('explains the right provider missing its credentials', () => {
    health({ auth_mode: 'stand-in' });
    fake.client = client({ isConfigured: false });
    app();
    expect(screen.getByText(/STAND-IN HINT/)).toBeInTheDocument();
  });
});
