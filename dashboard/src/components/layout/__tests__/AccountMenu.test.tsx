import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const auth = vi.hoisted(() => ({
  value: null as null | { status: string; user: { id: string; email?: string } | null; signOut: () => Promise<void> },
}));
vi.mock('../../../auth/AuthProvider', () => ({ useOptionalAuth: () => auth.value }));

const { AccountMenu } = await import('../AccountMenu');

function renderMenu() {
  return render(
    <MemoryRouter>
      <AccountMenu />
    </MemoryRouter>,
  );
}

describe('AccountMenu', () => {
  beforeEach(() => {
    auth.value = null;
  });

  it('is the plain Settings link on a local gateway', () => {
    renderMenu();
    expect(screen.getByRole('link', { name: 'Account and API keys' })).toHaveAttribute('href', '/settings');
    expect(screen.queryByText('Sign out')).not.toBeInTheDocument();
  });

  it('stays a link while signed out', () => {
    auth.value = { status: 'signed-out', user: null, signOut: vi.fn() };
    renderMenu();
    expect(screen.getByRole('link', { name: 'Account and API keys' })).toBeInTheDocument();
  });

  it('shows who is signed in and signs out', async () => {
    const signOut = vi.fn().mockResolvedValue(undefined);
    auth.value = { status: 'signed-in', user: { id: 'u1', email: 'pilot@example.com' }, signOut };
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Account: pilot@example.com' }));
    expect(screen.getByRole('menu')).toHaveTextContent('pilot@example.com');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Sign out' }));
    await waitFor(() => expect(signOut).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
  });

  it('closes on Escape', () => {
    auth.value = { status: 'signed-in', user: { id: 'u1' }, signOut: vi.fn() };
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'Account: Signed in' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});
