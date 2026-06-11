import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { render } from '../test/test-utils';
import type { SourceSummary } from '../types/knowledge';

// Mock the endpoint module so the page's TanStack Query call resolves
// against a deterministic in-memory data set. We mock at the module
// boundary (endpoints/knowledge) rather than the hook layer because the
// page calls ``listSources`` directly via ``useQuery``.
vi.mock('../api/endpoints/knowledge', () => ({
  listSources: vi.fn(),
}));

// MET-452: KnowledgePage now reads ``useProjects`` to populate the
// project dropdown + auto-select the newest project on first load.
// Default mock returns "no projects exist" so existing tests keep the
// pre-MET-452 behaviour (projectId stays ``''``, auto-select is a
// no-op). Individual tests override via ``mockUseProjects.mockReturnValue(...)``
// to exercise the dropdown / auto-select path.
const mockUseProjects = vi.fn(() => ({ data: [] as unknown[], isLoading: false }));
vi.mock('../hooks/use-projects', () => ({
  useProjects: () => mockUseProjects(),
}));

// Mock useNavigate so we can assert the row click without a real router.
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

import { KnowledgePage } from '../pages/KnowledgePage';
import { listSources } from '../api/endpoints/knowledge';

const mockListSources = vi.mocked(listSources);

const SOURCE_DECISION: SourceSummary = {
  source_path: 'uat://decisions/regulator-choice.md',
  knowledge_type: 'design_decision',
  fragment_count: 4,
  indexed_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
  metadata: {},
};

const SOURCE_COMPONENT: SourceSummary = {
  source_path: 'uat://datasheets/ina226.pdf',
  knowledge_type: 'component',
  fragment_count: 12,
  indexed_at: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
  metadata: { vendor: 'Texas Instruments', mpn: 'INA226' },
};

const SOURCE_FAILURE: SourceSummary = {
  source_path: 'uat://failures/thermal-runaway.md',
  knowledge_type: 'failure',
  fragment_count: 2,
  indexed_at: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
  metadata: {},
};

