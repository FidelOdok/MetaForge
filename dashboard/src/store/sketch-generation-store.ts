import { create } from 'zustand';
import type { SketchRegion, SketchStyle } from '../api/endpoints/sketchGeneration';
import type { Point } from '../components/sketch/geometry';

/**
 * The Create stage's concept generation controls, per project (FORGE-794).
 *
 * Kept apart from the sketch store and the chat queue: drawing never waits
 * for a generation, and a generation never goes through the chat send queue.
 * The region the person selected with the sketch layer's region tool is the
 * mask a region edit uses. Requests are debounced, so a burst of clicks or a
 * change of mind sends one request (FSG-012, PERF-003).
 */

export interface GenerationSession {
  open: boolean;
  intent: string;
  style: SketchStyle;
  negative: string;
  /** A rectangle drawn with the region tool, in sketch units. */
  mask: SketchRegion | null;
  /** Use the mask: edit only the selected region. */
  regionOnly: boolean;
  variants: number;
  /** The proposals picked for A/B compare (at most two). */
  compare: string[];
  /** The operation of the last request, while it is running. */
  pendingOperation: string | null;
  error: string | null;
  /** The gateway asked for confirmation before going past the hourly cap. */
  spendPrompt: string | null;
}

export const EMPTY_GENERATION: GenerationSession = {
  open: false,
  intent: '',
  style: 'industrial_sketch',
  negative: '',
  mask: null,
  regionOnly: false,
  variants: 1,
  compare: [],
  pendingOperation: null,
  error: null,
  spendPrompt: null,
};

/** Wait this long after the last Generate before sending (PERF-003). */
export const GENERATE_DEBOUNCE_MS = 400;

interface GenerationStore {
  sessions: Record<string, GenerationSession>;
  session: (projectId: string) => GenerationSession;
  patch: (projectId: string, change: Partial<GenerationSession>) => void;
  /** The region tool's rectangle becomes the mask, and region-only is on. */
  setRegion: (projectId: string, a: Point, b: Point) => void;
  clearRegion: (projectId: string) => void;
  toggleCompare: (projectId: string, nodeId: string) => void;
}

export function rectanglePolygon(a: Point, b: Point): Point[] {
  const [x0, x1] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])];
  const [y0, y1] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])];
  return [
    [x0, y0],
    [x1, y0],
    [x1, y1],
    [x0, y1],
  ];
}

export const useSketchGenerationStore = create<GenerationStore>()((set, get) => {
  const patch = (projectId: string, change: Partial<GenerationSession>) =>
    set((st) => ({
      sessions: { ...st.sessions, [projectId]: { ...(st.sessions[projectId] ?? EMPTY_GENERATION), ...change } },
    }));
  return {
    sessions: {},
    session: (projectId) => get().sessions[projectId] ?? EMPTY_GENERATION,
    patch,
    setRegion: (projectId, a, b) => {
      if (Math.abs(a[0] - b[0]) < 1e-3 || Math.abs(a[1] - b[1]) < 1e-3) return;
      patch(projectId, { mask: { polygon: rectanglePolygon(a, b) }, regionOnly: true });
    },
    clearRegion: (projectId) => patch(projectId, { mask: null, regionOnly: false }),
    toggleCompare: (projectId, nodeId) => {
      const current = get().session(projectId).compare;
      const next = current.includes(nodeId)
        ? current.filter((id) => id !== nodeId)
        : [...current, nodeId].slice(-2);
      patch(projectId, { compare: next });
    },
  };
});

const timers = new Map<string, ReturnType<typeof setTimeout>>();

/** Run `fn` once, `GENERATE_DEBOUNCE_MS` after the last call for this project. */
export function debounceGenerate(projectId: string, fn: () => void, ms = GENERATE_DEBOUNCE_MS): void {
  const pending = timers.get(projectId);
  if (pending) clearTimeout(pending);
  timers.set(
    projectId,
    setTimeout(() => {
      timers.delete(projectId);
      fn();
    }, ms),
  );
}
