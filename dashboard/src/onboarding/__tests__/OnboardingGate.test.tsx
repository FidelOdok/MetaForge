import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * The gate decides whether a visitor has ever had a working gateway.
 *
 * The condition looks obvious and is not. "No gateway configured" is the
 * tempting test, but an empty base means *same-origin relative paths* — which
 * is exactly how the Docker image and `npm run dev` are meant to work, via
 * their nginx and Vite proxies. Gating on that would show a setup wizard to
 * every local user for whom everything already works, which is why the
 * condition is behavioural (health failed) rather than declarative.
 *
 * The other half matters as much: someone who *has* configured a gateway must
 * never be trapped here when it goes down, because the wizard is not the page
 * with their other settings on it.
 */

const mockHealth = vi.hoisted(() => vi.fn());
const mockConfigured = vi.hoisted(() => ({ value: false }));

vi.mock('../../hooks/use-health', () => ({ useHealth: mockHealth }));
vi.mock('../../lib/gatewayConfig', () => ({
  isGatewayUserConfigured: () => mockConfigured.value,
}));
vi.mock('../GatewaySetup', () => ({
  GatewaySetup: () => <div>SETUP SCREEN</div>,
}));

const { OnboardingGate } = await import('../OnboardingGate');

function renderGate() {
  return render(
    <OnboardingGate>
      <div>APP</div>
    </OnboardingGate>,
  );
}

describe('OnboardingGate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockConfigured.value = false;
  });

  it('shows setup when nothing has ever worked and nothing was chosen', () => {
    // The app.metaforge.uk visitor: no gateway, no history, no idea why the
    // dashboard is empty.
    mockHealth.mockReturnValue({ isError: true, isLoading: false });
    renderGate();
    expect(screen.getByText('SETUP SCREEN')).toBeInTheDocument();
  });

  it('does not trap a user whose configured gateway is merely down', () => {
    // They have somewhere to go — Settings — and the wizard is not it.
    mockHealth.mockReturnValue({ isError: true, isLoading: false });
    mockConfigured.value = true;
    renderGate();
    expect(screen.getByText('APP')).toBeInTheDocument();
    expect(screen.queryByText('SETUP SCREEN')).not.toBeInTheDocument();
  });

  it('renders the app when the gateway answers', () => {
    mockHealth.mockReturnValue({ isError: false, isLoading: false });
    renderGate();
    expect(screen.getByText('APP')).toBeInTheDocument();
  });

  it('does not flash the wizard during the first health poll', () => {
    mockHealth.mockReturnValue({ isError: false, isLoading: true });
    renderGate();
    expect(screen.getByText('APP')).toBeInTheDocument();
  });

  it('does not show setup mid-poll even if an earlier poll errored', () => {
    // react-query can report isError and isLoading together while refetching.
    // Treating that as "never worked" would make the wizard flicker in and out
    // of view on an intermittent connection.
    mockHealth.mockReturnValue({ isError: true, isLoading: true });
    renderGate();
    expect(screen.getByText('APP')).toBeInTheDocument();
  });

  it('local proxy setups are never gated', () => {
    // Docker and `npm run dev` have an empty base (same-origin, proxied) and
    // a healthy gateway. isGatewayUserConfigured() is false for them, so only
    // the health check keeps them out of the wizard.
    mockHealth.mockReturnValue({ isError: false, isLoading: false });
    mockConfigured.value = false;
    renderGate();
    expect(screen.getByText('APP')).toBeInTheDocument();
  });
});
