import { describe, it, expect } from 'vitest';
import { render, screen } from '../../../test/test-utils';
import { Card } from '../Card';

describe('Card', () => {
  it('renders children', () => {
    render(<Card>Card content</Card>);
    expect(screen.getByText('Card content')).toBeInTheDocument();
  });

  it('applies base classes', () => {
    render(<Card>Test</Card>);
    const el = screen.getByText('Test');
    expect(el.className).toContain('rounded-lg');
    // border is now an inline style (not a Tailwind class)
  });

  it('merges custom className', () => {
    render(<Card className="my-class">Test</Card>);
    expect(screen.getByText('Test').className).toContain('my-class');
  });
});
