import * as THREE from 'three';

/**
 * Renderer colour management for the twin viewer (FORGE-517).
 *
 * Chain: STEP colour (sRGB) -> GLB baseColorFactor (linear, set by the
 * converter) -> renderer output (sRGB). `outputColorSpace` must stay sRGB so
 * the linear factor is encoded back to the STEP value.
 *
 * Tone mapping must not repaint flat part colours. ACES Filmic (the previous
 * setting, exposure 1.1) desaturates and darkens mid-tones, so a light birch
 * tan rendered as a muddy brown. Neutral (Khronos PBR Neutral) is designed for
 * product colour fidelity: it leaves colours below ~0.8 untouched and only
 * compresses highlights. No colour is invented; this only avoids altering the
 * model's own.
 */
export const VIEWER_COLOR_MANAGEMENT = {
  outputColorSpace: THREE.SRGBColorSpace,
  toneMapping: THREE.NeutralToneMapping,
  toneMappingExposure: 1,
} as const;
