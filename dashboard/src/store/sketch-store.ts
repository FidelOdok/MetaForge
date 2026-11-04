import { create } from 'zustand';
import { generateId } from '../utils/id';
import type { Point } from '../components/sketch/geometry';

/**
 * The Create stage's sketch, per project (FORGE-789).
 *
 * Strokes are drawn and rendered locally first: nothing on the input path
 * waits for the network, and this store never goes through the chat send
 * queue. Saving to the twin is a separate, later step (`markSaving` and
 * `markSaved`), keyed by a client save id so a retry is idempotent.
 *
 * Points are in sketch units (see `components/sketch/geometry.ts`), so the
 * drawing does not depend on the viewport. A human sketch has no known scale
 * and no plane: `scale` is always `unknown` and `plane.state` is
 * `UNASSIGNED` until a later step proposes and confirms one.
 *
 * The strokes, the item they save to and any unsaved change are kept as a
 * draft per project in localStorage (best effort), so a refresh does not lose
 * work. The undo history is not kept across a reload.
 */

export type StrokeTool = 'pen' | 'line' | 'arc' | 'circle' | 'rectangle' | 'spline';
/** `draw` is the freehand pen; the shape tools and region select appear only while drawing. */
export type SketchTool = 'select' | 'draw' | 'erase' | 'line' | 'arc' | 'circle' | 'rectangle' | 'spline' | 'region';

export const DRAWING_TOOLS: readonly SketchTool[] = ['draw', 'line', 'arc', 'circle', 'rectangle', 'spline', 'region'];

export function isDrawingTool(tool: SketchTool): boolean {
  return DRAWING_TOOLS.includes(tool);
}

export interface SketchStroke {
  id: string;
  tool: StrokeTool;
  points: Point[];
  /** Per-point pen pressure in 0..1, only when the device reported it. */
  pressure?: number[];
  /** Line width in screen pixels. */
  width: number;
  created_at: string;
  source: 'human' | 'generated';
}

export type SaveState = 'idle' | 'saving' | 'saved' | 'failed';

export interface SketchDoc {
  strokes: SketchStroke[];
  /** Earlier stroke lists, newest last (undo), and undone ones (redo). */
  undo: SketchStroke[][];
  redo: SketchStroke[][];
  created_at: string;
  source: 'human';
  space: 'sketch_unit';
  scale: 'unknown';
  plane: { state: 'UNASSIGNED' };
  selection: string[];
  /** The twin item this sketch saves to, once it has been saved. */
  itemKey: string | null;
  /** The revision last saved (`SKT-...@n`). */
  savedRevision: string | null;
  savedNodeId: string | null;
  /** The id the next save uses; `null` when nothing is unsaved. */
  pendingSaveId: string | null;
  /** The save id currently on its way to the gateway. */
  inflightSaveId: string | null;
  saveState: SaveState;
  saveError: string | null;
  /** Bumped by every change, so an idle save can wait for the last one. */
  changeTick: number;
  viewport: { width_px: number; height_px: number } | null;
  referenceNodeId: string | null;
  referenceOpacity: number;
  /** True once the gateway's copy has been checked for this project. */
  remoteChecked: boolean;
}

export const UNDO_LIMIT = 200;
const DRAFT_PREFIX = 'metaforge.sketch.v1.';

export function emptySketch(now = new Date().toISOString()): SketchDoc {
  return {
    strokes: [],
    undo: [],
    redo: [],
    created_at: now,
    source: 'human',
    space: 'sketch_unit',
    scale: 'unknown',
    plane: { state: 'UNASSIGNED' },
    selection: [],
    itemKey: null,
    savedRevision: null,
    savedNodeId: null,
    pendingSaveId: null,
    inflightSaveId: null,
    saveState: 'idle',
    saveError: null,
    changeTick: 0,
    viewport: null,
    referenceNodeId: null,
    referenceOpacity: 0.5,
    remoteChecked: false,
  };
}

// ── Draft persistence (best effort; never required for correctness) ─────

interface Draft {
  strokes: SketchStroke[];
  created_at: string;
  itemKey: string | null;
  savedRevision: string | null;
  savedNodeId: string | null;
  pendingSaveId: string | null;
  viewport: SketchDoc['viewport'];
  referenceNodeId: string | null;
  referenceOpacity: number;
}

function isPoint(p: unknown): p is Point {
  return Array.isArray(p) && p.length === 2 && p.every((v) => typeof v === 'number' && Number.isFinite(v));
}

function isStroke(s: unknown): s is SketchStroke {
  const v = s as SketchStroke;
  return !!v && typeof v.id === 'string' && typeof v.tool === 'string' && Array.isArray(v.points) && v.points.every(isPoint);
}

