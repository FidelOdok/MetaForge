import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('../../hooks/use-bom', () => ({
  useBom: vi.fn(),
}));

vi.mock('../../hooks/use-scoped-chat', () => ({
  useScopedChat: () => ({
    thread: null,
    messages: [],
    isTyping: false,
    sendMessage: vi.fn(),
    createThread: vi.fn(),
    isLoading: false,
    isCreating: false,
    isSending: false,
  }),
}));

import { BomPage } from '../BomPage';
import { useBom } from '../../hooks/use-bom';

const mockUseBom = vi.mocked(useBom);

describe('BomPage', () => {
  it('shows loading state', () => {
    mockUseBom.mockReturnValue({ data: undefined, isLoading: true } as ReturnType<typeof useBom>);
    const { container } = render(<BomPage />);
    // KC renders animate-pulse skeleton rows (no data-testid)
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
  });

  it('shows empty state', () => {
    mockUseBom.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useBom>);
    render(<BomPage />);
    expect(screen.getByText('No components')).toBeInTheDocument();
  });

  it('renders BOM table', () => {
    mockUseBom.mockReturnValue({
      data: [
        { id: 'b1', designator: 'U1', partNumber: 'STM32F405', description: 'MCU', manufacturer: 'STM', quantity: 1, unitPrice: 8.5, status: 'available', category: 'IC', projectId: 'p1' },
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useBom>);
    render(<BomPage />);
    expect(screen.getByText('U1')).toBeInTheDocument();
    expect(screen.getByText('STM32F405')).toBeInTheDocument();
  });
});
