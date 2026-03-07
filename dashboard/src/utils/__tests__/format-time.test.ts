import { describe, it, expect } from 'vitest';
import { formatRelativeTime } from '../format-time';

describe('formatRelativeTime', () => {
  it('returns relative time for valid ISO string', () => {
    const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    const result = formatRelativeTime(oneHourAgo);
    expect(result).toContain('ago');
  });

  it('returns the original string for invalid dates', () => {
    expect(formatRelativeTime('not-a-date')).toBe('not-a-date');
  });

  it('handles recent timestamps', () => {
    const fiveSecsAgo = new Date(Date.now() - 5000).toISOString();
    const result = formatRelativeTime(fiveSecsAgo);
    expect(result).toContain('ago');
  });
});