export function readDraft(projectId: string): Partial<SketchDoc> | null {
  try {
    const raw = window.localStorage.getItem(DRAFT_PREFIX + projectId);
    if (!raw) return null;
    const d = JSON.parse(raw) as Partial<Draft>;
    if (!Array.isArray(d.strokes)) return null;
    return {
      strokes: d.strokes.filter(isStroke),
      created_at: typeof d.created_at === 'string' ? d.created_at : new Date().toISOString(),
      itemKey: d.itemKey ?? null,
      savedRevision: d.savedRevision ?? null,
      savedNodeId: d.savedNodeId ?? null,
      pendingSaveId: d.pendingSaveId ?? null,
      viewport: d.viewport ?? null,
      referenceNodeId: d.referenceNodeId ?? null,
      referenceOpacity: typeof d.referenceOpacity === 'number' ? d.referenceOpacity : 0.5,
    };
  } catch {
    return null;
  }
}

function writeDraft(projectId: string, doc: SketchDoc): void {
  try {
    const draft: Draft = {
      strokes: doc.strokes,
      created_at: doc.created_at,
      itemKey: doc.itemKey,
      savedRevision: doc.savedRevision,
      savedNodeId: doc.savedNodeId,
      pendingSaveId: doc.pendingSaveId,
      viewport: doc.viewport,
      referenceNodeId: doc.referenceNodeId,
      referenceOpacity: doc.referenceOpacity,
    };
    window.localStorage.setItem(DRAFT_PREFIX + projectId, JSON.stringify(draft));
  } catch {
    // Private mode or a full quota: the sketch still works, it just is not kept.
  }
}

// ── Store ─────────────────────────────────────────────────────────────────

export interface NewStroke {
  tool: StrokeTool;
  points: Point[];
  pressure?: number[];
  width?: number;
  source?: 'human' | 'generated';
}

interface SketchStore {
  docs: Record<string, SketchDoc>;
  tool: SketchTool;
  /** The project's sketch, restoring its draft on first use. */
  doc: (projectId: string) => SketchDoc;
  /** Put the project's doc (with its restored draft) into the store. */
  ensure: (projectId: string) => void;
  setTool: (tool: SketchTool) => void;
  addStroke: (projectId: string, stroke: NewStroke) => SketchStroke;
  erase: (projectId: string, ids: readonly string[]) => void;
  clear: (projectId: string) => void;
  undo: (projectId: string) => void;
  redo: (projectId: string) => void;
  select: (projectId: string, ids: readonly string[]) => void;
  deleteSelected: (projectId: string) => void;
  setViewport: (projectId: string, width: number, height: number) => void;
  setReference: (projectId: string, nodeId: string | null) => void;
  setReferenceOpacity: (projectId: string, opacity: number) => void;
  /** Start a save: returns the save id to send, or `null` when nothing is unsaved. */
  markSaving: (projectId: string) => string | null;
  markSaved: (projectId: string, saveId: string, result: { itemKey: string | null; itemRef: string | null; nodeId: string }) => void;
  markFailed: (projectId: string, saveId: string, error: string) => void;
  /** Adopt the gateway's current sketch when this browser has nothing of its own. */
  loadRemote: (
    projectId: string,
    remote: { strokes: SketchStroke[]; itemKey: string | null; itemRef: string | null; nodeId: string; createdAt: string | null } | null,
  ) => void;
  reset: () => void;
}

function initialDoc(projectId: string): SketchDoc {
  const draft = typeof window !== 'undefined' ? readDraft(projectId) : null;
  return { ...emptySketch(), ...(draft ?? {}) };
}

