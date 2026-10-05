import { describe, it, expect, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../test/test-utils';

const mockUseProposeRequirementFix = vi.fn();
vi.mock('../../hooks/use-requirements', () => ({
  useRequirementQuality: vi.fn(),
  useRequirementMatrix: vi.fn(),
  useRequirementCoverage: vi.fn(),
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

vi.mock('../../hooks/use-releases', () => ({
  useReleasePackages: vi.fn(),
  useCreateReleasePackage: vi.fn(),
}));

vi.mock('../../hooks/use-test-plan', () => ({
  useTestPlan: vi.fn(),
  useGenerateTestPlan: vi.fn(),
}));

import { RequirementsPage } from '../RequirementsPage';
import {
  useRequirementCoverage,
  useRequirementMatrix,
  useRequirementQuality,
} from '../../hooks/use-requirements';
import { useCreateReleasePackage, useReleasePackages } from '../../hooks/use-releases';
import { useGenerateTestPlan, useTestPlan } from '../../hooks/use-test-plan';

const mockUseRequirementQuality = vi.mocked(useRequirementQuality);
const mockUseRequirementMatrix = vi.mocked(useRequirementMatrix);
const mockUseRequirementCoverage = vi.mocked(useRequirementCoverage);
const mockUseReleasePackages = vi.mocked(useReleasePackages);
const mockUseCreateReleasePackage = vi.mocked(useCreateReleasePackage);
const mockUseTestPlan = vi.mocked(useTestPlan);
const mockUseGenerateTestPlan = vi.mocked(useGenerateTestPlan);

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

const MATRIX_REPORT = {
  rows: [
    {
      requirementId: 'r1',
      requirementName: 'moving_mass_budget',
      limitText: '<= 4.5 kg',
      status: 'fail' as const,
      detail: 'value 6.78 exceeds limit 4.5 (margin -2.28)',
      artefactIds: ['a1'],
      evidence: [
        {
          id: 'e1',
          method: 'twin.rank_sensitivity',
          tier: null,
          value: 6.78,
          limit: 4.5,
          margin: -2.28,
          staleness: 'current',
        },
      ],
    },
    {
      requirementId: 'r2',
      requirementName: 'deflection',
      limitText: '<= 0.5mm',
      status: 'pass' as const,
      detail: 'value 0.05 within limit 0.5 (margin 0.45)',
      artefactIds: ['a2'],
      evidence: [
        {
          id: 'e2',
          method: 'twin.evaluate_metric',
          tier: 0,
          value: 0.05,
          limit: 0.5,
          margin: 0.45,
          staleness: 'current',
        },
      ],
    },
  ],
};

describe('RequirementsPage', () => {
  beforeEach(() => {
    mockUseProposeRequirementFix.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    });
    mockUseRequirementMatrix.mockReturnValue({
      data: { rows: [] },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementMatrix>);
    mockUseRequirementCoverage.mockReturnValue({
      data: {
        needs_to_requirements: null,
        requirements_to_architecture: null,
        requirements_to_verification: null,
        verification_to_evidence: null,
        critical_requirements_to_evidence: null,
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementCoverage>);
    mockUseReleasePackages.mockReturnValue({
      data: [],
      isLoading: false,
    } as unknown as ReturnType<typeof useReleasePackages>);
    mockUseCreateReleasePackage.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useCreateReleasePackage>);
    mockUseTestPlan.mockReturnValue({
      data: [],
      isLoading: false,
    } as unknown as ReturnType<typeof useTestPlan>);
    mockUseGenerateTestPlan.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useGenerateTestPlan>);
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

  it('renders the evidence matrix with status badges', () => {
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    mockUseRequirementMatrix.mockReturnValue({
      data: MATRIX_REPORT,
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementMatrix>);
    render(<RequirementsPage />);
    const matrix = screen.getByTestId('requirements-matrix');
    expect(matrix).toHaveTextContent('moving_mass_budget');
    expect(matrix).toHaveTextContent('fail');
    expect(matrix).toHaveTextContent('deflection');
    expect(matrix).toHaveTextContent('pass');
  });

  it('shows the constraint set revision the requirements are read from (FORGE-528)', () => {
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    mockUseRequirementMatrix.mockReturnValue({
      data: {
        revisionRefs: ['CS-ARM@2'],
        rows: MATRIX_REPORT.rows.map((r) => ({ ...r, revisionRef: 'CS-ARM@2' })),
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementMatrix>);
    render(<RequirementsPage />);
    expect(screen.getByTestId('requirements-revision')).toHaveTextContent('current: CS-ARM@2');
    expect(screen.getAllByTestId('requirement-revision-ref')).toHaveLength(2);
  });

  it('expands evidence details on click', async () => {
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    mockUseRequirementMatrix.mockReturnValue({
      data: MATRIX_REPORT,
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementMatrix>);
    const user = userEvent.setup();
    render(<RequirementsPage />);

    expect(screen.queryByText('twin.rank_sensitivity')).not.toBeInTheDocument();
    // Both matrix rows have exactly 1 evidence entry -- the first button is
    // moving_mass_budget's (declared first in MATRIX_REPORT).
    const evidenceButtons = screen.getAllByRole('button', { name: /1 evidence/ });
    await user.click(evidenceButtons[0]!);
    expect(screen.getByText('twin.rank_sensitivity')).toBeInTheDocument();
  });

  it('links each matrix row to the Structure tab', () => {
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    mockUseRequirementMatrix.mockReturnValue({
      data: MATRIX_REPORT,
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementMatrix>);
    render(<RequirementsPage />);
    const links = screen.getAllByRole('link', { name: 'Structure' });
    expect(links.length).toBe(2);
    expect(links[0]).toHaveAttribute('href', '/twin');
  });

  it('does not render the matrix section when there are no rows', () => {
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    render(<RequirementsPage />);
    expect(screen.queryByTestId('requirements-matrix')).not.toBeInTheDocument();
  });

  it('does not render the coverage heatmap without an active project', () => {
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    render(<RequirementsPage />);
    expect(screen.queryByTestId('requirements-coverage')).not.toBeInTheDocument();
  });

  it('renders the coverage heatmap with real percentages for the active project', () => {
    mockUseActiveProject.mockReturnValue({
      activeProjectId: 'proj-1',
      activeProject: undefined,
      setActiveProjectId: vi.fn(),
      projects: [],
    });
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    mockUseRequirementCoverage.mockReturnValue({
      data: {
        needs_to_requirements: 100,
        requirements_to_architecture: 40,
        requirements_to_verification: 20,
        verification_to_evidence: null,
        critical_requirements_to_evidence: 0,
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementCoverage>);

    render(<RequirementsPage />);

    expect(screen.getByTestId('requirements-coverage')).toBeInTheDocument();
    expect(screen.getByTestId('coverage-tile-needs_to_requirements')).toHaveTextContent('100%');
    expect(screen.getByTestId('coverage-tile-critical_requirements_to_evidence')).toHaveTextContent(
      '0%',
    );
    expect(screen.getByTestId('coverage-tile-verification_to_evidence')).toHaveTextContent('N/A');
  });

  it('does not render release packages without an active project', () => {
    // Explicit reset -- prior tests in this file set activeProjectId via
    // mockReturnValue, which persists across tests (no global mock reset
    // is configured), so this test must not rely on the module-level
    // default still being in effect.
    mockUseActiveProject.mockReturnValue({
      activeProjectId: null,
      activeProject: undefined,
      setActiveProjectId: vi.fn(),
      projects: [],
    });
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    render(<RequirementsPage />);
    expect(screen.queryByTestId('release-packages')).not.toBeInTheDocument();
  });

  it('shows an empty state with no release packages yet', () => {
    mockUseActiveProject.mockReturnValue({
      activeProjectId: 'proj-1',
      activeProject: undefined,
      setActiveProjectId: vi.fn(),
      projects: [],
    });
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    render(<RequirementsPage />);
    expect(screen.getByTestId('release-packages')).toHaveTextContent('No release packages yet');
  });

  it('renders release packages with real snapshot counts and diffs', () => {
    mockUseActiveProject.mockReturnValue({
      activeProjectId: 'proj-1',
      activeProject: undefined,
      setActiveProjectId: vi.fn(),
      projects: [],
    });
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    mockUseReleasePackages.mockReturnValue({
      data: [
        {
          nodeId: 'rp-1',
          createdAt: '2026-09-30T10:00:00Z',
          title: 'v1.0 release candidate',
          statement: 'Release package: 3 hierarchy nodes, 2 BOM items, 1 evidence, 1 decisions',
          snapshot: {
            hierarchyNodeIds: ['h1', 'h2', 'h3'],
            bomItemIds: ['b1', 'b2'],
            evidenceIds: ['e1'],
            decisionIds: ['d1'],
            drawingIds: [],
          },
          diffFromPrevious: {
            comparedTo: null,
            hierarchyDelta: 3,
            bomDelta: 2,
            evidenceDelta: 1,
            decisionDelta: 1,
          },
          gateStatus: 'passed',
        },
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useReleasePackages>);
    render(<RequirementsPage />);

    const section = screen.getByTestId('release-packages');
    expect(section).toHaveTextContent('v1.0 release candidate');
    expect(section).toHaveTextContent('passed');
    expect(screen.getByTestId('release-package-rp-1')).toHaveTextContent('First release');
  });

  it('clicking Create release package triggers the creation mutation', async () => {
    mockUseActiveProject.mockReturnValue({
      activeProjectId: 'proj-1',
      activeProject: undefined,
      setActiveProjectId: vi.fn(),
      projects: [],
    });
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    const mutate = vi.fn((_notes: string | undefined, opts?: { onSuccess?: (r: unknown) => void }) => {
      opts?.onSuccess?.({ nodeId: 'rp-2', title: 'v1.1' });
    });
    mockUseCreateReleasePackage.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateReleasePackage>);
    const user = userEvent.setup();
    render(<RequirementsPage />);

    await user.click(screen.getByTestId('create-release-button'));

    expect(mutate).toHaveBeenCalledWith(undefined, expect.anything());
  });

  it('shows an empty state with no test-plan entries yet', () => {
    mockUseActiveProject.mockReturnValue({
      activeProjectId: 'proj-1',
      activeProject: undefined,
      setActiveProjectId: vi.fn(),
      projects: [],
    });
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    render(<RequirementsPage />);
    expect(screen.getByTestId('test-plan')).toHaveTextContent('No test-plan entries yet');
  });

  it('renders generated test-plan entries with step and acceptance value', () => {
    mockUseActiveProject.mockReturnValue({
      activeProjectId: 'proj-1',
      activeProject: undefined,
      setActiveProjectId: vi.fn(),
      projects: [],
    });
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    mockUseTestPlan.mockReturnValue({
      data: [
        {
          nodeId: 'vc-1',
          requirementId: 'r1',
          step: 'Measure payload_capacity_kg on robot_description; acceptance: payload_capacity_kg >= 2.0kg',
          acceptanceValue: '2.0kg',
        },
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useTestPlan>);
    render(<RequirementsPage />);

    const section = screen.getByTestId('test-plan');
    expect(section).toHaveTextContent('payload_capacity_kg');
    expect(section).toHaveTextContent('2.0kg');
    expect(screen.getByTestId('test-plan-entry-vc-1')).toBeInTheDocument();
  });

  it('clicking Generate test plan triggers the generation mutation', async () => {
    mockUseActiveProject.mockReturnValue({
      activeProjectId: 'proj-1',
      activeProject: undefined,
      setActiveProjectId: vi.fn(),
      projects: [],
    });
    mockUseRequirementQuality.mockReturnValue({
      data: { requirements: [], conflicts: [], completeness: { productType: 'generic', covered: [], missing: [] } },
      isLoading: false,
    } as unknown as ReturnType<typeof useRequirementQuality>);
    const mutate = vi.fn((_arg: undefined, opts?: { onSuccess?: (r: unknown) => void }) => {
      opts?.onSuccess?.([]);
    });
    mockUseGenerateTestPlan.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useGenerateTestPlan>);
    const user = userEvent.setup();
    render(<RequirementsPage />);

    await user.click(screen.getByTestId('generate-test-plan-button'));

    expect(mutate).toHaveBeenCalledWith(undefined, expect.anything());
  });
});