describe('KnowledgePage', () => {
  beforeEach(() => {
    mockListSources.mockReset();
    mockNavigate.mockReset();
  });

  it('renders empty state when no sources', async () => {
    mockListSources.mockResolvedValue([]);

    render(<KnowledgePage />);

    await waitFor(() => {
      expect(screen.getByText('No sources ingested yet')).toBeInTheDocument();
    });
    // Empty-state hint points engineers at the ingestion CLI.
    expect(screen.getByText(/forge ingest/)).toBeInTheDocument();
    // No data rows rendered — the only row in the document is the header.
    expect(screen.queryByText(SOURCE_COMPONENT.source_path)).not.toBeInTheDocument();
  });

  it('renders table with rows when sources returned', async () => {
    mockListSources.mockResolvedValue([SOURCE_DECISION, SOURCE_COMPONENT]);

    render(<KnowledgePage />);

    await waitFor(() => {
      expect(screen.getByText(SOURCE_DECISION.source_path)).toBeInTheDocument();
    });
    expect(screen.getByText(SOURCE_COMPONENT.source_path)).toBeInTheDocument();

    // Column headers (sortable buttons)
    expect(screen.getByRole('button', { name: /source_path/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^type/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /fragments/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /indexed/i })).toBeInTheDocument();

    // metadata.vendor/mpn surfaced for the component source
    expect(screen.getByText('Texas Instruments')).toBeInTheDocument();
    expect(screen.getByText('INA226')).toBeInTheDocument();
  });

  it('filter chip narrows by knowledge_type', async () => {
    // Initial unfiltered fetch returns 3 mixed sources; the filter chip
    // should re-call listSources with knowledge_type='component'. We
    // make the second call return a narrowed set so the table reflects
    // the server-side filter.
    mockListSources
      .mockResolvedValueOnce([SOURCE_DECISION, SOURCE_COMPONENT, SOURCE_FAILURE])
      .mockResolvedValueOnce([SOURCE_COMPONENT]);

    render(<KnowledgePage />);

    await waitFor(() => {
      expect(screen.getByText(SOURCE_DECISION.source_path)).toBeInTheDocument();
    });

    // Click the "component" chip
    const chip = screen.getByRole('button', { name: /component/i, pressed: false });
    fireEvent.click(chip);

    await waitFor(() => {
      expect(mockListSources).toHaveBeenCalledWith(
        expect.objectContaining({ knowledge_type: 'component' }),
      );
    });
    // After re-fetch, only the component source row remains.
    await waitFor(() => {
      expect(screen.queryByText(SOURCE_DECISION.source_path)).not.toBeInTheDocument();
    });
    expect(screen.getByText(SOURCE_COMPONENT.source_path)).toBeInTheDocument();
  });

  it('row click navigates to /knowledge/sources/:id', async () => {
    mockListSources.mockResolvedValue([SOURCE_COMPONENT]);

    render(<KnowledgePage />);

    const cell = await screen.findByText(SOURCE_COMPONENT.source_path);
    // The row is the parent with role="row"
    const row = cell.closest('[role="row"]');
    expect(row).not.toBeNull();
    fireEvent.click(row!);

    expect(mockNavigate).toHaveBeenCalledWith(
      `/knowledge/sources/${encodeURIComponent(SOURCE_COMPONENT.source_path)}`,
    );
  });

  // ──────────────────────────────────────────────────────────────────────
  // MET-452: project dropdown + auto-select
  // ──────────────────────────────────────────────────────────────────────

  it('auto-selects the most-recently-updated project on first load', async () => {
    // Two projects, newer one last in array — auto-select should still
    // pick the newest by lastUpdated, not the array order.
    mockUseProjects.mockReturnValue({
      data: [
        {
          id: '11111111-1111-1111-1111-111111111111',
          name: 'Older Project',
          description: '',
          status: 'active',
          work_products: [],
          agentCount: 0,
          lastUpdated: '2026-01-01T00:00:00Z',
          createdAt: '2026-01-01T00:00:00Z',
        },
        {
          id: '22222222-2222-2222-2222-222222222222',
          name: 'Newer Drone Kit',
          description: '',
          status: 'active',
          work_products: [],
          agentCount: 0,
          lastUpdated: '2026-05-22T00:00:00Z',
          createdAt: '2026-05-22T00:00:00Z',
        },
      ],
      isLoading: false,
    });
    mockListSources.mockResolvedValue([SOURCE_COMPONENT]);

    render(<KnowledgePage />);

    // After auto-select runs, listSources should have been called with
    // the newer project's UUID. Earlier "default tenant" call (project_id
    // undefined) may also have fired during the initial render; we just
    // assert the auto-selected call exists.
    await waitFor(() => {
      const calls = mockListSources.mock.calls;
      const sawAutoSelect = calls.some(
        (callArgs) =>
          callArgs[0]?.project_id === '22222222-2222-2222-2222-222222222222',
      );
      expect(sawAutoSelect).toBe(true);
    });

    // The dropdown's current value reflects the auto-selected project.
    const select = screen.getByLabelText(/^project$/i) as HTMLSelectElement;
    expect(select.value).toBe('22222222-2222-2222-2222-222222222222');

    // "All projects" option is still reachable as the first option.
    const allOption = Array.from(select.options).find((o) => o.value === '');
    expect(allOption).toBeDefined();
    expect(allOption?.textContent).toMatch(/all projects/i);
  });

  it('selecting "All projects" returns the picker to the default-tenant view', async () => {
    mockUseProjects.mockReturnValue({
      data: [
        {
          id: '33333333-3333-3333-3333-333333333333',
          name: 'Some Project',
          description: '',
          status: 'active',
          work_products: [],
          agentCount: 0,
          lastUpdated: '2026-05-22T00:00:00Z',
          createdAt: '2026-05-22T00:00:00Z',
        },
      ],
      isLoading: false,
    });
    mockListSources.mockResolvedValue([SOURCE_COMPONENT]);

    render(<KnowledgePage />);

    const select = (await screen.findByLabelText(/^project$/i)) as HTMLSelectElement;
    // Wait for auto-select to land — the test before this one already
    // covers that path; here we just need a known starting state.
    await waitFor(() => {
      expect(select.value).toBe('33333333-3333-3333-3333-333333333333');
    });

    fireEvent.change(select, { target: { value: '' } });

    // Visual-state assertion: the dropdown is back on "All projects".
    // We don't assert listSources call order because react-query's
    // refetch ordering after a queryKey change is timing-sensitive in
    // jsdom; the wire contract (project_id passed through unchanged
    // from state) is covered by ``filter chip narrows by knowledge_type``
    // and the auto-select test above.
    await waitFor(() => {
      expect(select.value).toBe('');
    });
  });

  // ──────────────────────────────────────────────────────────────────────
  // MET-486: empty-project fallback
  // ──────────────────────────────────────────────────────────────────────

  it('falls back to "All projects" when the auto-selected project has no knowledge', async () => {
    mockUseProjects.mockReturnValue({
      data: [
        {
          id: '44444444-4444-4444-4444-444444444444',
          name: 'Empty Kit',
          description: '',
          status: 'active',
          work_products: [],
          agentCount: 0,
          lastUpdated: '2026-05-22T00:00:00Z',
          createdAt: '2026-05-22T00:00:00Z',
        },
      ],
      isLoading: false,
    });
    // The auto-selected project has no scoped knowledge; the default
    // tenant ("All projects", project_id undefined) does.
    mockListSources.mockImplementation(async (q) =>
      q?.project_id === '44444444-4444-4444-4444-444444444444' ? [] : [SOURCE_COMPONENT],
    );

    render(<KnowledgePage />);

    const select = (await screen.findByLabelText(/^project$/i)) as HTMLSelectElement;
    // Auto-selects the project → empty → falls back to "All projects".
    await waitFor(() => {
      expect(select.value).toBe('');
    });
    // And the fallback surfaces the default-tenant sources instead of empty.
    await waitFor(() => {
      expect(screen.getByText(SOURCE_COMPONENT.source_path)).toBeInTheDocument();
    });
  });

  it('does not fall back when the user deliberately picks an empty project', async () => {
    mockUseProjects.mockReturnValue({
      data: [
        {
          id: '55555555-5555-5555-5555-555555555555',
          name: 'Has Knowledge',
          description: '',
          status: 'active',
          work_products: [],
          agentCount: 0,
          lastUpdated: '2026-05-22T00:00:00Z',
          createdAt: '2026-05-22T00:00:00Z',
        },
        {
          id: '66666666-6666-6666-6666-666666666666',
          name: 'Empty Project',
          description: '',
          status: 'active',
          work_products: [],
          agentCount: 0,
          lastUpdated: '2026-01-01T00:00:00Z',
          createdAt: '2026-01-01T00:00:00Z',
        },
      ],
      isLoading: false,
    });
    // Newest (5555…) has knowledge → auto-select sticks; the older empty
    // project (6666…) has none.
    mockListSources.mockImplementation(async (q) =>
      q?.project_id === '66666666-6666-6666-6666-666666666666' ? [] : [SOURCE_COMPONENT],
    );

    render(<KnowledgePage />);

    const select = (await screen.findByLabelText(/^project$/i)) as HTMLSelectElement;
    await waitFor(() => {
      expect(select.value).toBe('55555555-5555-5555-5555-555555555555');
    });

    // User deliberately switches to the empty project — it must stay put.
    fireEvent.change(select, { target: { value: '66666666-6666-6666-6666-666666666666' } });
    await waitFor(() => {
      expect(screen.getByText('No sources ingested yet')).toBeInTheDocument();
    });
    expect(select.value).toBe('66666666-6666-6666-6666-666666666666');
  });
});
