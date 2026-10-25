import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '../../../test/test-utils';
import type { DesignFilesView as DesignFiles, TwinNode } from '../../../types/twin';

vi.mock('../../../api/endpoints/twin', () => ({
  fetchNodeNamedFileText: vi.fn(),
  nodeNamedFileUrl: (id: string, path: string) => `/api/v1/twin/nodes/${id}/files/${path}`,
}));

vi.mock('../parsers/gerberToSvg', () => ({
  gerberToSvg: (text: string) =>
    text === 'EMPTY'
      ? { svg: '', empty: true, viewBox: null }
      : { svg: `<svg viewBox="0 0 10 10"><g>${text}</g></svg>`, empty: false, viewBox: [0, -10, 10, 10] },
  unionViewBox: () => [0, -10, 20, 20],
  withViewBox: (svg: string) => svg,
}));

import { fetchNodeNamedFileText } from '../../../api/endpoints/twin';
import { DesignFilesView } from '../engines/DesignFilesView';

const mockFetch = vi.mocked(fetchNodeNamedFileText);

function node(wp_type: string, designFiles: Partial<DesignFiles>): TwinNode {
  return {
    id: 'wp-1',
    name: 'Drone FC',
    type: 'work_product',
    domain: 'electronics',
    status: 'valid',
    properties: { wp_type, item_key: 'SCH-DRONE-FC', item_revision: 2 },
    updatedAt: '2026-10-25T10:00:00Z',
    designFiles: { files: [], previews: [], layers: [], buildOutputs: [], sources: [], ...designFiles },
  };
}

beforeEach(() => mockFetch.mockReset());

describe('DesignFilesView (FORGE-687)', () => {
  it('shows each schematic sheet preview and switches between them', () => {
    render(
      <DesignFilesView
        node={node('schematic', {
          files: [{ path: 'fc.kicad_sch', bytes: 2048 }],
          previews: ['preview/fc.svg', 'preview/fc-power.svg'],
          sources: ['bom-1'],
        })}
        mode="modal"
      />,
    );
    expect(screen.getByText('Schematic')).toBeInTheDocument();
    expect(screen.getByText('SCH-DRONE-FC@2')).toBeInTheDocument();
    expect(screen.getByText('made from 1')).toBeInTheDocument();
    const img = screen.getByRole('img');
    expect(img).toHaveAttribute('src', '/api/v1/twin/nodes/wp-1/files/preview/fc.svg');
    fireEvent.click(screen.getByRole('tab', { name: 'fc-power.svg' }));
    expect(screen.getByRole('img')).toHaveAttribute('src', '/api/v1/twin/nodes/wp-1/files/preview/fc-power.svg');
  });

  it('says why a board has no preview', () => {
    render(
      <DesignFilesView
        node={node('pcb_layout', { files: [{ path: 'fc.kicad_pcb' }], previewError: 'kicad adapter unreachable' })}
        mode="panel"
      />,
    );
    expect(screen.getByText(/kicad adapter unreachable/)).toBeInTheDocument();
  });

  it('stacks the drawable gerber layers and lets one be hidden', async () => {
    mockFetch.mockImplementation(async (_id: string, path: string) => (path === 'b.gbr' ? 'EMPTY' : path));
    render(
      <DesignFilesView
        node={node('gerber', {
          files: [{ path: 'fc-F_Cu.gtl' }, { path: 'fc-Edge_Cuts.gm1' }, { path: 'b.gbr' }],
          layers: [
            { path: 'fc-F_Cu.gtl', layer: 'copper' },
            { path: 'fc-Edge_Cuts.gm1', layer: 'outline' },
            { path: 'fc-job.gbrjob', layer: 'job' },
            { path: 'b.gbr', layer: 'silkscreen' },
          ],
        })}
        mode="modal"
      />,
    );
    const stack = await screen.findByTestId('design-gerber-stack');
    expect(mockFetch).not.toHaveBeenCalledWith('wp-1', 'fc-job.gbrjob');
    expect(stack.querySelectorAll('img')).toHaveLength(2);
    expect(screen.getByText('not drawable')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show fc-F_Cu.gtl' }));
    expect(stack.querySelectorAll('img')).toHaveLength(1);
  });

  it('reads firmware sources one at a time and offers the build output', async () => {
    mockFetch.mockImplementation(async (_id, path) => `/* ${path} */`);
    render(
      <DesignFilesView
        node={node('firmware_source', {
          files: [{ path: 'src/main.c' }, { path: 'platformio.ini' }, { path: '.pio/build/f405/firmware.elf' }],
          buildOutputs: ['.pio/build/f405/firmware.elf'],
        })}
        mode="modal"
      />,
    );
    expect(await screen.findByText('/* src/main.c */')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'firmware.elf' })).toHaveAttribute(
      'href',
      '/api/v1/twin/nodes/wp-1/files/.pio/build/f405/firmware.elf',
    );
    fireEvent.click(screen.getByRole('button', { name: 'platformio.ini' }));
    await waitFor(() => expect(screen.getByText('/* platformio.ini */')).toBeInTheDocument());
  });

  it('shows a pin map as a table', async () => {
    mockFetch.mockResolvedValue('pin,signal\nPA5,LED\n');
    render(<DesignFilesView node={node('pinmap', { files: [{ path: 'pinmap.csv' }] })} mode="panel" />);
    expect(await screen.findByText('PA5')).toBeInTheDocument();
  });
});
