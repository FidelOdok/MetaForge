import { describe, it, expect, vi, afterEach } from 'vitest';
import { generateId } from '../id';

afterEach(() => vi.unstubAllGlobals());

describe('generateId', () => {
  it('uses crypto.randomUUID when available', () => {
    vi.stubGlobal('crypto', { randomUUID: () => 'native-uuid' });
    expect(generateId()).toBe('native-uuid');
  });
  it('falls back to a v4-shaped id in non-secure contexts', () => {
    vi.stubGlobal('crypto', {}); // no randomUUID
    const id = generateId();
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });
});
