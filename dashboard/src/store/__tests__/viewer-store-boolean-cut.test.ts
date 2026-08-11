import { describe, it, expect, beforeEach } from 'vitest';
import { useViewerStore } from '../viewer-store';
import type { ModelManifest } from '../../types/viewer';

const IDLE = {
  mode: 'idle' as const,
  targetNodeId: null,
  cutterNodeId: null,
  cutterGlbUrl: null,
  cutterManifest: null,
  cutting: false,
};

const MANIFEST: ModelManifest = {
  parts: [],
  meshToNodeMap: {},
  materials: [],
  stats: { triangleCount: 0, fileSize: 0 },
};

describe('useViewerStore booleanCut slice (MET-612)', () => {
  beforeEach(() => useViewerStore.setState({ booleanCut: IDLE }));

  it('starts idle', () => {
    expect(useViewerStore.getState().booleanCut).toEqual(IDLE);
  });

  it('openBooleanCut enters picking-cutter mode for the given target', () => {
    useViewerStore.getState().openBooleanCut('target-1');
    const { booleanCut } = useViewerStore.getState();
    expect(booleanCut.mode).toBe('picking-cutter');
    expect(booleanCut.targetNodeId).toBe('target-1');
    expect(booleanCut.cutterNodeId).toBeNull();
  });

  it('setBooleanCutCutter advances to ready with the cutter model', () => {
    useViewerStore.getState().openBooleanCut('target-1');
    useViewerStore.getState().setBooleanCutCutter('cutter-1', 'blob:cutter.glb', MANIFEST);
    const { booleanCut } = useViewerStore.getState();
    expect(booleanCut.mode).toBe('ready');
    expect(booleanCut.cutterNodeId).toBe('cutter-1');
    expect(booleanCut.cutterGlbUrl).toBe('blob:cutter.glb');
    expect(booleanCut.cutterManifest).toBe(MANIFEST);
    // Does not touch which target is being cut.
    expect(booleanCut.targetNodeId).toBe('target-1');
  });

  it('clearBooleanCutCutter drops back to picking-cutter without leaving the mode entirely', () => {
    useViewerStore.getState().openBooleanCut('target-1');
    useViewerStore.getState().setBooleanCutCutter('cutter-1', 'blob:cutter.glb', MANIFEST);
    useViewerStore.getState().clearBooleanCutCutter();
    const { booleanCut } = useViewerStore.getState();
    expect(booleanCut.mode).toBe('picking-cutter');
    expect(booleanCut.cutterNodeId).toBeNull();
    expect(booleanCut.cutterGlbUrl).toBeNull();
    expect(booleanCut.cutterManifest).toBeNull();
    expect(booleanCut.targetNodeId).toBe('target-1'); // target survives cutter reselection
  });

  it('setBooleanCutting toggles the in-flight flag without touching the rest', () => {
    useViewerStore.getState().openBooleanCut('target-1');
    useViewerStore.getState().setBooleanCutting(true);
    expect(useViewerStore.getState().booleanCut.cutting).toBe(true);
    expect(useViewerStore.getState().booleanCut.targetNodeId).toBe('target-1');
    useViewerStore.getState().setBooleanCutting(false);
    expect(useViewerStore.getState().booleanCut.cutting).toBe(false);
  });

  it('closeBooleanCut resets fully to idle', () => {
    useViewerStore.getState().openBooleanCut('target-1');
    useViewerStore.getState().setBooleanCutCutter('cutter-1', 'blob:cutter.glb', MANIFEST);
    useViewerStore.getState().closeBooleanCut();
    expect(useViewerStore.getState().booleanCut).toEqual(IDLE);
  });

  it('does not touch the primary glbUrl/manifest/selectedMeshName state', () => {
    useViewerStore.setState({ glbUrl: 'primary.glb', selectedMeshName: 'mesh_0' });
    useViewerStore.getState().openBooleanCut('target-1');
    useViewerStore.getState().setBooleanCutCutter('cutter-1', 'blob:cutter.glb', MANIFEST);
    const state = useViewerStore.getState();
    expect(state.glbUrl).toBe('primary.glb');
    expect(state.selectedMeshName).toBe('mesh_0');
  });
});
