import { describe, expect, it } from 'vitest';
import { STALE_SEND_MS, persistQueue, restoreQueue, syncQueue, type Instruction } from '../create-store';

/*
 * One send, many tabs (MetaForge, 2026-11-11). A second tab opened while the first was sending
 * showed the message as failed ("The page closed while this was being sent"), and a Retry there
 * would have sent it twice. A send now records which tab is sending it.
 */

const NOW = 1_800_000_000_000;
const ins = (over: Partial<Instruction>): Instruction =>
  ({ id: 'i1', text: 'hello', intent: 'design', references: [], state: 'queued', createdAt: '2026-11-11T10:00:00Z', ...over }) as Instruction;

describe('a send across tabs', () => {
  it('is saved with the tab that is sending it', () => {
    const [saved] = persistQueue([ins({ state: 'sending', sendingTab: 'tab-a', sendingSince: NOW })]);
    expect(saved).toMatchObject({ state: 'sending', sendingTab: 'tab-a', sendingSince: NOW });
    const [unsent] = persistQueue([ins({ state: 'queued' })]);
    expect(unsent).toMatchObject({ state: 'failed', error: 'Not sent before the page closed' });
  });

  it('reads as sending in another tab when another tab is sending it', () => {
    const [item] = restoreQueue([ins({ state: 'sending', sendingTab: 'tab-a', sendingSince: NOW })], 'tab-b', NOW + 5_000);
    expect(item).toMatchObject({ state: 'sending', elsewhere: true });
  });

  it('reads as failed after this same tab reloaded, or when the send is stale', () => {
    const [own] = restoreQueue([ins({ state: 'sending', sendingTab: 'tab-a', sendingSince: NOW })], 'tab-a', NOW + 5_000);
    expect(own?.state).toBe('failed');
    expect(own?.error).toMatch(/page closed/);
    const [stale] = restoreQueue([ins({ state: 'sending', sendingTab: 'tab-a', sendingSince: NOW })], 'tab-b', NOW + STALE_SEND_MS + 1);
    expect(stale?.state).toBe('failed');
  });

  it('follows the other tab: gone when it was sent, failed when that tab says so', () => {
    const mine = [ins({ id: 'a', state: 'sending', elsewhere: true }), ins({ id: 'b', state: 'sending', elsewhere: true }), ins({ id: 'c', state: 'queued' })];
    const stored = [ins({ id: 'b', state: 'failed', error: 'The page closed while this was being sent.' })];
    const next = syncQueue(mine, stored);
    expect(next.map((i) => i.id)).toEqual(['b', 'c']);
    expect(next[0]).toMatchObject({ state: 'failed', elsewhere: false });
  });
});
