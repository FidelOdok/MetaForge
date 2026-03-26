import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '../../test/test-utils';

vi.mock('../../hooks/use-assistant', () => ({
  useSubmitRequest: () => ({ mutate: vi.fn(), isPending: false, isError: false, reset: vi.fn() }),
  useRunStatus: () => ({ data: undefined }),
  useProposals: vi.fn(),
  useDecideProposal: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../hooks/use-projects', () => ({
  useProjects: () => ({ data: [] }),
}));

import { DesignAssistantPage } from '../DesignAssistantPage';

describe('DesignAssistantPage', () => {
  it('renders the page heading', () => {
    render(<DesignAssistantPage />);
    expect(screen.getByText('Design Assistant')).toBeInTheDocument();
  });

  it('renders project selector', () => {
    render(<DesignAssistantPage />);
    expect(screen.getByText('Select a project...')).toBeInTheDocument();
  });

  it('renders action selector with options', () => {
    render(<DesignAssistantPage />);
    expect(screen.getByText('Validate Stress')).toBeInTheDocument();
    expect(screen.getByText('Generate CAD')).toBeInTheDocument();
  });

  it('shows target work product field for actions that need it', () => {
    render(<DesignAssistantPage />);
    // Default action (validate_stress) needs a target work product
    expect(screen.getByText('Target work product')).toBeInTheDocument();
  });

  it('hides target work product when action does not need it', () => {
    render(<DesignAssistantPage />);
    const actionSelect = document.getElementById('action-select') as HTMLSelectElement;
    fireEvent.change(actionSelect, { target: { value: 'generate_cad' } });
    expect(screen.queryByText('Target work product')).not.toBeInTheDocument();
  });

  it('renders submit button', () => {
    render(<DesignAssistantPage />);
    expect(screen.getByRole('button', { name: 'Submit request' })).toBeInTheDocument();
  });

  it('shows description prompt label when action does not need target', () => {
    render(<DesignAssistantPage />);
    const actionSelect = document.getElementById('action-select') as HTMLSelectElement;
    fireEvent.change(actionSelect, { target: { value: 'generate_cad' } });
    expect(screen.getByText('Description / prompt')).toBeInTheDocument();
  });
});
