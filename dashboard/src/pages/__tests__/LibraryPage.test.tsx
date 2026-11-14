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
  part('hi_link_hlk_pm01', { title: 'Hi-Link HLK-PM01: AC-DC module', family: 'module', manufacturer: 'Hi-Link' }),
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
    const types = screen.getByRole('group', { name: 'Type' });
    fireEvent.click(within(types).getByRole('button', { name: /module/i }));
    expect(screen.getByTestId('library-count')).toHaveTextContent('1 of 3');
    fireEvent.click(within(types).getByRole('button', { name: 'All' }));
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
});
