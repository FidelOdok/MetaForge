import { describe, it, expect } from 'vitest';
import { render, screen } from '../../../test/test-utils';
import { Badge } from '../Badge';

describe('Badge', () => {
  it('renders children', () => {
    render(<Badge>Hello</Badge>);
    expect(screen.getByText('Hello')).toBeInTheDocument();
  });

  it('applies default variant classes', () => {
    render(<Badge>Default</Badge>);
    const el = screen.getByText('Default');
    // KC uses inline styles; className has text color class
    expect(el.className).toContain('text-on-surface-variant');
  });

  it('applies success variant', () => {
    render(<Badge variant="success">OK</Badge>);
    const el = screen.getByText('OK');
    expect(el.className).toContain('text-success');
  });

  it('applies error variant', () => {
    render(<Badge variant="error">Fail</Badge>);
    const el = screen.getByText('Fail');
    expect(el.className).toContain('text-error');
  });
});
