import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { SCENES, sceneFor } from '../scenes/registry';
import { sceneDomainOf, sceneStateOf, type SceneState } from '../scenes/types';
import type { OperationDomain, OperationState } from '../../../types/operations';

const STATES: SceneState[] = ['queued', 'running', 'waiting', 'completed', 'failed', 'stale'];
const DOMAINS: OperationDomain[] = [
  'concept',
  'sketch',
  'mechanical',
  'electronics',
  'firmware',
  'simulation',
  'manufacturing',
  'cross_domain',
  'general',
];

describe('activity scenes', () => {
  for (const [name, Scene] of Object.entries(SCENES)) {
    for (const state of STATES) {
      it(`${name} renders ${state} without throwing`, () => {
        const { container } = render(<Scene state={state} stageIndex={2} reducedMotion={false} paused={false} staleRevision="8" />);
        const svg = container.querySelector('svg');
        expect(svg).not.toBeNull();
        expect(svg?.getAttribute('data-state')).toBe(state);
        expect(svg?.getAttribute('data-animated')).toBe(state === 'running' ? 'true' : 'false');
        // Safe by construction: no scripts, handlers or filters (ASVG-013/014).
        expect(container.querySelector('script, filter, foreignObject')).toBeNull();
        for (const el of Array.from(container.querySelectorAll('*'))) {
          for (const attr of Array.from(el.attributes)) expect(attr.name.startsWith('on')).toBe(false);
        }
      });
    }

    it(`${name} is static when paused or under reduced motion`, () => {
      const paused = render(<Scene state="running" stageIndex={0} reducedMotion={false} paused />);
      expect(paused.container.querySelector('svg')?.getAttribute('data-animated')).toBe('false');
      const reduced = render(<Scene state="running" stageIndex={0} reducedMotion paused={false} />);
      expect(reduced.container.querySelector('svg')?.getAttribute('data-animated')).toBe('false');
    });
  }

  it('marks failure and labels a stale scene with its revision', () => {
    const Scene = SCENES.cad;
    const failed = render(<Scene state="failed" stageIndex={1} reducedMotion={false} paused={false} />);
    expect(failed.container.querySelector('.act-scene-fail')).not.toBeNull();
    const stale = render(<Scene state="stale" stageIndex={1} reducedMotion={false} paused={false} staleRevision="8" />);
    expect(stale.container.textContent).toContain('Revision 8');
  });

  it('has a scene for every domain', () => {
    for (const domain of DOMAINS) {
      const Scene = sceneFor(domain);
      expect(Scene).toBe(SCENES[sceneDomainOf(domain)]);
    }
  });

  it('maps every operation state to a scene state', () => {
    const states: OperationState[] = [
      'queued',
      'running',
      'waiting_for_user',
      'blocked',
      'completed',
      'failed',
      'cancelled',
      'superseded',
      'stale',
    ];
    expect(states.map(sceneStateOf)).toEqual([
      'queued',
      'running',
      'waiting',
      'waiting',
      'completed',
      'failed',
      'failed',
      'stale',
      'stale',
    ]);
  });
});
