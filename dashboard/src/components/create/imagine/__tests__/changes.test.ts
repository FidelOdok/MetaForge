import { describe, expect, it } from 'vitest';
import { describeChanges } from '../changes';

const part = (id: string, name: string, extra: Record<string, unknown> = {}) => ({ id, name, kind: 'generated', code: 'return [];', ...extra });
const cfg = (form: Record<string, unknown>, rest: Record<string, unknown> = {}) => ({ engine: 'formgraph@2', process: { id: 'fdm' }, ...rest, form: { template: 'authored', ...form } });

describe('describeChanges', () => {
  it('says what changed in the form, in plain words', () => {
    const before = cfg({
      params: { case_d: 40, glass_t: 1.5 },
      authored: {
        parts: [part('case', 'Watch case', { make: { process: 'fdm', material: 'pla', color: 'graphite', source: 'proposed' } }), part('bar', 'Strap bar')],
      },
    });
    const after = cfg({
      params: { case_d: 48, glass_t: 1.5, strap_w: 20 },
      authored: {
        parts: [
          part('case', 'Watch case', { code: 'return [g.box(1,1,1)];', make: { process: 'cnc', material: 'al6061', color: 'graphite', source: 'person' } }),
          part('gnss', 'GNSS module'),
        ],
        behaviours: [{ id: 'light', name: 'Backlight', kind: 'glow', parts: ['case'], color: '#FFFFFF' }],
      },
    });
    const lines = describeChanges(before, after);
    expect(lines).toEqual([
      'Case d: 40 → 48',
      'New size Strap w: 20',
      'Added GNSS module',
      'Removed Strap bar',
      'Reshaped Watch case',
      'Watch case: fdm · pla · graphite → cnc · al6061 · graphite',
      'Added behaviour Backlight',
    ]);
  });

  it('names the other sections that changed, and nothing when nothing did', () => {
    const a = cfg({ params: {} }, { process: { id: 'fdm' }, context: { components: { parts: [] } } });
    const b = cfg({ params: {} }, { process: { id: 'cnc' }, context: { components: { parts: [{ id: 'x' }] } } });
    expect(describeChanges(a, b)).toEqual(['Changed: components and context', 'Changed: manufacturing process']);
    expect(describeChanges(a, a)).toEqual([]);
  });

  it('keeps a long list short', () => {
    const many = (n: number) => Object.fromEntries(Array.from({ length: n }, (_, i) => [`p${i}`, i]));
    const lines = describeChanges(cfg({ params: many(12) }), cfg({ params: Object.fromEntries(Object.entries(many(12)).map(([k, v]) => [k, v + 1])) }));
    expect(lines).toHaveLength(9);
    expect(lines[8]).toBe('and 4 more changes');
  });
});
