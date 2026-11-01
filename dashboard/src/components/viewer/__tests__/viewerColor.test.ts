import { describe, it, expect } from 'vitest';
import * as THREE from 'three';
import { VIEWER_COLOR_MANAGEMENT } from '../viewerColor';

describe('viewer colour management (FORGE-517)', () => {
  it('outputs sRGB so the GLB linear baseColorFactor is encoded back', () => {
    expect(VIEWER_COLOR_MANAGEMENT.outputColorSpace).toBe(THREE.SRGBColorSpace);
  });

  it('does not use ACES, which darkens and desaturates flat part colours', () => {
    expect(VIEWER_COLOR_MANAGEMENT.toneMapping).not.toBe(THREE.ACESFilmicToneMapping);
    expect(VIEWER_COLOR_MANAGEMENT.toneMapping).toBe(THREE.NeutralToneMapping);
    expect(VIEWER_COLOR_MANAGEMENT.toneMappingExposure).toBe(1);
  });

  it('round-trips the birch table colour: linear GLB factor displays as ~0.87', () => {
    // converter writes the linear value OCCT returns for STEP sRGB 0.87
    const linear = new THREE.Color().setRGB(0.87, 0.74, 0.54, THREE.SRGBColorSpace);
    expect(linear.r).toBeLessThan(0.75);
    const shown = linear.getRGB(new THREE.Color(), THREE.SRGBColorSpace);
    expect(shown.r).toBeCloseTo(0.87, 2);
    expect(shown.g).toBeCloseTo(0.74, 2);
    expect(shown.b).toBeCloseTo(0.54, 2);
  });
});
