import { describe, expect, it } from 'vitest';
import { decideUpAxis, parseUpAxis, uprightRotationX } from '../up-axis';

describe('decideUpAxis', () => {
  it('leaves an AI 3D concept (L0, Tripo) Y-up', () => {
    const d = decideUpAxis({
      asset: { generator: 'tripo3d' },
      format: 'glb',
      source: 'file',
      representation: { level: 'L0', generated_by: { tool: 'twin.generate_concept_mesh' } },
    });
    expect(d.axis).toBe('y');
    expect(uprightRotationX(d.axis)).toBe(0);
  });

  it('leaves an L0 mesh Y-up from its tool alone when no level is recorded', () => {
    expect(decideUpAxis({ format: 'glb', source: 'file', representation: { generated_by: { tool: 'tripo.image_to_model' } } }).axis).toBe('y');
  });

  it('reads a structured mesh (L1) from the up axis written in the file', () => {
    const scene = { children: [{ name: 'metaforge_structured_mesh', userData: { up_axis: 'y', level: 'L1' } }] };
    expect(decideUpAxis({ scene, format: 'glb', source: 'file' })).toEqual({ axis: 'y', reason: 'written in the file' });
    expect(decideUpAxis({ asset: { extras: { up_axis: 'y' } }, source: 'file' }).axis).toBe('y');
  });

  it('turns the OCCT tessellation of an exact model (L2/L3) Z-up to Y-up', () => {
    const l3 = decideUpAxis({ format: 'glb', source: 'converter', representation: { level: 'L3' } });
    expect(l3.axis).toBe('z');
    expect(uprightRotationX(l3.axis)).toBeCloseTo(-Math.PI / 2);
    expect(decideUpAxis({ format: 'glb', source: 'file', representation: { level: 'L2' } }).axis).toBe('z');
  });

  it('treats a converter GLB with no descriptor as CAD', () => {
    expect(decideUpAxis({ format: 'glb', source: 'converter' }).axis).toBe('z');
    expect(decideUpAxis({ asset: { generator: 'Open CASCADE Technology 7.8' }, format: 'glb', source: 'file' }).axis).toBe('z');
  });

  it('turns STL and 3MF files', () => {
    expect(decideUpAxis({ format: 'stl', source: 'file' }).axis).toBe('z');
    expect(decideUpAxis({ format: '3mf' }).axis).toBe('z');
  });

  it('takes an up axis written in the file over everything else', () => {
    expect(
      decideUpAxis({ asset: { extras: { up_axis: 'Z' } }, format: 'glb', source: 'file', representation: { level: 'L0' } }).axis,
    ).toBe('z');
    expect(decideUpAxis({ scene: { userData: { up_axis: 'Y_UP' } }, source: 'converter' }).axis).toBe('y');
  });

  it('defaults a plain glTF file to Y-up, as the glTF specification says', () => {
    expect(decideUpAxis({ format: 'glb', source: 'file' }).axis).toBe('y');
  });
});

describe('parseUpAxis', () => {
  it('reads the usual spellings and refuses anything else', () => {
    expect(parseUpAxis('y')).toBe('y');
    expect(parseUpAxis('+Z')).toBe('z');
    expect(parseUpAxis('z-up')).toBe('z');
    expect(parseUpAxis('x')).toBeNull();
    expect(parseUpAxis(3)).toBeNull();
  });
});
