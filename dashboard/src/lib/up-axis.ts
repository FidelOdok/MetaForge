/**
 * Which way is up in a loaded 3D asset, so the viewer turns it only when it
 * has to.
 *
 * The dashboard's 3D world is Y-up (three.js and OrbitControls). Two kinds
 * of asset reach it:
 *
 * - **CAD-derived** meshes (the STEP to GLB converter, the OCCT
 *   tessellation of an exact model, STL and 3MF files) are authored Z-up,
 *   so they are turned -90 degrees about X;
 * - **glTF-native** assets (an AI 3D concept, L0; a structured mesh, L1;
 *   any other glTF file) are Y-up, as the glTF specification says, and are
 *   shown as they are.
 *
 * The decision reads, in order: an explicit up axis written in the file
 * (`asset.extras.up_axis`, or `up_axis` in a root node's or the scene's
 * extras), then the file format, then the twin's representation descriptor
 * (`level`, then `generated_by`), then the glTF generator string, and last
 * where the file came from. Pure, so every case is tested without WebGL.
 */

export type UpAxis = 'y' | 'z';

export interface UpAxisDecision {
  axis: UpAxis;
  /** Why, in a few words (for logs and tests). */
  reason: string;
}

/** The parts of a glTF `asset` block this reads. */
export interface GltfAssetInfo {
  generator?: string;
  extras?: Record<string, unknown> | null;
}

/** A loaded scene node, as far as this needs: its name, extras and children. */
export interface SceneNodeInfo {
  name?: string;
  userData?: Record<string, unknown> | null;
  children?: SceneNodeInfo[];
}

/** What the twin says about the representation (`TwinNode.representation`). */
export interface RepresentationHint {
  level?: string | null;
  generated_by?: { tool?: string | null } | null;
}

export interface UpAxisInput {
  asset?: GltfAssetInfo | null;
  scene?: SceneNodeInfo | null;
  /** `glb`, `gltf`, `stl` or `3mf`. */
  format?: string | null;
  representation?: RepresentationHint | null;
  /**
   * Where the file came from: `converter` (the STEP to GLB route, always
   * CAD) or `file` (the node's own file, as stored).
   */
  source?: 'converter' | 'file';
}

/** `y`, `z`, `Y_UP`, `+Z` and the like; anything else is not an answer. */
export function parseUpAxis(raw: unknown): UpAxis | null {
  if (typeof raw !== 'string') return null;
  const t = raw.trim().toLowerCase().replace(/^[+]/, '').replace(/[-_ ]?up$/, '');
  if (t === 'y') return 'y';
  if (t === 'z') return 'z';
  return null;
}

function extrasAxis(extras: Record<string, unknown> | null | undefined): UpAxis | null {
  if (!extras) return null;
  return parseUpAxis(extras.up_axis ?? extras.upAxis ?? extras.up);
}

/** An up axis written in the scene: on the scene itself, or on a root node. */
function sceneAxis(scene: SceneNodeInfo | null | undefined): UpAxis | null {
  if (!scene) return null;
  const own = extrasAxis(scene.userData);
  if (own) return own;
  // drei/three put a root node's glTF extras in its userData; the scene's
  // direct children are the glTF scene's root nodes.
  for (const child of scene.children ?? []) {
    const axis = extrasAxis(child.userData);
    if (axis) return axis;
  }
  return null;
}

const Y_UP_LEVELS = new Set(['L0', 'L1']);
const Z_UP_LEVELS = new Set(['L2', 'L3']);
const Y_UP_TOOLS = /tripo|concept_mesh|structured_mesh|meshy|rodin|hunyuan|trellis/i;
const Z_UP_TOOLS = /occt|open ?cascade|cadquery|freecad|step|exact_cad|build_exact/i;
const Z_UP_FORMATS = new Set(['stl', '3mf']);

export function decideUpAxis(input: UpAxisInput): UpAxisDecision {
  const explicit = extrasAxis(input.asset?.extras) ?? sceneAxis(input.scene);
  if (explicit) return { axis: explicit, reason: 'written in the file' };

  const format = (input.format ?? '').toLowerCase();
  if (Z_UP_FORMATS.has(format)) return { axis: 'z', reason: `${format} is Z-up` };
  if (input.source === 'converter') return { axis: 'z', reason: 'CAD through the STEP converter' };

  const level = input.representation?.level ?? '';
  if (Y_UP_LEVELS.has(level)) return { axis: 'y', reason: `${level} is glTF-native` };
  if (Z_UP_LEVELS.has(level)) return { axis: 'z', reason: `${level} is CAD-derived` };

  const tool = input.representation?.generated_by?.tool ?? '';
  if (tool && Y_UP_TOOLS.test(tool)) return { axis: 'y', reason: `made by ${tool}` };
  if (tool && Z_UP_TOOLS.test(tool)) return { axis: 'z', reason: `made by ${tool}` };

  const generator = input.asset?.generator ?? '';
  if (generator && Z_UP_TOOLS.test(generator)) return { axis: 'z', reason: `written by ${generator}` };
  if (generator && Y_UP_TOOLS.test(generator)) return { axis: 'y', reason: `written by ${generator}` };

  if (format === 'glb' || format === 'gltf') return { axis: 'y', reason: 'glTF is Y-up' };
  return { axis: 'z', reason: 'CAD by default' };
}

/** The X rotation that stands an asset up in the Y-up viewer. */
export function uprightRotationX(axis: UpAxis): number {
  return axis === 'z' ? -Math.PI / 2 : 0;
}
