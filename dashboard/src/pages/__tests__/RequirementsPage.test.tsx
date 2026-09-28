import { describe, it, expect, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../test/test-utils';

const mockUseProposeRequirementFix = vi.fn();
vi.mock('../../hooks/use-requirements', () => ({
  useRequirementQuality: vi.fn(),
  useProposeRequirementFix: () => mockUseProposeRequirementFix(),
}));

const mockUseActiveProject = vi.fn(() => ({
  activeProjectId: null as string | null,
  activeProject: undefined,
  setActiveProjectId: vi.fn(),
  projects: [] as unknown[],
}));
vi.mock('../../hooks/use-active-project', () => ({
  useActiveProject: () => mockUseActiveProject(),
}));

import { RequirementsPage } from '../RequirementsPage';
import { useRequirementQuality } from '../../hooks/use-requirements';

const mockUseRequirementQuality = vi.mocked(useRequirementQuality);

const REPORT = {
  requirements: [
    {
      id: 'r1',
      name: 'mass_limit',
      text: 'The arm shall weigh at most 2 kg.',
      severity: 'error',
      clarity: 'pass' as const,
      atomicity: 'pass' as const,
      quantified: 'pass' as const,
      traceability: null,
      verificationReady: 'pass' as const,
      conflicts: [] as string[],
    },
    {
      id: 'r2',
      name: 'vague_speed',
      text: 'The arm should move fast.',
      severity: 'warning',
      clarity: 'fail' as const,
      atomicity: 'pass' as const,
      quantified: 'fail' as const,
      traceability: null,
      verificationReady: 'fail' as const,
      conflicts: [] as string[],
    },
  ],
  conflicts: [
    { aId: 'r1', aName: 'mass_limit', bId: 'r3', bName: 'mass_floor', detail: 'incompatible bounds' },
  ],
  completeness: { productType: 'generic', covered: ['mechanical'], missing: ['safety'] },
};

describe('RequirementsPage', () => {
  beforeEach(() => {
    mockUseProposeRequirementFix.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    });
  });

  it('shows loading state', () => {
    mockUseRequirementQuality.mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    const { container } = render(<RequirementsPage />);
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
  });

  it('shows empty state when there are no requirements', () => {
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    render(<RequirementsPage />);
    expect(screen.getByText('No requirements yet')).toBeInTheDocument();
  });

  it('renders requirement rows with quality flags', () => {
    mockUseRequirementQuality.mockReturnValue({
      data: REPORT,
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    render(<RequirementsPage />);
    // "mass_limit" also appears in the conflicts panel -- there are two.
    expect(screen.getAllByText('mass_limit').length).toBeGreaterThan(0);
    expect(screen.getByText('vague_speed')).toBeInTheDocument();
  });

  it('shows conflict pairs', () => {
    mockUseRequirementQuality.mockReturnValue({
      data: REPORT,
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    render(<RequirementsPage />);
    expect(screen.getByTestId('requirements-conflicts')).toHaveTextContent('mass_limit');
    expect(screen.getByTestId('requirements-conflicts')).toHaveTextContent('mass_floor');
  });

  it('shows completeness coverage and gaps', () => {
    mockUseRequirementQuality.mockReturnValue({
      data: REPORT,
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    render(<RequirementsPage />);
    const panel = screen.getByTestId('requirements-completeness');
    expect(panel).toHaveTextContent('mechanical');
    expect(panel).toHaveTextContent('missing: safety');
  });

  it('offers "Fix with AI" only for requirements with issues', () => {
    mockUseRequirementQuality.mockReturnValue({
      data: REPORT,
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    render(<RequirementsPage />);
    const buttons = screen.getAllByRole('button', { name: 'Fix with AI' });
    // Only the second requirement (vague_speed, with failing flags) offers a fix.
    expect(buttons).toHaveLength(1);
  });

  it('clicking Fix with AI triggers the proposal mutation', async () => {
    const mutate = vi.fn((_id: string, opts?: { onSuccess?: (r: unknown) => void }) => {
      opts?.onSuccess?.({ proposedText: 'The arm shall move at at least 0.5 m/s.', rationale: 'weak_word: fast' });
    });
    mockUseProposeRequirementFix.mockReturnValue({ mutate, isPending: false });
    mockUseRequirementQuality.mockReturnValue({
      data: REPORT,
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    const user = userEvent.setup();
    render(<RequirementsPage />);

    await user.click(screen.getByRole('button', { name: 'Fix with AI' }));

    expect(mutate).toHaveBeenCalledWith('r2', expect.anything());
    expect(screen.getByText(/at least 0.5 m\/s/)).toBeInTheDocument();
  });
});
