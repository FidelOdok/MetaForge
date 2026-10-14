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

/**
 * Which GPU this canvas asks for (FORGE-625).
 *
 * A WebGL context that says nothing gets `powerPreference: 'default'`, and on
 * a machine with two GPUs that is exactly when a browser picks the integrated
 * one to save power. Measured on two of this project's machines: both report
 * AMD integrated graphics over D3D11, including the one with an RTX in it.
 * Windows' own per-application graphics preference cannot rescue that --
 * it can only honour a preference the application expressed.
 *
 * Applied to every 3D surface rather than only the main viewer, so a preview
 * and the viewer never end up on different adapters at the same time, which
 * costs a copy between them.
 *
 * The trade is real: on a laptop this can wake the discrete GPU and draw more
 * power. For a CAD viewer whose whole job is showing geometry, that is the
 * right side of the trade.
 */
export const VIEWER_GPU_PREFERENCE = {
  powerPreference: 'high-performance',
} as const;
