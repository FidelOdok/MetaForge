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
}

export interface ViewerAdapter {
  loadModel(glbUrl: string, manifest: ModelManifest): Promise<void>;
  selectPart(meshName: string): void;
  highlightParts(meshNames: string[]): void;
  setVisibility(meshName: string, visible: boolean): void;
  setExplodedView(factor: number): void;
  getSelectedPart(): PartInfo | null;
  onPartClick(callback: (part: PartInfo) => void): void;
  dispose(): void;
}
