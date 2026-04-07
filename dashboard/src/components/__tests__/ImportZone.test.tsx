import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act } from '../../test/test-utils';
import { ImportZone } from '../ImportZone';
import * as useImportModule from '../../hooks/use-import';
import type { ImportWorkProductResponse } from '../../types/twin';

function makeMockMutation(overrides = {}) {
  return {
    mutate: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
    data: undefined,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useImportModule.useImportWorkProduct>;
}

function makeFile(name: string) {
  return new File(['content'], name, { type: 'application/octet-stream' });
}

const MOCK_RESPONSE: ImportWorkProductResponse = {
  id: 'wp-1',
  name: 'chassis.step',
  domain: 'mechanical',
  wp_type: 'cad_model',
  file_path: '/uploads/chassis.step',
  content_hash: 'abc123',
  format: 'step',
  metadata: { components: '5', bounding_box: '100x50x30mm' },
  project_id: 'proj-1',
  created_at: '2026-03-25T00:00:00Z',
};

describe('ImportZone', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders idle drop zone with instructions', () => {
    vi.spyOn(useImportModule, 'useImportWorkProduct').mockReturnValue(makeMockMutation());
    render(<ImportZone />);
    expect(screen.getByTestId('import-zone')).toBeInTheDocument();
    expect(screen.getByText(/drag & drop/i)).toBeInTheDocument();
    expect(screen.getByText(/max 100 mb/i)).toBeInTheDocument();
  });

  it('rejects unsupported file types and shows error', async () => {
    vi.spyOn(useImportModule, 'useImportWorkProduct').mockReturnValue(makeMockMutation());
    render(<ImportZone />);

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    await act(async () => {
      Object.defineProperty(input, 'files', { value: [makeFile('design.pdf')], configurable: true });
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(screen.getByTestId('import-error')).toBeInTheDocument();
    expect(screen.getByText(/unsupported file type/i)).toBeInTheDocument();
  });

  it('rejects files over 100 MB', async () => {
    vi.spyOn(useImportModule, 'useImportWorkProduct').mockReturnValue(makeMockMutation());
    render(<ImportZone />);

    const bigFile = makeFile('big.step');
    Object.defineProperty(bigFile, 'size', { value: 101 * 1024 * 1024, configurable: true });

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    await act(async () => {
      Object.defineProperty(input, 'files', { value: [bigFile], configurable: true });
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(screen.getByTestId('import-error')).toBeInTheDocument();
    expect(screen.getByText(/too large/i)).toBeInTheDocument();
  });

  it('calls mutate with FormData for a valid .step file', async () => {
    const mutate = vi.fn();
    vi.spyOn(useImportModule, 'useImportWorkProduct').mockReturnValue(makeMockMutation({ mutate }));
    render(<ImportZone projectId="proj-1" />);

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    await act(async () => {
      Object.defineProperty(input, 'files', { value: [makeFile('part.step')], configurable: true });
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(mutate).toHaveBeenCalledOnce();
    const [vars] = mutate.mock.calls[0] as [{ formData: FormData }];
    expect(vars.formData.get('file')).toBeTruthy();
    expect(vars.formData.get('project_id')).toBe('proj-1');
  });

  it('shows success card with metadata after import', () => {
    const mutate = vi.fn((_vars: unknown, cbs: { onSuccess: (d: ImportWorkProductResponse) => void }) => {
      cbs.onSuccess(MOCK_RESPONSE);
    });
    vi.spyOn(useImportModule, 'useImportWorkProduct').mockReturnValue(makeMockMutation({ mutate }));
    render(<ImportZone />);

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    act(() => {
      Object.defineProperty(input, 'files', { value: [makeFile('chassis.step')], configurable: true });
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(screen.getByTestId('import-success-card')).toBeInTheDocument();
    expect(screen.getByText('Import successful')).toBeInTheDocument();
    expect(screen.getByText('mechanical')).toBeInTheDocument();
  });

  it('calls onSuccess callback and shows "Import another" button', () => {
    const onSuccess = vi.fn();
    const mutate = vi.fn((_vars: unknown, cbs: { onSuccess: (d: ImportWorkProductResponse) => void }) => {
      cbs.onSuccess(MOCK_RESPONSE);
    });
    vi.spyOn(useImportModule, 'useImportWorkProduct').mockReturnValue(makeMockMutation({ mutate }));
    render(<ImportZone onSuccess={onSuccess} />);

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    act(() => {
      Object.defineProperty(input, 'files', { value: [makeFile('chassis.step')], configurable: true });
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(onSuccess).toHaveBeenCalledWith(MOCK_RESPONSE);
    expect(screen.getByText(/import another/i)).toBeInTheDocument();
  });
});
