import { create } from 'zustand';
import type { CompiledSketch } from '../lib/sketch/compiler';

/**
 * The sketch compiler's working state, per project (FORGE-791).
 *
 * Kept apart from the stroke store on purpose: the strokes are the person's
 * drawing, this is a proposal made from one saved revision of it. Every edit
 * (accept, reject, a typed dimension, a correction) pushes the previous
 * state, so undo and redo walk the person's own decisions; `lastSaved` is
 * the last state the gateway stored, which *Restore* returns to (UXS-018).
 */

export interface PromotionState {
  nodeId: string;
  itemRef: string | null;
  approvalId: string;
  approved: boolean;
}

export interface CompilerDoc {
  sketch: CompiledSketch | null;
  /** The fit as first proposed, for *Compare*. */
  original: CompiledSketch | null;
  undo: CompiledSketch[];
  redo: CompiledSketch[];
  /** The state the gateway last stored, and its revision (`SKI-...@n`). */
  lastSaved: CompiledSketch | null;
  savedRef: string | null;
  /** True when the sketch changed since it was last stored. */
  dirty: boolean;
  busy: 'idle' | 'saving' | 'promoting' | 'approving';
  error: string | null;
  /** Kept across a retry so the same state is never stored twice. */
  saveId: string | null;
  /** Kept across a retry so a promotion is never made twice. */
  promoteId: string | null;
  promotion: PromotionState | null;
  compare: boolean;
  detail: boolean;
}

const UNDO_LIMIT = 100;

export function emptyCompilerDoc(): CompilerDoc {
  return {
    sketch: null,
    original: null,
    undo: [],
    redo: [],
    lastSaved: null,
    savedRef: null,
    dirty: false,
    busy: 'idle',
    error: null,
    saveId: null,
    promoteId: null,
    promotion: null,
    compare: false,
    detail: false,
  };
}

interface CompilerStore {
  docs: Record<string, CompilerDoc>;
  doc: (projectId: string) => CompilerDoc;
  /** Start from a fresh interpretation. */
  open: (projectId: string, sketch: CompiledSketch) => void;
  /** Apply an edit; the previous state goes on the undo stack. */
  apply: (projectId: string, next: CompiledSketch) => void;
  undo: (projectId: string) => void;
  redo: (projectId: string) => void;
  /** Back to what the gateway last stored. */
  restore: (projectId: string) => void;
  close: (projectId: string) => void;
  patch: (projectId: string, change: Partial<CompilerDoc>) => void;
  reset: () => void;
}

export const useSketchCompilerStore = create<CompilerStore>((set, get) => {
  const update = (projectId: string, change: (doc: CompilerDoc) => Partial<CompilerDoc>) =>
    set((s) => {
      const current = s.docs[projectId] ?? emptyCompilerDoc();
      return { docs: { ...s.docs, [projectId]: { ...current, ...change(current) } } };
    });
  return {
    docs: {},
    doc: (projectId) => get().docs[projectId] ?? emptyCompilerDoc(),
    open: (projectId, sketch) => update(projectId, () => ({ ...emptyCompilerDoc(), sketch, original: sketch, dirty: true })),
    apply: (projectId, next) =>
      update(projectId, (doc) => ({
        sketch: next,
        undo: doc.sketch ? [...doc.undo, doc.sketch].slice(-UNDO_LIMIT) : doc.undo,
        redo: [],
        dirty: true,
        saveId: null,
        promoteId: null,
        promotion: null,
        error: null,
      })),
    undo: (projectId) =>
      update(projectId, (doc) => {
        const prev = doc.undo[doc.undo.length - 1];
        if (!prev || !doc.sketch) return {};
        return { sketch: prev, undo: doc.undo.slice(0, -1), redo: [...doc.redo, doc.sketch], dirty: true, saveId: null, promoteId: null };
      }),
    redo: (projectId) =>
      update(projectId, (doc) => {
        const next = doc.redo[doc.redo.length - 1];
        if (!next || !doc.sketch) return {};
        return { sketch: next, redo: doc.redo.slice(0, -1), undo: [...doc.undo, doc.sketch], dirty: true, saveId: null, promoteId: null };
      }),
    restore: (projectId) =>
      update(projectId, (doc) => {
        if (!doc.lastSaved || !doc.sketch) return {};
        return { sketch: doc.lastSaved, undo: [...doc.undo, doc.sketch], redo: [], dirty: false };
      }),
    close: (projectId) => update(projectId, () => emptyCompilerDoc()),
    patch: (projectId, change) => update(projectId, () => change),
    reset: () => set({ docs: {} }),
  };
});
