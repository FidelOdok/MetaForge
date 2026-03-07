import { create } from 'zustand';
import type { ModelManifest } from '../types/viewer';

interface ViewerState {
  glbUrl: string | null;
  manifest: ModelManifest | null;
  selectedMeshName: string | null;
  hiddenMeshes: Set<string>;
  explodeFactor: number;
  viewMode: '3d' | 'graph';

  loadModel: (glbUrl: string, manifest: ModelManifest) => void;
  selectPart: (meshName: string | null) => void;
  toggleVisibility: (meshName: string) => void;
  setExplodeFactor: (factor: number) => void;
  setViewMode: (mode: '3d' | 'graph') => void;
  reset: () => void;
}

export const useViewerStore = create<ViewerState>((set, get) => ({
  glbUrl: null,
  manifest: null,
  selectedMeshName: null,
  hiddenMeshes: new Set<string>(),
  explodeFactor: 0,
  viewMode: 'graph',

  loadModel: (glbUrl, manifest) =>
    set({ glbUrl, manifest, selectedMeshName: null, hiddenMeshes: new Set(), explodeFactor: 0, viewMode: '3d' }),

  selectPart: (meshName) => set({ selectedMeshName: meshName }),

  toggleVisibility: (meshName) => {
    const { hiddenMeshes } = get();
    const next = new Set(hiddenMeshes);
    if (next.has(meshName)) {
      next.delete(meshName);
    } else {
      next.add(meshName);
    }
    set({ hiddenMeshes: next });
  },

  setExplodeFactor: (factor) => set({ explodeFactor: Math.max(0, Math.min(1, factor)) }),

  setViewMode: (mode) => set({ viewMode: mode }),

  reset: () =>
    set({
      glbUrl: null,
      manifest: null,
      selectedMeshName: null,
      hiddenMeshes: new Set(),
      explodeFactor: 0,
      viewMode: 'graph',
    }),
}));
