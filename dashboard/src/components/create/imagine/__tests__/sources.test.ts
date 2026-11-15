import { describe, expect, it } from 'vitest';
import { valueSources } from '../sources';

// The night-light studio as the gateway writes it (2026-11-15), trimmed.
const studio = {
  process: { id: 'fdm', material: 'pla', machine: 'mi_cr10' },
  context: {
    components: {
      parts: [
        {
          id: 'ring-a',
          name: 'NeoPixel Ring 12',
          library: 'adafruit_1643_neopixel_ring_12',
          geometry: { envelope: [36.8, 36.8, 6.7], derived_from: 'library', source: 'library adafruit_1643_neopixel_ring_12 v2: https://www.adafruit.com/product/1643' },
          cost: { unit: 6.69, currency: 'GBP', derived_from: 'library', source: 'library adafruit_1643_neopixel_ring_12 v2: Mouser 485-1643, read 2026-11-15 via api' },
        },
        {
          id: 'touch-a',
          name: 'Touch electrode',
          geometry: { envelope: [25, 0.1, 25], size_basis: 'typical', source: 'industrial design choice' },
        },
      ],
    },
    machines: {
      machines: [
        {
          id: 'mi_cr10',
          name: 'My CR-10 V3 (home)',
          derived_from: 'library',
          source: 'library mi_cr10 v1 (in_house)',
          fromProcess: ['featureMin', 'tolerance'],
          defaulted: ['materials'],
        },
        { id: 'mi_other', name: 'Other printer', derived_from: 'library', source: 'library mi_other v1 (in_house)', fromProcess: [], defaulted: [] },
      ],
      materials: [
        { id: 'pla', name: 'PLA', derived_from: 'library', source: 'library pla v1: https://example.com/tds.pdf', fromLibrary: ['density', 'E', 'str'], defaulted: ['costKg', 'k', 'ul94'] },
      ],
    },
  },
};

describe('valueSources', () => {
  it('says which part values came from the library and which were typed', () => {
    const rows = valueSources(studio);
    const ring = rows.find((r) => r.subject === 'NeoPixel Ring 12' && r.what === 'Size');
    expect(ring).toMatchObject({ origin: 'library', value: '36.8 × 36.8 × 6.7 mm' });
    expect(ring?.source).toContain('adafruit_1643_neopixel_ring_12 v2');
    const price = rows.find((r) => r.subject === 'NeoPixel Ring 12' && r.what === 'Price');
    expect(price).toMatchObject({ origin: 'library', value: '6.69 GBP' });
    const tape = rows.find((r) => r.subject === 'Touch electrode' && r.what === 'Size');
    expect(tape).toMatchObject({ origin: 'estimate' });
  });

  it('names the chosen machine and what came from the process or a default', () => {
    const rows = valueSources(studio);
    const machine = rows.find((r) => r.what === 'Machine');
    expect(machine).toMatchObject({ subject: 'My CR-10 V3 (home)', origin: 'library' });
    expect(machine?.detail).toContain('from the process guide: featureMin, tolerance');
    expect(machine?.detail).toContain('defaults: materials');
    // only the chosen machine, not every in-house machine offered beside it
    expect(rows.filter((r) => r.what === 'Machine')).toHaveLength(1);
  });

  it('names the material figures from the library and those still defaults', () => {
    const mat = valueSources(studio).find((r) => r.what === 'Material');
    expect(mat).toMatchObject({ subject: 'PLA', origin: 'library' });
    expect(mat?.detail).toContain('density, E, str');
    expect(mat?.detail).toContain('costKg, k, ul94');
  });

  it('returns nothing for a studio with no parts, machine or material', () => {
    // negative control: no invented rows
    expect(valueSources({ process: { id: 'fdm' } })).toEqual([]);
    expect(valueSources(null)).toEqual([]);
  });
});
