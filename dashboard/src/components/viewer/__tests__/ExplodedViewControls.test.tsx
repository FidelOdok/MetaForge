import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '../../../test/test-utils';
import { ExplodedViewControls } from '../ExplodedViewControls';

const mockSetExplodeFactor = vi.fn();
let mockExplodeFactor = 0;

vi.mock('../../../store/viewer-store', () => ({
  useViewerStore: vi.fn((selector: (s: Record<string, unknown>) => unknown) => {
    const state = {
      explodeFactor: mockExplodeFactor,
      setExplodeFactor: mockSetExplodeFactor,
    };
    return selector(state);
  }),
}));

describe('ExplodedViewControls', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockExplodeFactor = 0;
  });

  it('renders slider and reset button', () => {
    const { getByText, getByRole } = render(<ExplodedViewControls />);
    expect(getByText('Explode')).toBeInTheDocument();
    expect(getByText('0%')).toBeInTheDocument();
    expect(getByRole('slider')).toBeInTheDocument();
  });

  it('slider updates store value', () => {
    const { getByRole } = render(<ExplodedViewControls />);
    const slider = getByRole('slider');

    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    )!.set!;
    nativeInputValueSetter.call(slider, '50');
    slider.dispatchEvent(new Event('change', { bubbles: true }));

    expect(mockSetExplodeFactor).toHaveBeenCalledWith(0.5);
  });
});
