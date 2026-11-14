import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '../../../test/test-utils';
import type { LibraryComponent } from '../../../api/endpoints/library';

const api = vi.hoisted(() => ({ getComponentMesh: vi.fn(), refuse: null as null | object }));
vi.mock('../../../api/endpoints/library', () => ({
  // A plain function for the refusal, so the rejection is the component's to handle alone.
  getComponentMesh: (name: string) => (api.refuse ? Promise.reject(api.refuse) : api.getComponentMesh(name)),
}));
// jsdom has no WebGL: the canvas stands in as a box that counts what it would draw.
vi.mock('@react-three/fiber', () => ({
  Canvas: ({ children }: { children: React.ReactNode }) => <div data-testid="canvas">{children}</div>,
}));
vi.mock('@react-three/drei', () => ({ OrbitControls: () => null }));

import { PartPreview } from '../PartPreview';

const BASE: LibraryComponent = {
  id: 'co_1', name: 'chip', title: 'Chip', family: 'electronic', origin: 'purchased',
  specs: {}, notes: '', version: 1, updated_at: 0,
};
const MAKER = {
  model_id: 'maker_step', version: '1', engine: 'step', usage: 'u', status: 'active' as const,
  artifacts: [{ path: 'x.step', url: 'https://m.example/x.zip', sha256: 'a'.repeat(64), size: 1, licence: 'none stated' }],
};
const BOX = [0, 0, 0, 7, 0, 0, 7, 0.85, 7, 0, 0.85, 7];

describe('PartPreview', () => {
  beforeEach(() => {
    api.getComponentMesh.mockReset();
    api.refuse = null;
  });
  afterEach(() => {
    delete window.FGAuthored;
  });

  it("draws the maker's model when the part has one, and measures it", async () => {
    api.getComponentMesh.mockResolvedValue({
      parts: [{ name: 'Body', colour: [0.1, 0.1, 0.1], colour_source: 'part', positions: BOX, indices: [0, 1, 2, 0, 2, 3] }],
      triangles: 2, source: {},
    });
    render(<PartPreview part={{ ...BASE, models: [MAKER], form_code: 'return [];' }} />);
    expect(await screen.findByText(/7.0 x 7.0 x 0.8 mm/)).toBeInTheDocument();
    expect(screen.getByTestId('library-preview')).toHaveAttribute('data-source', 'maker');
  });

  it("says why a maker's model is not shown instead of drawing nothing", async () => {
    api.refuse = { response: { status: 422, data: { detail: 'the converter is not running' } } };
    render(<PartPreview part={{ ...BASE, models: [MAKER] }} />);
    expect(await screen.findByText(/Model not shown: the converter is not running/)).toBeInTheDocument();
  });

  it('runs the drawn model through the drawing engine when there is no maker model', async () => {
    const run = vi.fn().mockResolvedValue({ parts: [{ shapes: [{ name: 'body [epoxy_black]', positions: BOX, indices: [0, 1, 2] }] }] });
    window.FGAuthored = { run };
    render(<PartPreview part={{ ...BASE, form_code: "return [g.box(7, 0.85, 7).name('body [epoxy_black]')];" }} />);
    expect(await screen.findByText(/7.0 x 7.0 x 0.8 mm/)).toBeInTheDocument();
    expect(run).toHaveBeenCalledWith([expect.objectContaining({ code: expect.stringContaining('g.box') })], {});
    expect(api.getComponentMesh).not.toHaveBeenCalled();
  });

  it('shows a drawing error from the engine', async () => {
    window.FGAuthored = { run: vi.fn().mockResolvedValue({ parts: [{ error: 'g.box: width must be a positive number' }] }) };
    render(<PartPreview part={{ ...BASE, form_code: 'return [g.box(0, 1, 1)];' }} />);
    expect(await screen.findByText(/width must be a positive number/)).toBeInTheDocument();
  });

  it('says there is no model yet when there is neither', () => {
    render(<PartPreview part={BASE} />);
    expect(screen.getByText('No model yet.')).toBeInTheDocument();
  });
});
