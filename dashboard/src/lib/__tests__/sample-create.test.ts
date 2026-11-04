import { describe, it, expect, beforeEach } from 'vitest';
import type { CreationView } from '../../api/endpoints/create';
import { resetSampleCreation, routeSampleCreate } from '../sample-create';

const P = 'sample-drone-fc';
const call = (method: string, path: string, body: Record<string, unknown> = {}) =>
  routeSampleCreate(method, path, body, P, 'sample-pcb') as CreationView;

describe('sample creation', () => {
  beforeEach(resetSampleCreation);

  it('opens on a finished build waiting for its last required check', () => {
    const view = call('get', `/create/${P}`);
    expect(view.readiness.stage).toBe('build_completed');
    expect(view.readiness.blockers.map((b) => b.id)).toEqual(['check:boot']);
  });

  it('walks to an accepted product, keeping the unreviewed optional check as a limitation', () => {
    const build = call('get', `/create/${P}`).creation.builds[0]!;
    expect(call('post', `/create/${P}/builds/${build.id}/accept`, { decision: 'accept' })).toBeUndefined();
    call('put', `/create/${P}/builds/${build.id}/results/boot`, { status: 'passed' });
    const accepted = call('post', `/create/${P}/builds/${build.id}/accept`, { decision: 'accept' });
    expect(accepted.readiness.stage).toBe('accepted_product');
    expect(accepted.creation.builds[0]!.acceptance?.limitations).toEqual(['Silkscreen legible: awaiting review']);
  });

  it('leaves other projects and routes to the rest of the sample workspace', () => {
    expect(call('get', '/create/another')).toBeUndefined();
    expect(call('get', '/projects')).toBeUndefined();
    expect(routeSampleCreate('get', '/library/machines', {}, P, '')).toEqual({ machines: [] });
    expect(routeSampleCreate('get', '/approvals', {}, P, '')).toEqual({ items: [], unscoped_count: 0 });
  });
});
