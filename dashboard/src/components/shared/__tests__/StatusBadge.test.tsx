import { describe, it, expect } from 'vitest';
import { render, screen } from '../../../test/test-utils';
import { StatusBadge } from '../StatusBadge';

describe('StatusBadge', () => {
  it('renders known status label', () => {
    render(<StatusBadge status="completed" />);
    expect(screen.getByText('Completed')).toBeInTheDocument();
  });

  it('renders unknown status as-is', () => {
    render(<StatusBadge status="custom_status" />);
    expect(screen.getByText('custom_status')).toBeInTheDocument();
  });

  it.each([
    ['available', 'Available'],
    ['low_stock', 'Low Stock'],
    ['out_of_stock', 'Out of Stock'],
    ['alternate_needed', 'Alternate Needed'],
  ])('renders BOM status %s as %s', (status, label) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it('applies success variant for completed', () => {
    render(<StatusBadge status="completed" />);
    const el = screen.getByText('Completed');
    expect(el.className).toContain('bg-green-100');
  });
});
