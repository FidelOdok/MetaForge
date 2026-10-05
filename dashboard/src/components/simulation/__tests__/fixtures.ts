import type { SimFieldPayload } from '../../../types/simulationField';

/** A unit square in z=0 split into two triangles, stress rising with x. */
export function squarePayload(overrides: Partial<SimFieldPayload> = {}): SimFieldPayload {
  return {
    format: 'metaforge.sim_field',
    version: 1,
    analysis_type: 'static_stress',
    units: { length: 'mm', stress: 'MPa' },
    vertex_count: 4,
    triangle_count: 2,
    positions: [0, 0, 0, 10, 0, 0, 10, 10, 0, 0, 10, 0],
    indices: [0, 1, 2, 0, 2, 3],
    displacement: [0, 0, 0, 0, 0, -0.1, 0, 0, -0.1, 0, 0, 0],
    fields: {
      von_mises: {
        label: 'Von Mises stress',
        unit: 'MPa',
        values: [0, 100, 100, 0],
        min: 0,
        max: 120,
        peak: { position: [10, 0, 0], value: 120 },
      },
      displacement_magnitude: {
        label: 'Displacement magnitude',
        unit: 'mm',
        values: [0, 0.1, 0.1, 0],
        min: 0,
        max: 0.1,
        peak: { position: [10, 0, 0], value: 0.1 },
      },
    },
    markers: [
      { kind: 'fixture', label: 'Surface1', position: [0, 5, 0], bbox: { min: [0, 0, 0], max: [0, 10, 0] } },
      { kind: 'load', label: 'Surface2', position: [10, 5, 0], vector: [0, 0, -100], unit: 'N' },
    ],
    bbox: { min: [0, 0, 0], max: [10, 10, 0] },
    decimation: { applied: false, source_triangle_count: 2, cell_size_mm: null },
    ...overrides,
  };
}
