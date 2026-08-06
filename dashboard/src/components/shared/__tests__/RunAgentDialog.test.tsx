import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '../../../test/test-utils';
import { useProjectStore } from '../../../store/project-store';

const mockUseProjects = vi.fn();
vi.mock('../../../hooks/use-projects', () => ({
  useProjects: () => mockUseProjects(),
}));

vi.mock('../../../hooks/use-assistant', () => ({
  useSubmitRequest: () => ({ mutate: vi.fn(), isPending: false }),
  useRunStatus: () => ({ data: undefined }),
}));

import { RunAgentDialog } from '../RunAgentDialog';

const PROJECT = {
  id: 'p-active',
  name: 'Active Project',
  description: '',
  status: 'active',
  work_products: [],
  agentCount: 0,
  lastUpdated: '2026-05-22T00:00:00Z',
  createdAt: '2026-05-22T00:00:00Z',
};

describe('RunAgentDialog', () => {
  beforeEach(() => {
    mockUseProjects.mockReturnValue({ data: [PROJECT], isLoading: false });
    useProjectStore.setState({ activeProjectId: null, hasSelected: false });
  });

  it('renders as an accessible modal', () => {
    render(<RunAgentDialog onClose={vi.fn()} />);
    const dialog = screen.getByRole('dialog', { name: 'Run Agent' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
  });

  it('locks body scroll while open and restores it on unmount', () => {
    const { unmount } = render(<RunAgentDialog onClose={vi.fn()} />);
    expect(document.body.style.overflow).toBe('hidden');
    unmount();
    expect(document.body.style.overflow).not.toBe('hidden');
  });

  it('closes on Escape', () => {
    const onClose = vi.fn();
    render(<RunAgentDialog onClose={onClose} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('closes when clicking the backdrop but not the dialog panel', () => {
    const onClose = vi.fn();
    render(<RunAgentDialog onClose={onClose} />);
    fireEvent.click(screen.getByRole('dialog'));
    expect(onClose).not.toHaveBeenCalled();
    // eslint-disable-next-line testing-library/no-node-access
    fireEvent.click(screen.getByRole('dialog').parentElement!);
    expect(onClose).toHaveBeenCalled();
  });

  it('pre-fills the project field from the shared active-project context', () => {
    useProjectStore.setState({ activeProjectId: 'p-active', hasSelected: true });
    render(<RunAgentDialog onClose={vi.fn()} />);
    const select = document.getElementById('dialog-project') as HTMLSelectElement;
    expect(select.value).toBe('p-active');
  });

  it('defaults to "Select a project..." when no project is active', () => {
    render(<RunAgentDialog onClose={vi.fn()} />);
    const select = document.getElementById('dialog-project') as HTMLSelectElement;
    expect(select.value).toBe('');
  });
});
