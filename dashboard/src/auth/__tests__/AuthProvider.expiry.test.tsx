import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AuthClient, AuthSession } from '../types';

/**
 * A gateway that stops accepting the session's token mid-use ends the session
 * here, once, with a reason the sign-in screen can show.
 */

const fake = vi.hoisted(() => ({ client: null as AuthClient | null }));
vi.mock('metaforge:auth', () => ({
  get authClient() {
    return fake.client;
  },
}));

const { AuthProvider, useAuth } = await import('../AuthProvider');
const { getAccessToken, notifyUnauthorized } = await import('../accessToken');

const SESSION: AuthSession = { accessToken: 'tok', user: { id: 'u1', email: 'a@example.com' } };

function Probe() {
  const { status, signedOutReason } = useAuth();
  return <p>{`${status}:${signedOutReason ?? '-'}`}</p>;
}

describe('AuthProvider and a rejected token', () => {
  let signOut: ReturnType<typeof vi.fn<() => Promise<void>>>;

  beforeEach(() => {
    signOut = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);
    fake.client = {
      mode: 'stand-in',
      isConfigured: true,
      configHint: () => '',
      restore: async () => SESSION,
      subscribe: () => () => {},
      signIn: async () => {},
      signUp: async () => ({ needsConfirmation: false }),
      signOut,
      SignInView: () => null,
    };
  });

  it('signs out once with reason "expired" and clears the token', async () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByText('signed-in:-')).toBeInTheDocument());
    expect(getAccessToken()).toBe('tok');

    act(() => {
      notifyUnauthorized();
      notifyUnauthorized(); // a burst of failing panels
    });

    expect(screen.getByText('signed-out:expired')).toBeInTheDocument();
    expect(getAccessToken()).toBeNull();
    expect(signOut).toHaveBeenCalledOnce();
  });

  it('ignores a 401 when there is no session', async () => {
    fake.client!.restore = async () => null;
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByText('signed-out:-')).toBeInTheDocument());
    act(() => notifyUnauthorized());
    expect(screen.getByText('signed-out:-')).toBeInTheDocument();
    expect(signOut).not.toHaveBeenCalled();
  });
});
