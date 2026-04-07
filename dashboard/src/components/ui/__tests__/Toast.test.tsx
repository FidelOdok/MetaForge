import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, act } from '../../../test/test-utils';
import { Toaster, useToast, useToastStore } from '../Toast';
import { renderHook } from '@testing-library/react';

describe('Toaster', () => {
  beforeEach(() => {
    act(() => { useToastStore.setState({ toasts: [] }); });
  });

  it('renders nothing when no toasts', () => {
    const { container } = render(<Toaster />);
    expect(container.firstChild).toBeNull();
  });

  it('shows a toast after useToast().success is called', () => {
    // Render toaster and hook together
    function Wrapper() {
      const toast = useToast();
      return (
        <>
          <button onClick={() => toast.success('It worked!')}>trigger</button>
          <Toaster />
        </>
      );
    }

    render(<Wrapper />);
    act(() => {
      screen.getByText('trigger').click();
    });

    expect(screen.getByText('It worked!')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('shows an error toast with correct text', () => {
    function Wrapper() {
      const toast = useToast();
      return (
        <>
          <button onClick={() => toast.error('Something failed')}>trigger</button>
          <Toaster />
        </>
      );
    }

    render(<Wrapper />);
    act(() => {
      screen.getByText('trigger').click();
    });

    expect(screen.getByText('Something failed')).toBeInTheDocument();
  });

  it('dismisses toast on X button click', () => {
    function Wrapper() {
      const toast = useToast();
      return (
        <>
          <button onClick={() => toast.info('Hello')}>trigger</button>
          <Toaster />
        </>
      );
    }

    render(<Wrapper />);
    act(() => {
      screen.getByText('trigger').click();
    });

    expect(screen.getByText('Hello')).toBeInTheDocument();

    act(() => {
      screen.getByLabelText('Dismiss notification').click();
    });

    expect(screen.queryByText('Hello')).not.toBeInTheDocument();
  });
});

describe('useToast', () => {
  it('returns success/error/warning/info methods', () => {
    const { result } = renderHook(() => useToast());
    expect(typeof result.current.success).toBe('function');
    expect(typeof result.current.error).toBe('function');
    expect(typeof result.current.warning).toBe('function');
    expect(typeof result.current.info).toBe('function');
  });
});
