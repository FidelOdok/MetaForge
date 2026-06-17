export interface PartInfo {
  meshName: string;
  name: string;
  nodeId?: string;
  boundingBox?: { min: [number, number, number]; max: [number, number, number] };
}

export interface PartTreeNode {
  name: string;
  meshName: string;
  children: PartTreeNode[];
  boundingBox?: { min: [number, number, number]; max: [number, number, number] };
}

export interface ModelManifest {
  parts: PartTreeNode[];
  meshToNodeMap: Record<string, string>;
  materials: { name: string; color?: string }[];
  stats: { triangleCount: number; fileSize: number };
  /** Rigid groups for interactive manipulation (MET-519). Optional — when
   * absent the viewer treats each top-level part as its own single-part group. */
  rigidGroups?: RigidGroup[];
}

/**
 * A rigidly-constrained cluster of parts that move together (MET-519).
 * Wire shape in the GLB manifest; `partIndices` index into
 * {@link ModelManifest.parts}. `transform` is an optional 16-float
 * column-major base matrix (defaults to identity).
 */
export interface RigidGroup {
  name: string;
  partIndices: number[];
  transform?: number[];
}

/** A rigid group resolved to scene-graph mesh names, ready for the gizmo. */
export interface ResolvedRigidGroup {
  name: string;
  meshNames: string[];
}

/** Explode direction modes for the 3D assembly viewer. */
export type ExplodeDirection = 'radial' | 'axial';

export interface ViewerAdapter {
  loadModel(glbUrl: string, manifest: ModelManifest): Promise<void>;
  selectPart(meshName: string): void;
  highlightParts(meshNames: string[]): void;
  setVisibility(meshName: string, visible: boolean): void;
  setExplodedView(factor: number): void;
  setExplodeDirection(direction: ExplodeDirection): void;
  getSelectedPart(): PartInfo | null;
  onPartClick(callback: (part: PartInfo) => void): void;
  dispose(): void;
}
