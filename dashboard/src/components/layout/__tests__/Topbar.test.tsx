import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const mockUseHealth = vi.fn();
vi.mock('../../../hooks/use-health', () => ({ useHealth: () => mockUseHealth() }));
vi.mock('../../shared/ProjectSwitcher', () => ({
  ProjectSwitcher: () => <div data-testid="project-switcher" />,
}));

import { Topbar } from '../Topbar';
import { THEME_STORAGE_KEY, useThemeStore } from '../../../store/theme-store';

function renderTopbar(path = '/projects') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Topbar />
    </MemoryRouter>,
  );
}

describe('Topbar', () => {
  beforeEach(() => {
    localStorage.clear();
    useThemeStore.getState().setMode('light');
  });

  it.each([
    [{ isError: false, data: undefined }, 'Connecting'],
    [{ isError: false, data: { status: 'healthy' } }, 'Connected'],
    [{ isError: true, data: undefined }, 'Unavailable'],
  ])('gateway chip reflects health %#', (health, label) => {
    mockUseHealth.mockReturnValue(health);
    renderTopbar();
    const chip = screen.getByRole('link', { name: `Gateway: ${label}` });
    expect(chip).toHaveAttribute('href', '/settings');
    expect(chip).toHaveTextContent(label);
    expect(chip.classList.contains('disconnected')).toBe(health.isError);
  });

  it('links to the sample workspace, or exits it when demo=1', () => {
    mockUseHealth.mockReturnValue({ isError: false, data: undefined });
    const { unmount } = renderTopbar('/twin');
    expect(screen.getByTitle('Open sample workspace')).toHaveAttribute(
      'href',
      '/twin?demo=1&node=sample-pcb',
    );
    unmount();
    renderTopbar('/twin?demo=1&node=sample-pcb');
    expect(screen.getByText('Exit sample')).toHaveAttribute('href', '/twin');
    expect(screen.getByRole('link', { name: 'Sample workspace · offline' })).toHaveTextContent('Sample');
  });

  it('has the account link and project switcher', () => {
    mockUseHealth.mockReturnValue({ isError: false, data: undefined });
    renderTopbar();
    expect(screen.getByRole('link', { name: 'Account and API keys' })).toHaveAttribute('href', '/settings');
    expect(screen.getByTestId('project-switcher')).toBeInTheDocument();
  });

  it('theme control applies and persists the appearance', () => {
    mockUseHealth.mockReturnValue({ isError: false, data: undefined });
    renderTopbar();
    const select = screen.getByRole('combobox', { name: 'Appearance' });
    expect(select).toHaveValue('light');
    fireEvent.change(select, { target: { value: 'dark' } });
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(document.documentElement.style.colorScheme).toBe('dark');
    fireEvent.change(select, { target: { value: 'light' } });
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});
