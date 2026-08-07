import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '../../test/test-utils';

const mockUseActiveProject = vi.fn();
vi.mock('../../hooks/use-active-project', () => ({
  useActiveProject: () => mockUseActiveProject(),
}));

const mockUseChecklist = vi.fn();
const mockLinkEvidenceMutate = vi.fn();
vi.mock('../../hooks/use-compliance', () => ({
  useChecklist: (...args: unknown[]) => mockUseChecklist(...args),
  useLinkEvidence: () => ({ mutate: mockLinkEvidenceMutate, isPending: false }),
}));

import { CompliancePage } from '../CompliancePage';
import type { Checklist } from '../../types/compliance';

const PROJECT = { id: 'p1', name: 'Drone Kit' };

const CHECKLIST: Checklist = {
  project_id: 'p1',
  target_markets: ['UKCA', 'CE'],
  total_items: 2,
  evidenced_items: 1,
  coverage_percent: 50,
  items: [
    {
      id: 'UKCA-SAF-001',
      regime: 'UKCA',
      category: 'safety',
      requirement: 'Enclosure rated for creepage/clearance',
      standard: 'EN 62368-1:2020',
      evidence_type: 'TEST_REPORT',
      evidence_status: 'MISSING',
      evidence_work_product_id: null,
      notes: '',
    },
    {
      id: 'CE-EMC-002',
      regime: 'CE',
      category: 'EMC',
      requirement: 'Radiated emissions within limit',
      standard: 'EN 55032',
      evidence_type: 'TEST_REPORT',
      evidence_status: 'APPROVED',
      evidence_work_product_id: 'wp-1',
      notes: '',
    },
  ],
};

describe('CompliancePage', () => {
  beforeEach(() => {
    mockUseChecklist.mockReset();
    mockLinkEvidenceMutate.mockReset();
    mockUseActiveProject.mockReturnValue({ activeProjectId: 'p1', activeProject: PROJECT, projects: [PROJECT] });
  });

  it('prompts to select a project when none is active', () => {
    mockUseActiveProject.mockReturnValue({ activeProjectId: null, activeProject: undefined, projects: [] });
    mockUseChecklist.mockReturnValue({ data: undefined, isLoading: false });
    render(<CompliancePage />);
    expect(screen.getByText('No project selected')).toBeInTheDocument();
  });

  it('shows a loading state while the checklist is generating', () => {
    mockUseChecklist.mockReturnValue({ data: undefined, isLoading: true });
    render(<CompliancePage />);
    expect(screen.getByText('Generating checklist…')).toBeInTheDocument();
  });

  it('renders regime cards computed from real checklist data', () => {
    mockUseChecklist.mockReturnValue({ data: CHECKLIST, isLoading: false });
    render(<CompliancePage />);
    // UKCA has 1 item, 0 evidenced (MISSING) -> "0 / 1 requirements"
    expect(screen.getByText('0 / 1 requirements')).toBeInTheDocument();
    // CE has 1 item, 1 evidenced (APPROVED) -> "1 / 1 requirements"
    expect(screen.getByText('1 / 1 requirements')).toBeInTheDocument();
  });

  it('lists only missing items in the Missing Evidence panel', () => {
    mockUseChecklist.mockReturnValue({ data: CHECKLIST, isLoading: false });
    render(<CompliancePage />);
    expect(screen.getByText('Missing Evidence · 1')).toBeInTheDocument();
    // The missing item renders in both the Missing Evidence panel and the
    // full Checklist panel (which lists everything).
    expect(screen.getAllByText('Enclosure rated for creepage/clearance').length).toBe(2);
    // The approved item appears only in the full Checklist panel, not
    // duplicated into Missing Evidence.
    expect(screen.getAllByText('Radiated emissions within limit').length).toBe(1);
  });

  it('does not fabricate content unrelated to the design spec mock (no RoHS table, no fake gate badges)', () => {
    mockUseChecklist.mockReturnValue({ data: CHECKLIST, isLoading: false });
    render(<CompliancePage />);
    expect(screen.queryByText('ROHS COMPLIANCE')).not.toBeInTheDocument();
    expect(screen.queryByText('AT-RISK')).not.toBeInTheDocument();
    expect(screen.queryByText('W3 Gate Check')).not.toBeInTheDocument();
  });

  it('opens an add-evidence form for a missing item and submits it', async () => {
    mockUseChecklist.mockReturnValue({ data: CHECKLIST, isLoading: false });
    render(<CompliancePage />);

    // The missing item renders in both the "Missing Evidence" panel and the
    // full "Checklist" panel — click the first (Missing Evidence) instance.
    // Expanding it must not also expand the other panel's copy of the row.
    fireEvent.click(screen.getAllByText('Enclosure rated for creepage/clearance')[0]!);
    expect(await screen.findAllByPlaceholderText('Evidence title')).toHaveLength(1);
    const titleInput = screen.getByPlaceholderText('Evidence title');
    fireEvent.change(titleInput, { target: { value: 'Enclosure test report' } });
    fireEvent.click(screen.getByRole('button', { name: 'Link evidence' }));

    await waitFor(() => {
      expect(mockLinkEvidenceMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          checklist_item_id: 'UKCA-SAF-001',
          title: 'Enclosure test report',
        }),
        expect.anything(),
      );
    });
  });
});
