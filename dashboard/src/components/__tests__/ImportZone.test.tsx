import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ImportZone } from '../ImportZone';

// ── Mocks ──────────────────────────────────────────────────────────────────

vi.mock('../../hooks/use-import', () => ({
  useImportWorkProduct: vi.fn(),
}));

vi.mock('../ui/Toast', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

import { useImportWorkProduct } from '../../hooks/use-import';

const mockMutate = vi.fn();
const defaultMutation = {
  mutate: mockMutate,
  reset: vi.fn(),
  isPending: false,
  isSuccess: false,
  isError: false,
};

function makeMutation(overrides = {}) {
  return { ...defaultMutation, ...overrides };
}

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function makeFile(name: string, sizeBytes = 1024): File {
  const content = new Uint8Array(sizeBytes);
  return new File([content], name, { type: 'application/octet-stream' });
}

// ── Tests ──────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  (useImportWorkProduct as ReturnType<typeof vi.fn>).mockReturnValue(makeMutation());
});

describe('ImportZone', () => {
  it('renders idle upload zone', () => {
    render(<ImportZone />, { wrapper });

    expect(screen.getByTestId('import-zone')).toBeDefined();
    expect(screen.getByTestId('file-input')).toBeDefined();
    // Drop zone contains instructional text
    expect(screen.getByText(/drag.*drop/i)).toBeDefined();
    expect(screen.getByText(/max 100 MB/i)).toBeDefined();
  });

  it('shows error for unsupported file type', async () => {
    render(<ImportZone />, { wrapper });

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    const file = makeFile('drawing.pdf');

    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByTestId('import-error')).toBeDefined();
    });

    expect(screen.getByText(/unsupported file type/i)).toBeDefined();
  });

  it('shows error for file too large', async () => {
    render(<ImportZone />, { wrapper });

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    // Create a file object that reports a size > 100 MB
    const bigFile = makeFile('model.step', 1024);
    Object.defineProperty(bigFile, 'size', { value: 101 * 1024 * 1024 });

    fireEvent.change(input, { target: { files: [bigFile] } });

    await waitFor(() => {
      expect(screen.getByTestId('import-error')).toBeDefined();
    });

    expect(screen.getByText(/too large/i)).toBeDefined();
  });

  it('calls mutation on valid file drop via input', async () => {
    render(<ImportZone projectId="proj-123" />, { wrapper });

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    const file = makeFile('chassis.step');

    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(mockMutate).toHaveBeenCalledOnce();
    });

    const [callArg] = mockMutate.mock.calls[0] as [{ formData: FormData; onProgress: unknown }][];
    const args = callArg as unknown as { formData: FormData; onProgress: unknown };
    expect(args.formData.get('file')).toEqual(file);
    expect(args.formData.get('project_id')).toBe('proj-123');
  });

  it('shows metadata card on success', () => {
    const successResult = {
      id: 'wp-001',
      name: 'chassis.step',
      domain: 'mechanical',
      wp_type: 'CAD_MODEL',
      file_path: '/uploads/chassis.step',
      content_hash: 'abc123',
      format: 'step',
      metadata: { units: 'mm', volume: '12.5', mass: '0.34' },
      project_id: null,
      created_at: '2026-03-25T10:00:00Z',
    };

    // Mock mutation to be in success state
    (useImportWorkProduct as ReturnType<typeof vi.fn>).mockReturnValue({
      ...makeMutation({ isSuccess: true }),
      mutate: vi.fn((_args, callbacks) => {
        // Immediately invoke onSuccess
        if (callbacks?.onSuccess) callbacks.onSuccess(successResult);
      }),
      reset: vi.fn(),
    });

    render(<ImportZone />, { wrapper });

    // Trigger a valid file drop
    const input = screen.getByTestId('file-input') as HTMLInputElement;
    const file = makeFile('chassis.step');
    fireEvent.change(input, { target: { files: [file] } });

    // After onSuccess fires, success card should appear
    expect(screen.getByTestId('import-success-card')).toBeDefined();
    expect(screen.getByText('chassis.step')).toBeDefined();
    expect(screen.getByText('mechanical')).toBeDefined();
    expect(screen.getByText('CAD_MODEL')).toBeDefined();
    // Metadata entries rendered
    expect(screen.getByText('units')).toBeDefined();
  });

  it('shows error message when mutation fails', async () => {
    (useImportWorkProduct as ReturnType<typeof vi.fn>).mockReturnValue({
      ...makeMutation(),
      mutate: vi.fn((_args, callbacks) => {
        if (callbacks?.onError) callbacks.onError(new Error('Server error 500'));
      }),
      reset: vi.fn(),
    });

    render(<ImportZone />, { wrapper });

    const input = screen.getByTestId('file-input') as HTMLInputElement;
    const file = makeFile('board.kicad_sch');
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByTestId('import-error')).toBeDefined();
    });

    expect(screen.getByText(/server error 500/i)).toBeDefined();
  });
});
