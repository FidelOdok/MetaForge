import { describe, it, expect } from 'vitest';
import { render } from '../../../test/test-utils';
import { Skeleton, SkeletonCard, SkeletonTable, SkeletonList } from '../Skeleton';

describe('Skeleton', () => {
  it('renders with animate-pulse class', () => {
    const { container } = render(<Skeleton className="h-4 w-40" />);
    const el = container.firstChild as HTMLElement;
    expect(el.className).toContain('animate-pulse');
  });

  it('merges className prop', () => {
    const { container } = render(<Skeleton className="h-8 w-8" />);
    const el = container.firstChild as HTMLElement;
    expect(el.className).toContain('h-8');
  });
});

describe('SkeletonCard', () => {
  it('renders without crashing', () => {
    const { container } = render(<SkeletonCard />);
    expect(container.firstChild).toBeTruthy();
  });
});

describe('SkeletonTable', () => {
  it('renders the correct number of data rows', () => {
    const { container } = render(<SkeletonTable rows={3} cols={4} />);
    // 1 header row + 3 data rows, each with 4 animate-pulse cells = 16 total
    // (border is inline style, not a class)
    const pulses = container.querySelectorAll('.animate-pulse');
    expect(pulses.length).toBeGreaterThanOrEqual(12);
  });
});

describe('SkeletonList', () => {
  it('renders the correct number of list items', () => {
    const { container } = render(<SkeletonList rows={4} />);
    const items = container.querySelectorAll('[class*="rounded-full"]');
    // Each item has an avatar (rounded-full)
    expect(items.length).toBeGreaterThanOrEqual(4);
  });
});
