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
import { useProposals } from '../../hooks/use-assistant';

const mockUseProposals = vi.mocked(useProposals);

const MOCK_PROPOSAL = {
  change_id: 'c1',
  agent_code: 'MECH',
  description: 'Adjust bracket thickness to reduce stress concentration at mounting holes',
  diff: { thickness: { from: 2, to: 3 } },
  work_products_affected: ['mechanical', 'cad'],
  status: 'pending',
  session_id: 's1',
  created_at: new Date().toISOString(),
  decided_at: null,
  decision_reason: null,
  reviewer: null,
};

const MOCK_DECIDED_PROPOSAL = {
  ...MOCK_PROPOSAL,
  change_id: 'c2',
  description: 'Older proposal that was approved',
  status: 'approved',
  decided_at: new Date().toISOString(),
};

describe('DesignAssistantPage', () => {
  it('renders the page heading', () => {
    mockUseProposals.mockReturnValue({
      data: { proposals: [], total: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useProposals>);
    render(<DesignAssistantPage />);
    expect(screen.getByText('Design Assistant')).toBeInTheDocument();
  });

  it('shows loading skeletons while proposals are loading', () => {
    mockUseProposals.mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useProposals>);
    render(<DesignAssistantPage />);
    // SkeletonCard renders animate-pulse elements
    const skeletons = document.querySelectorAll('.animate-pulse');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('shows empty state when no proposals', () => {
    mockUseProposals.mockReturnValue({
      data: { proposals: [], total: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useProposals>);
    render(<DesignAssistantPage />);
    expect(screen.getByText('No pending proposals.')).toBeInTheDocument();
  });

  it('renders proposal card with title, confidence badge, and domain tags', () => {
    mockUseProposals.mockReturnValue({
      data: { proposals: [MOCK_PROPOSAL], total: 1 },
      isLoading: false,
    } as unknown as ReturnType<typeof useProposals>);
    render(<DesignAssistantPage />);
    // Title (truncated at 80 chars)
    expect(screen.getByText(/Adjust bracket thickness/)).toBeInTheDocument();
    // Confidence badge
    expect(screen.getByText(/confidence/i)).toBeInTheDocument();
    // Domain tags
    expect(screen.getByText('mechanical')).toBeInTheDocument();
    expect(screen.getByText('cad')).toBeInTheDocument();
  });

  it('renders Accept and Reject buttons on pending proposals', () => {
    mockUseProposals.mockReturnValue({
      data: { proposals: [MOCK_PROPOSAL], total: 1 },
      isLoading: false,
    } as unknown as ReturnType<typeof useProposals>);
    render(<DesignAssistantPage />);
    expect(screen.getByRole('button', { name: 'Accept' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
  });

  it('renders Ask more and Export buttons on pending proposals', () => {
    mockUseProposals.mockReturnValue({
      data: { proposals: [MOCK_PROPOSAL], total: 1 },
      isLoading: false,
    } as unknown as ReturnType<typeof useProposals>);
    render(<DesignAssistantPage />);
    expect(screen.getByRole('button', { name: 'Ask more' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /export/i })).toBeInTheDocument();
  });

  it('pre-fills prompt with Ask more text when button clicked', () => {
    mockUseProposals.mockReturnValue({
      data: { proposals: [MOCK_PROPOSAL], total: 1 },
      isLoading: false,
    } as unknown as ReturnType<typeof useProposals>);
    render(<DesignAssistantPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Ask more' }));
    const input = screen.getByPlaceholderText(/e.g. focus on thermal stress/i) as HTMLInputElement;
    expect(input.value).toMatch(/Tell me more about:/);
  });

  it('renders agent selector dropdown with expected options', () => {
    mockUseProposals.mockReturnValue({
      data: { proposals: [], total: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useProposals>);
    render(<DesignAssistantPage />);
    const agentSelect = screen.getByLabelText('Direct to agent') as HTMLSelectElement;
    expect(agentSelect).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Any Agent' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'ME — Mechanical' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'EE — Electronics' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'FW — Firmware' })).toBeInTheDocument();
  });

  it('shows prefix preview when a specific agent is selected and prompt is typed', () => {
    mockUseProposals.mockReturnValue({
      data: { proposals: [], total: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useProposals>);
    render(<DesignAssistantPage />);
    const agentSelect = screen.getByLabelText('Direct to agent');
    fireEvent.change(agentSelect, { target: { value: 'ME' } });
    // action is validate_stress which needsTarget — switch to generate_cad so prompt input is visible and required
    const actionSelect = screen.getByLabelText('Action');
    fireEvent.change(actionSelect, { target: { value: 'generate_cad' } });
    const input = screen.getByPlaceholderText(/e.g. simple bracket/i);
    fireEvent.change(input, { target: { value: 'optimize thickness' } });
    expect(screen.getByText(/\[@ME\]/)).toBeInTheDocument();
  });

  it('shows history section toggled by Previous Proposals button', () => {
    mockUseProposals.mockReturnValue({
      data: { proposals: [MOCK_DECIDED_PROPOSAL], total: 1 },
      isLoading: false,
    } as unknown as ReturnType<typeof useProposals>);
    render(<DesignAssistantPage />);
    const historyToggle = screen.getByRole('button', { name: /Previous Proposals/i });
    expect(historyToggle).toBeInTheDocument();
    // History is collapsed by default
    expect(screen.queryByText('Older proposal that was approved')).not.toBeInTheDocument();
    // Expand
    fireEvent.click(historyToggle);
    expect(screen.getByText(/Older proposal that was approved/)).toBeInTheDocument();
  });

  it('toggles the requirements panel on button click', () => {
    mockUseProposals.mockReturnValue({
      data: { proposals: [], total: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useProposals>);
    render(<DesignAssistantPage />);
    expect(screen.queryByText('Requirement Traceability')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show Traceability' }));
    expect(screen.getByText('Requirement Traceability')).toBeInTheDocument();
    // Close with the X button
    fireEvent.click(screen.getByRole('button', { name: 'Close requirements panel' }));
    expect(screen.queryByText('Requirement Traceability')).not.toBeInTheDocument();
  });
});
