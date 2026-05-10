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
});