export const useSketchStore = create<SketchStore>((set, get) => {
  /** Apply `change` to a project's doc, persist the draft, and return the new doc. */
  const update = (projectId: string, change: (doc: SketchDoc) => Partial<SketchDoc>): SketchDoc => {
    const current = get().docs[projectId] ?? initialDoc(projectId);
    const next = { ...current, ...change(current) };
    set((s) => ({ docs: { ...s.docs, [projectId]: next } }));
    writeDraft(projectId, next);
    return next;
  };

  /** A content change: push undo, clear redo, and mark the sketch unsaved. */
  const edit = (doc: SketchDoc, strokes: SketchStroke[]): Partial<SketchDoc> => ({
    strokes,
    undo: [...doc.undo, doc.strokes].slice(-UNDO_LIMIT),
    redo: [],
    selection: doc.selection.filter((id) => strokes.some((s) => s.id === id)),
    pendingSaveId: doc.pendingSaveId && doc.pendingSaveId !== doc.inflightSaveId ? doc.pendingSaveId : generateId(),
    changeTick: doc.changeTick + 1,
    saveState: doc.saveState === 'saving' ? 'saving' : 'idle',
  });

  return {
    docs: {},
    tool: 'select',
    doc: (projectId) => get().docs[projectId] ?? initialDoc(projectId),
    ensure: (projectId) => {
      if (get().docs[projectId]) return;
      set((s) => ({ docs: { ...s.docs, [projectId]: s.docs[projectId] ?? initialDoc(projectId) } }));
    },
    setTool: (tool) => set({ tool }),
    addStroke: (projectId, input) => {
      const stroke: SketchStroke = {
        id: generateId(),
        tool: input.tool,
        points: input.points,
        ...(input.pressure && input.pressure.length === input.points.length ? { pressure: input.pressure } : {}),
        width: input.width ?? 2,
        created_at: new Date().toISOString(),
        source: input.source ?? 'human',
      };
      update(projectId, (doc) => edit(doc, [...doc.strokes, stroke]));
      return stroke;
    },
    erase: (projectId, ids) => {
      if (ids.length === 0) return;
      const drop = new Set(ids);
      update(projectId, (doc) => {
        const kept = doc.strokes.filter((s) => !drop.has(s.id));
        return kept.length === doc.strokes.length ? {} : edit(doc, kept);
      });
    },
    clear: (projectId) => update(projectId, (doc) => (doc.strokes.length ? edit(doc, []) : {})),
    undo: (projectId) =>
      update(projectId, (doc) => {
        const prev = doc.undo[doc.undo.length - 1];
        if (!prev) return {};
        return {
          ...edit(doc, prev),
          undo: doc.undo.slice(0, -1),
          redo: [...doc.redo, doc.strokes],
        };
      }),
    redo: (projectId) =>
      update(projectId, (doc) => {
        const next = doc.redo[doc.redo.length - 1];
        if (!next) return {};
        return {
          ...edit(doc, next),
          undo: [...doc.undo, doc.strokes].slice(-UNDO_LIMIT),
          redo: doc.redo.slice(0, -1),
        };
      }),
    select: (projectId, ids) => update(projectId, () => ({ selection: [...ids] })),
    deleteSelected: (projectId) => {
      const doc = get().doc(projectId);
      get().erase(projectId, doc.selection);
    },
    setViewport: (projectId, width, height) => {
      const doc = get().doc(projectId);
      if (doc.viewport?.width_px === width && doc.viewport?.height_px === height) return;
      update(projectId, () => ({ viewport: { width_px: width, height_px: height } }));
    },
    setReference: (projectId, nodeId) => update(projectId, () => ({ referenceNodeId: nodeId })),
    setReferenceOpacity: (projectId, opacity) =>
      update(projectId, () => ({ referenceOpacity: Math.max(0, Math.min(1, opacity)) })),
    markSaving: (projectId) => {
      const doc = get().doc(projectId);
      if (!doc.pendingSaveId) return null;
      const saveId = doc.pendingSaveId;
      update(projectId, () => ({ inflightSaveId: saveId, saveState: 'saving', saveError: null }));
      return saveId;
    },
    markSaved: (projectId, saveId, result) =>
      update(projectId, (doc) => {
        const caughtUp = doc.pendingSaveId === saveId;
        return {
          itemKey: result.itemKey ?? doc.itemKey,
          savedRevision: result.itemRef ?? doc.savedRevision,
          savedNodeId: result.nodeId,
          inflightSaveId: null,
          pendingSaveId: caughtUp ? null : doc.pendingSaveId,
          saveState: caughtUp ? 'saved' : 'idle',
          saveError: null,
        };
      }),
    markFailed: (projectId, saveId, error) =>
      update(projectId, (doc) => ({
        inflightSaveId: doc.inflightSaveId === saveId ? null : doc.inflightSaveId,
        saveState: 'failed',
        saveError: error,
      })),
    loadRemote: (projectId, remote) =>
      update(projectId, (doc) => {
        if (!remote || doc.strokes.length > 0 || doc.pendingSaveId || doc.undo.length > 0) {
          return { remoteChecked: true };
        }
        return {
          remoteChecked: true,
          strokes: remote.strokes.filter(isStroke),
          itemKey: remote.itemKey,
          savedRevision: remote.itemRef,
          savedNodeId: remote.nodeId,
          created_at: remote.createdAt ?? doc.created_at,
          saveState: 'saved',
        };
      }),
    reset: () => set({ docs: {}, tool: 'select' }),
  };
});

/** The project's sketch, kept current. */
export function useSketchDoc(projectId: string): SketchDoc {
  return useSketchStore((s) => s.docs[projectId]) ?? useSketchStore.getState().doc(projectId);
}
