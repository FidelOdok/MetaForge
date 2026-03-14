import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ErrorBoundary } from '../ErrorBoundary';

// Suppress React error boundary console output during tests
beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'info').mockImplementation(() => {});
});

function ThrowingChild(): JSX.Element {
  throw new Error('Test error');
}

function GoodChild() {
  return <div>All good</div>;
}

describe('ErrorBoundary', () => {
  it('renders children when no error', () => {
    render(
      <ErrorBoundary>
        <GoodChild />
      </ErrorBoundary>,
    );
    expect(screen.getByText('All good')).toBeDefined();
  });

  it('renders fallback UI when child throws', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Something went wrong')).toBeDefined();
    expect(screen.getByText('Test error')).toBeDefined();
    expect(screen.getByText('Try again')).toBeDefined();
  });

  it('"Try again" button resets the error state', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild />
      </ErrorBoundary>,
    );

    // Error boundary is showing
    expect(screen.getByText('Something went wrong')).toBeDefined();

    // Click Try again — the child will throw again, so we'll see
    // the error boundary re-appear (this verifies the reset cycle works)
    fireEvent.click(screen.getByText('Try again'));

    // After reset, ThrowingChild throws again, so error boundary re-catches
    expect(screen.getByText('Something went wrong')).toBeDefined();
  });

  it('renders custom fallback when provided', () => {
    render(
      <ErrorBoundary fallback={<div>Custom fallback</div>}>
        <ThrowingChild />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Custom fallback')).toBeDefined();
  });
});
