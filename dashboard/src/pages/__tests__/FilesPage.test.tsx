import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('../../hooks/use-links', () => ({
  useAllLinks: vi.fn(),
  useDeleteLink: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncNode: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { FilesPage } from '../FilesPage';
import { useAllLinks } from '../../hooks/use-links';

const mockUseAllLinks = vi.mocked(useAllLinks);

const LINK = {
  id: 'l1',
  node_id: 'n1',
  file_path: 'schematic.kicad_sch',
  tool: 'kicad' as const,
  status: 'synced' as const,
  last_synced_at: new Date().toISOString(),
};

describe('FilesPage', () => {
  it('shows an honest empty state, not fabricated sync pipeline rows, when there are no links', () => {
    mockUseAllLinks.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useAllLinks>);
    render(<FilesPage />);
    expect(screen.getByText('No source files linked yet')).toBeInTheDocument();
    expect(screen.getByText('No sync activity yet')).toBeInTheDocument();
    // These were hardcoded placeholder rows unrelated to the real (empty) data.
    expect(screen.queryByText('PRD.md')).not.toBeInTheDocument();
    expect(screen.queryByText('constraints.json')).not.toBeInTheDocument();
    expect(screen.queryByText('bom.csv')).not.toBeInTheDocument();
  });

  it('renders real links in both the File Links and Sync Pipeline panels', () => {
    mockUseAllLinks.mockReturnValue({ data: [LINK], isLoading: false } as unknown as ReturnType<typeof useAllLinks>);
    render(<FilesPage />);
    expect(screen.getAllByText('schematic.kicad_sch').length).toBeGreaterThan(0);
    expect(screen.queryByText('No sync activity yet')).not.toBeInTheDocument();
  });
});
