import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '../../test/test-utils';
import type { LibraryComponent } from '../../api/endpoints/library';

const api = vi.hoisted(() => ({
  listComponents: vi.fn(),
  listMaterials: vi.fn(),
  listMachines: vi.fn(),
  exportLibrary: vi.fn(),
  importLibrary: vi.fn(),
  getComponentMesh: vi.fn(),
}));
vi.mock('../../api/endpoints/library', () => api);
// The 3D view has its own tests; here it only says which part it was given.
vi.mock('../../components/library/PartPreview', () => ({
  PartPreview: ({ part }: { part: LibraryComponent }) => <div data-testid="preview">{part.name}</div>,
  activeMakerModel: (p: LibraryComponent) =>
    (p.models || []).find((m) => (m.status ?? 'active') === 'active') ?? null,
}));

import { LibraryPage } from '../LibraryPage';
import { Toaster } from '../../components/ui/Toast';

function part(name: string, extra: Partial<LibraryComponent> = {}): LibraryComponent {
  return {
    id: `co_${name}`,
    name,
    title: name,
    family: 'electronic',
    origin: 'purchased',
    specs: {},
    notes: '',
    version: 1,
    updated_at: 0,
    ...extra,
  };
}

const PARTS = [
  part('nordic_nrf52840_qiaa_r', {
    title: 'Nordic nRF52840-QIAA-R: BLE SoC',
    manufacturer: 'Nordic',
    form_code: "return [g.box(7, 0.85, 7).name('body [epoxy_black]')];",
  }),
  part('hi_link_hlk_pm01', {
    title: 'Hi-Link HLK-PM01: AC-DC module',
    family: 'module',
    manufacturer: 'Hi-Link',
    notes: 'Smart-lamp market research, 01-power-and-drivers.md section 1 (Mains AC-DC modules), read 2026-11-12.',
  }),
  part('samsung_lm301b', { title: '[ENDING: check status] Samsung LM301B: LED', manufacturer: 'Samsung' }),
];

describe('LibraryPage', () => {
  beforeEach(() => {
    Object.values(api).forEach((f) => f.mockReset());
    api.listComponents.mockResolvedValue(PARTS);
    api.listMaterials.mockResolvedValue([]);
    api.listMachines.mockResolvedValue([]);
  });

  it('lists every component and shows the first in detail', async () => {
    render(<LibraryPage />);
    expect(await screen.findByTestId('library-count')).toHaveTextContent('3 parts');
    expect(screen.getByTestId('library-detail')).toBeInTheDocument();
  });

  it('narrows the list by search, type, 3D and discontinued parts', async () => {
    render(<LibraryPage />);
    await screen.findByTestId('library-count');
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search components' }), { target: { value: 'nordic' } });
    expect(screen.getByTestId('library-count')).toHaveTextContent('1 of 3');
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search components' }), { target: { value: '' } });
    const groups = screen.getByRole('group', { name: 'Category' });
    fireEvent.click(within(groups).getByRole('button', { name: /Power/ }));
    expect(screen.getByTestId('library-count')).toHaveTextContent('1 of 3');
    fireEvent.click(within(groups).getByRole('button', { name: 'All' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Integration level' }), { target: { value: 'L5 Module' } });
    expect(screen.getByTestId('library-count')).toHaveTextContent('1 of 3');
    fireEvent.change(screen.getByRole('combobox', { name: 'Integration level' }), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /3D only/ }));
    expect(screen.getByTestId('library-count')).toHaveTextContent('1 of 3');
    fireEvent.click(screen.getByRole('button', { name: /3D only/ }));
    fireEvent.click(screen.getByRole('button', { name: /Hide discontinued/ }));
    expect(screen.getByTestId('library-count')).toHaveTextContent('2 of 3');
  });

  it('shows the part a person picks, with a discontinued warning where the research found one', async () => {
    render(<LibraryPage />);
    const list = await screen.findByRole('listbox', { name: 'Components' });
    fireEvent.click(within(list).getByText('Samsung LM301B: LED'));
    const detail = screen.getByTestId('library-detail');
    expect(within(detail).getByText(/being discontinued/)).toBeInTheDocument();
    expect(screen.getByTestId('preview')).toHaveTextContent('samsung_lm301b');
  });

  it('loads an export file and says what was added and what was left alone', async () => {
    api.importLibrary.mockResolvedValue({ added: { component: 2 }, skipped: { component: 500 }, failed: [] });
    render(
      <>
        <LibraryPage />
        <Toaster />
      </>,
    );
    await screen.findByTestId('library-count');
    const file = new File([JSON.stringify({ format: 'metaforge-library', version: 1 })], 'lib.json', {
      type: 'application/json',
    });
    fireEvent.change(screen.getByTestId('library-import-file'), { target: { files: [file] } });
    await waitFor(() => expect(api.importLibrary).toHaveBeenCalledWith({ format: 'metaforge-library', version: 1 }));
    expect(await screen.findByText(/Imported 2 entries; 500 already here/)).toBeInTheDocument();
  });

  it('says so when the library cannot be read', async () => {
    api.listComponents.mockRejectedValue(new Error('down'));
    render(<LibraryPage />);
    expect(await screen.findByText('The library could not be read')).toBeInTheDocument();
  });

  it("shows each machine's capabilities, a bench machine's own and those it takes from its model", async () => {
    const src = { source_kind: 'manufacturer_spec', source_ref: 'https://maker.example', needs_review: false };
    api.listMachines.mockResolvedValue([
      {
        id: 'mm_1', layer: 'model', kind: 'fdm_printer', make: 'Prusa', model: 'MK4S', version: 1,
        capabilities: {
          build_volume_mm: { value: [250, 210, 220], unit: 'mm', ...src },
          materials: { value: ['PLA', 'PETG'], unit: '', ...src },
        },
      },
      { id: 'mi_1', layer: 'instance', kind: 'fdm_printer', name: 'Bench MK4S', model_id: 'mm_1', location: 'home', version: 1, capabilities: {} },
    ]);
    render(<LibraryPage />);
    await screen.findByTestId('library-count');
    fireEvent.click(screen.getByRole('tab', { name: /Machines/ }));
    const cards = await screen.findAllByTestId('library-machine');
    const bench = cards[0]!;
    expect(within(bench).getByText('Bench MK4S')).toBeInTheDocument();
    expect(within(bench).getByText('250 x 210 x 220 mm')).toBeInTheDocument();
    expect(within(bench).getAllByText(/from the model/).length).toBe(2);
    expect(within(cards[1]!).getByText('PLA, PETG')).toBeInTheDocument();
    expect(within(cards[1]!).queryByText(/from the model/)).toBeNull();
  });
});
