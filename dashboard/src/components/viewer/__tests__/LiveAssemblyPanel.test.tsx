import { describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../../test/test-utils';

import { LiveAssemblyPanel } from '../LiveAssemblyPanel';
import type { LiveState } from '../../../hooks/use-live-assembly';

// Three needs WebGL, which jsdom does not have. The scene is the part under
// test elsewhere (it only loads GLBs and applies transforms); here it is a stub.
vi.mock('../LiveAssemblyScene', () => ({
  default: ({ parts }: { parts: unknown[] }) => <div data-testid="scene-stub">{parts.length} parts</div>,
}));

const live = vi.hoisted(() => ({
  value: { state: { seq: 0, assemblies: [] } as LiveState, connected: false },
}));
vi.mock('../../../hooks/use-live-assembly', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../hooks/use-live-assembly')>()),
  useLiveAssembly: () => live.value,
}));

describe('LiveAssemblyPanel', () => {
  it('says it cannot see when the stream is not connected, not that nothing is built', () => {
    live.value = { state: { seq: 0, assemblies: [] }, connected: false };
    render(<LiveAssemblyPanel projectId="p1" onClose={() => {}} />);
    expect(screen.getByTestId('live-assembly-offline')).toHaveTextContent('not the same as nothing');
    expect(screen.queryByTestId('live-assembly-empty')).toBeNull();
  });

  it('says nothing is being assembled once connected and empty', () => {
    live.value = { state: { seq: 0, assemblies: [] }, connected: true };
    render(<LiveAssemblyPanel projectId="p1" onClose={() => {}} />);
    expect(screen.getByTestId('live-assembly-empty')).toHaveTextContent('twin.place_part');
    expect(screen.queryByTestId('live-assembly-offline')).toBeNull();
  });

  it('lists each placed part with its transform and draws the scene', async () => {
    live.value = {
      connected: true,
      state: {
        seq: 2,
        assemblies: [
          {
            name: 'Pi5 Enclosure',
            parts: [
              { nodeId: 'a', name: 'Base', position: [0, 0, 0], rotation: [0, 0, 0], seq: 1 },
              { nodeId: 'b', name: 'Lid', position: [91, 0, 25.6], rotation: [0, 180, 0], seq: 2 },
            ],
          },
        ],
      },
    };
    render(<LiveAssemblyPanel projectId="p1" onClose={() => {}} />);
    const list = screen.getByRole('list', { name: 'Parts of Pi5 Enclosure' });
    expect(list).toHaveTextContent('Base');
    expect(list).toHaveTextContent('Lid');
    expect(list).toHaveTextContent('at 91, 0, 25.6 mm, rotated 0, 180, 0 deg');
    expect(await screen.findByTestId('scene-stub')).toHaveTextContent('2 parts');
  });

  it('closes', async () => {
    live.value = { state: { seq: 0, assemblies: [] }, connected: true };
    const onClose = vi.fn();
    render(<LiveAssemblyPanel projectId="p1" onClose={onClose} />);
    await userEvent.click(screen.getByRole('button', { name: 'Close live assembly' }));
    expect(onClose).toHaveBeenCalled();
  });
});
