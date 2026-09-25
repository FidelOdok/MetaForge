import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * The gate decides whether the dashboard needs a signed-in user, by asking the
 * gateway rather than assuming from build config.
 *
 * The assertion that matters most is the first one: a local user, on a gateway
 * with no authentication, must never be shown a login for an account that does
 * not exist and that they have no way to create. Getting that wrong breaks the
 * default experience for everyone running `docker compose up`.
 */

const mockHealth = vi.hoisted(() => vi.fn());
const mockSupabase = vi.hoisted(() => ({ configured: true }));

vi.mock('../../hooks/use-health', () => ({ useHealth: mockHealth }));
vi.mock('../../lib/supabase', () => ({
  get isSupabaseConfigured() {
    return mockSupabase.configured;
  },
  supabase: null,
  supabaseConfigHint: () => 'VITE_SUPABASE_URL is missing.',
}));

const mockAuth = vi.hoisted(() => ({ status: 'signed-out' as string }));
vi.mock('../AuthProvider', () => ({
  useAuth: () => mockAuth,
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock('../SignInPage', () => ({
  SignInPage: () => <div>SIGN IN SCREEN</div>,
}));

const { AuthGate } = await import('../AuthGate');

function health(data: unknown, isLoading = false) {
  mockHealth.mockReturnValue({ data, isLoading });
}

describe('AuthGate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSupabase.configured = true;
    mockAuth.status = 'signed-out';
  });

  it('renders the app on a local gateway, with no sign-in screen', () => {
    health({ auth_mode: 'off' });
    render(
      <AuthGate>
        <div>APP</div>
      </AuthGate>,
    );
    expect(screen.getByText('APP')).toBeInTheDocument();
    expect(screen.queryByText('SIGN IN SCREEN')).not.toBeInTheDocument();
  });

  it('renders the app when the gateway is too old to report auth_mode', () => {
    health({ status: 'healthy' });
    render(
      <AuthGate>
        <div>APP</div>
      </AuthGate>,
    );
    expect(screen.getByText('APP')).toBeInTheDocument();
  });

  it('renders the app while health is still loading', () => {
    // Biased towards showing the app: blocking here would put a login wall in
    // front of every local user whose gateway is briefly slow.
    health(undefined, true);
    render(
      <AuthGate>
        <div>APP</div>
      </AuthGate>,
    );
    expect(screen.getByText('APP')).toBeInTheDocument();
  });

  it('renders the app when the gateway is unreachable', () => {
    health(undefined);
    render(
      <AuthGate>
        <div>APP</div>
      </AuthGate>,
    );
    expect(screen.getByText('APP')).toBeInTheDocument();
  });

  it('shows the sign-in screen when the gateway requires auth', () => {
    health({ auth_mode: 'supabase' });
    render(
      <AuthGate>
        <div>APP</div>
      </AuthGate>,
    );
    expect(screen.getByText('SIGN IN SCREEN')).toBeInTheDocument();
    expect(screen.queryByText('APP')).not.toBeInTheDocument();
  });

  it('renders the app once signed in', () => {
    health({ auth_mode: 'supabase' });
    mockAuth.status = 'signed-in';
    render(
      <AuthGate>
        <div>APP</div>
      </AuthGate>,
    );
    expect(screen.getByText('APP')).toBeInTheDocument();
  });

  it('explains the mismatch rather than looping on 401s', () => {
    // Gateway wants a token; this build has no way to obtain one. Silence here
    // would be an unexplained wall of failed requests.
    health({ auth_mode: 'supabase' });
    mockSupabase.configured = false;
    render(
      <AuthGate>
        <div>APP</div>
      </AuthGate>,
    );
    expect(screen.getByText(/Cannot sign in to this gateway/i)).toBeInTheDocument();
    expect(screen.queryByText('SIGN IN SCREEN')).not.toBeInTheDocument();
  });
});
