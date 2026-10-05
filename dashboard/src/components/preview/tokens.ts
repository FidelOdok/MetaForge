// Kinetic Console tokens shared by the preview engines (mirrors the local KC
// objects in TwinViewerPage and FullScreenPreviewModal).
export const PC = {
  surface: 'var(--mf-c-111319)',
  surfaceLow: 'var(--mf-c-191b22)',
  surfaceHigh: 'var(--mf-c-282a30)',
  canvas: 'var(--mf-c-0d0e13)',
  border: 'var(--mf-r-65-72-90-0p2)',
  borderMid: 'var(--mf-r-65-72-90-0p35)',
  onSurface: 'var(--mf-c-e2e2eb)',
  onSurfaceVariant: 'var(--mf-c-9a9aaa)',
  orange: '#ff5a0a',
  orangeFaint: 'rgba(255, 90, 10,0.15)',
  teal: 'var(--mf-c-86cfff)',
  green: 'var(--mf-c-3dd68c)',
  greenFaint: 'rgba(61,214,140,0.14)',
  amber: 'var(--mf-c-f5b04d)',
  amberFaint: 'rgba(245,176,77,0.14)',
  red: 'var(--mf-c-ffb4ab)',
  redFaint: 'rgba(255,180,171,0.14)',
} as const;

/** Compact = a project-row preview; panel = the twin inspector; modal = full screen. */
export type PreviewMode = 'compact' | 'panel' | 'modal';
