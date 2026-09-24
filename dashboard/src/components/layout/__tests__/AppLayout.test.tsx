import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

const mockUseHealth = vi.fn();
vi.mock('../../../hooks/use-health', () => ({ useHealth: () => mockUseHealth() }));
vi.mock('../../shared/ProjectSwitcher', () => ({
  ProjectSwitcher: () => <div data-testid="project-switcher" />,
}));

import { AppLayout } from '../AppLayout';
import { useLayoutStore } from '../../../store/layout-store';
import { NAV_COLLAPSED_STORAGE_KEY } from '../../../store/layout-store';

function renderShell(path = '/projects') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="projects" element={<p>projects page</p>} />
            <Route path="projects/:id" element={<p>project detail</p>} />
            <Route path="twin" element={<p>twin page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('AppLayout shell', () => {
  beforeEach(() => {
    localStorage.clear();
    useLayoutStore.setState({ sidebarCollapsed: true, mobileSidebarOpen: false });
    mockUseHealth.mockReturnValue({ isError: false, data: undefined });
  });

  it('renders the skip link, grouped nav and routed page', () => {
    const { container } = renderShell();
    expect(screen.getByText('Skip to main content')).toHaveAttribute('href', '#main-content');
    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    for (const label of [
      'Projects',
      'Agent sessions',
      'Runs',
      'Approvals',
      'Digital twin',
      'Bill of materials',
      'Files & artifacts',
      'Knowledge',
      'Compliance',
    ]) {
      expect(within(nav).getByRole('link', { name: label })).toBeInTheDocument();
    }
    expect(within(nav).getByRole('link', { name: 'Projects' })).toHaveClass('selected');
    expect(screen.getByRole('link', { name: 'Settings & connection' })).toHaveAttribute('href', '/settings');
    expect(screen.getByRole('link', { name: /Documentation/ })).toHaveAttribute(
      'href',
      'https://fidelodok.github.io/MetaForge/',
    );
    expect(screen.getByText('projects page')).toBeInTheDocument();
    expect(container.querySelector('.workspace-shell')).toHaveClass('nav-collapsed');
    expect(container.querySelector('.workspace-shell')).not.toHaveClass('twin-shell');
  });

  it('uses the twin-shell variant on /twin', () => {
    const { container } = renderShell('/twin');
    expect(container.querySelector('.workspace-shell')).toHaveClass('twin-shell');
  });

  it('toggles and persists the collapsed rail', () => {
    const { container } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: 'Expand navigation and explorer' }));
    expect(container.querySelector('.workspace-shell')).not.toHaveClass('nav-collapsed');
    expect(localStorage.getItem(NAV_COLLAPSED_STORAGE_KEY)).toBe('false');
    expect(screen.getByRole('button', { name: 'Minimise navigation and explorer' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('opens the mobile drawer with a backdrop and closes it', () => {
    const { container } = renderShell();
    fireEvent.click(screen.getByRole('button', { name: 'Toggle navigation' }));
    expect(container.querySelector('.workspace-sidebar')).toHaveClass('is-open');
    const closers = screen.getAllByRole('button', { name: 'Close navigation' });
    expect(closers.length).toBe(2); // backdrop + close button
    fireEvent.click(closers[0]!);
    expect(container.querySelector('.workspace-sidebar')).not.toHaveClass('is-open');
  });

  it('shows the section breadcrumb plus Details on nested routes', () => {
    renderShell('/projects/abc-123');
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' });
    expect(within(crumbs).getByRole('link', { name: 'Projects' })).toHaveAttribute('href', '/projects');
    expect(within(crumbs).getByText('Details')).toHaveAttribute('aria-current', 'page');
    expect(document.title).toBe('Projects — MetaForge');
  });
});
