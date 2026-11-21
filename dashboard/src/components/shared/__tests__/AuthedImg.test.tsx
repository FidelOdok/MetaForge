import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '../../../test/test-utils';
import type { TwinNode } from '../../../types/twin';

const get = vi.fn();
vi.mock('../../../api/client', () => ({ default: { get: (...a: unknown[]) => get(...a) } }));

import { AuthedImg } from '../AuthedImg';
import { isBoardSheet, isEngineering, isVisual } from '../../create/ProductStage';

describe('AuthedImg', () => {
  const created: string[] = [];
  beforeEach(() => {
    get.mockReset();
    created.length = 0;
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => {
      const url = `blob:test/${created.length}`;
      created.push(url);
      return url;
    });
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
  });
  afterEach(() => vi.restoreAllMocks());

  it('loads the file through the API client, which carries the token, and shows a local URL', async () => {
    get.mockResolvedValue({ data: new Blob(['<svg/>'], { type: 'image/svg+xml' }) });
    render(<AuthedImg src="https://api.example/v1/twin/nodes/n1/files/preview/board.svg" alt="LED board layout" />);
    const img = await screen.findByRole('img', { name: 'LED board layout' });
    expect(get).toHaveBeenCalledWith('https://api.example/v1/twin/nodes/n1/files/preview/board.svg', {
      responseType: 'blob',
    });
    // Never the gateway URL itself: an <img> could not send the token.
    expect(img.getAttribute('src')).toBe('blob:test/0');
  });

  it('says why when the file cannot be loaded, instead of a bare alt text', async () => {
    get.mockRejectedValue({ response: { status: 401 } });
    render(<AuthedImg src="https://api.example/v1/twin/nodes/n1/files/x.svg" alt="LED board schematic" />);
    await waitFor(() => expect(screen.getByText(/could not load it \(HTTP 401\)/)).toBeInTheDocument());
  });

  it('releases the object URL when it goes away', async () => {
    get.mockResolvedValue({ data: new Blob(['x']) });
    const { unmount } = render(<AuthedImg src="https://api.example/v1/a.svg" alt="a" />);
    await screen.findByRole('img', { name: 'a' });
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test/0');
  });
});

describe('a board layout is not the product model', () => {
  const node = (wp_type: string, board?: string): TwinNode =>
    ({
      id: `${wp_type}-${board ?? 'none'}`,
      name: 'x',
      type: 'work_product',
      properties: { wp_type },
      designFiles: { files: [], previews: ['preview/x.svg'], ...(board ? { board } : {}) },
    }) as unknown as TwinNode;

  it("leaves a board's layout to the PCB view", () => {
    const layout = node('pcb_layout', 'LED board');
    expect(isBoardSheet(layout)).toBe(true);
    expect(isVisual(layout)).toBe(false);
    expect(isEngineering(layout)).toBe(false);
  });

  it('still shows a layout that names no board (an electronics-only product)', () => {
    const layout = node('pcb_layout');
    expect(isBoardSheet(layout)).toBe(false);
    expect(isVisual(layout)).toBe(true);
    expect(isEngineering(layout)).toBe(true);
  });
});
