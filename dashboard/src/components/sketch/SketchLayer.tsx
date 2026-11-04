import { memo, useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  Box,
  Circle,
  Eraser,
  Image as ImageIcon,
  Minus,
  MousePointer2,
  Pencil,
  PenTool,
  Redo2,
  Save,
  Spline,
  Square,
  SquareDashed,
  Undo2,
} from 'lucide-react';
import { getCurrentSketch, saveSketch } from '../../api/endpoints/sketches';
import { refusalOf } from '../../api/endpoints/create';
import { nodeFileUrl } from '../../api/endpoints/twin';
import { twinKeys } from '../../hooks/use-twin';
import { isDrawingTool, useSketchStore, type SketchStroke, type SketchTool, type StrokeTool } from '../../store/sketch-store';
import type { TwinNode } from '../../types/twin';
import {
  arcShape,
  circleShape,
  dist,
  hitsPolyline,
  lineShape,
  pathD,
  pathSegment,
  rectangleShape,
  splineShape,
  toSketch,
  touchesRect,
  unitPx,
  viewBoxFor,
  type Point,
  type Viewport,
} from './geometry';
import { PlanePrompt } from './PlanePrompt';
import { SketchCompiler } from './SketchCompiler';
import { useSketchPlane } from '../../hooks/use-sketch-plane';
import { isPlaced, needsReattachment } from '../../api/endpoints/sketchPlanes';
import { ReattachChoices } from './ReattachChoices';
import { VIEW_NAMES } from '../../lib/sketch/planeMath';
import '../../styles/sketch.css';

/** Idle time after the last stroke before the sketch saves itself. */
export const AUTOSAVE_MS = 1500;
/** Eraser reach, in screen pixels. */
const ERASER_PX = 10;
/** Points closer than this (screen pixels) add nothing to a stroke. */
const MIN_STEP_PX = 0.75;
const PEN_WIDTH = 2.5;

interface Gesture {
  pointerId: number;
  tool: SketchTool;
  view: Viewport;
  rect: { left: number; top: number };
  raw: Point[];
  pressure: number[];
  pen: boolean;
  d: string;
  erased: Set<string>;
}

const BASIC_TOOLS: { tool: SketchTool; label: string; key: string; Icon: typeof Pencil }[] = [
  { tool: 'select', label: 'Select', key: 'V', Icon: MousePointer2 },
  { tool: 'draw', label: 'Draw', key: 'P', Icon: Pencil },
  { tool: 'erase', label: 'Erase', key: 'E', Icon: Eraser },
];

/** Shape and region tools: shown only while drawing (UXS-003). */
const SHAPE_TOOLS: { tool: SketchTool; label: string; Icon: typeof Pencil }[] = [
  { tool: 'line', label: 'Line', Icon: Minus },
  { tool: 'arc', label: 'Arc', Icon: Spline },
  { tool: 'circle', label: 'Circle', Icon: Circle },
  { tool: 'rectangle', label: 'Rectangle', Icon: Square },
  { tool: 'spline', label: 'Smooth curve', Icon: PenTool },
  { tool: 'region', label: 'Select a region', Icon: SquareDashed },
];

const TOOL_KEYS: Record<string, SketchTool> = { v: 'select', p: 'draw', e: 'erase' };

const STROKE_TOOL: Partial<Record<SketchTool, StrokeTool>> = {
  draw: 'pen',
  line: 'line',
  arc: 'arc',
  circle: 'circle',
  rectangle: 'rectangle',
  spline: 'spline',
};

function isEditable(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.closest !== 'function') return false;
  return !!el.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]');
}

/** The finished shape for a gesture, in sketch units. */
function shapeFor(tool: SketchTool, raw: readonly Point[]): Point[] {
  const a = raw[0];
  const b = raw[raw.length - 1];
  if (!a || !b) return [];
  switch (tool) {
    case 'line':
      return lineShape(a, b);
    case 'rectangle':
      return rectangleShape(a, b);
    case 'circle':
      return circleShape(a, b);
    case 'arc':
      return arcShape(raw);
    case 'spline':
      return splineShape(raw);
    default:
      return [...raw];
  }
}

export interface SketchLayerProps {
  projectId: string;
  /** Project images and renders that can be shown under the strokes. */
  referenceNodes?: TwinNode[];
}

/**
 * The freehand sketch layer over the Create product stage (FORGE-789).
 *
 * Strokes render locally the moment the pointer moves: the live stroke is a
 * single SVG path updated in place from coalesced pointer events, and only a
 * finished stroke reaches the store. Nothing on that path touches the
 * network. The sketch saves itself after a short idle, or on *Save sketch*,
 * as a revision of the project's `sketch_strokes` item. It is a human sketch,
 * not CAD: it has no scale and no plane yet, and the layer says so.
 */
export function SketchLayer({ projectId, referenceNodes = [] }: SketchLayerProps) {
  const doc = useSketchStore((s) => s.docs[projectId]) ?? useSketchStore.getState().doc(projectId);
  const tool = useSketchStore((s) => s.tool);
  const setTool = useSketchStore((s) => s.setTool);
  const queryClient = useQueryClient();

  const rootRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const liveRef = useRef<SVGPathElement>(null);
  const regionRef = useRef<SVGRectElement>(null);
  const gesture = useRef<Gesture | null>(null);
  const [size, setSize] = useState<Viewport>({ width: 1, height: 1 });
  // The plane prompt appears only when the person asks to use the sketch in 3D (UXS-006).
  const [planePrompt, setPlanePrompt] = useState(false);
  const planeQuery = useSketchPlane(projectId, doc.itemKey);
  const plane = planeQuery.data?.plane ?? null;
  const placed = isPlaced(plane);
  // "Decide later" hides the reattach choice until the plane changes again.
  const [reattachLater, setReattachLater] = useState(false);
  useEffect(() => {
    setReattachLater(false);
  }, [planeQuery.data?.item_ref]);

  // Materialise the project's doc (and its restored draft) in the store once.
  useEffect(() => {
    useSketchStore.getState().ensure(projectId);
  }, [projectId]);

  // Track the drawing viewport so the SVG maps sketch units onto it one to one.
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) setSize({ width: r.width, height: r.height });
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Adopt the gateway's current sketch when this browser has none of its own.
  useEffect(() => {
    const current = useSketchStore.getState().doc(projectId);
    if (current.remoteChecked || current.strokes.length > 0 || current.pendingSaveId) return;
    const ctl = new AbortController();
    getCurrentSketch(projectId, ctl.signal)
      .then((remote) =>
        useSketchStore.getState().loadRemote(
          projectId,
          remote && {
            strokes: remote.strokes,
            itemKey: remote.item_key,
            itemRef: remote.item_ref,
            nodeId: remote.node_id,
            createdAt: remote.created_at,
          },
        ),
      )
      .catch(() => {
        if (!ctl.signal.aborted) useSketchStore.getState().loadRemote(projectId, null);
      });
    return () => ctl.abort();
  }, [projectId]);

  const save = useCallback(async () => {
    const store = useSketchStore.getState();
    const saveId = store.markSaving(projectId);
    if (!saveId) return;
    const current = store.doc(projectId);
    try {
      const result = await saveSketch(projectId, {
        save_id: saveId,
        item_key: current.itemKey,
        base_revision: current.savedRevision,
        strokes: current.strokes,
        viewport: current.viewport,
        reference_node_id: current.referenceNodeId,
        plane_state: 'UNASSIGNED',
        scale: 'unknown',
      });
      useSketchStore.getState().markSaved(projectId, saveId, {
        itemKey: result.item_key,
        itemRef: result.item_ref,
        nodeId: result.node_id,
      });
      void queryClient.invalidateQueries({ queryKey: twinKeys.all });
    } catch (err) {
      useSketchStore.getState().markFailed(projectId, saveId, refusalOf(err));
    }
  }, [projectId, queryClient]);

  // Save after a short idle following the last finished stroke.
  useEffect(() => {
    if (!doc.pendingSaveId || doc.inflightSaveId || doc.saveState === 'failed') return;
    const timer = window.setTimeout(() => void save(), AUTOSAVE_MS);
    return () => window.clearTimeout(timer);
  }, [doc.changeTick, doc.pendingSaveId, doc.inflightSaveId, doc.saveState, save]);

  const resetLive = () => {
    liveRef.current?.setAttribute('d', '');
    regionRef.current?.setAttribute('width', '0');
    regionRef.current?.setAttribute('height', '0');
    svgRef.current?.querySelectorAll('.is-erasing').forEach((el) => el.classList.remove('is-erasing'));
  };

  const eraseAt = (g: Gesture, p: Point) => {
    const radius = ERASER_PX / unitPx(g.view);
    for (const s of useSketchStore.getState().doc(projectId).strokes) {
      if (g.erased.has(s.id) || !hitsPolyline(s.points, p, radius)) continue;
      g.erased.add(s.id);
      svgRef.current?.querySelectorAll('[data-stroke-id]').forEach((el) => {
        if (el.getAttribute('data-stroke-id') === s.id) el.classList.add('is-erasing');
      });
    }
  };

  const onPointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (tool === 'select' || gesture.current) return;
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    const svg = e.currentTarget;
    const r = svg.getBoundingClientRect();
    const view = { width: r.width || size.width, height: r.height || size.height };
    svg.setPointerCapture?.(e.pointerId);
    const p = toSketch(e.clientX - r.left, e.clientY - r.top, view);
    const pen = e.pointerType === 'pen';
    const g: Gesture = {
      pointerId: e.pointerId,
      tool,
      view,
      rect: { left: r.left, top: r.top },
      raw: [p],
      pressure: pen ? [e.pressure] : [],
      pen,
      d: pathD([p]),
      erased: new Set(),
    };
    gesture.current = g;
    if (tool === 'draw') liveRef.current?.setAttribute('d', g.d);
    if (tool === 'erase') eraseAt(g, p);
    e.preventDefault();
  };

  const onPointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    const g = gesture.current;
    if (!g || e.pointerId !== g.pointerId) return;
    const native = e.nativeEvent as PointerEvent;
    const coalesced = typeof native.getCoalescedEvents === 'function' ? native.getCoalescedEvents() : [];
    const events: { clientX: number; clientY: number; pressure: number }[] = coalesced.length ? coalesced : [native];
    const minStep = MIN_STEP_PX / unitPx(g.view);
    let added = false;
    for (const ev of events) {
      const p = toSketch(ev.clientX - g.rect.left, ev.clientY - g.rect.top, g.view);
      const last = g.raw[g.raw.length - 1]!;
      if (dist(p, last) < minStep) continue;
      g.raw.push(p);
      if (g.pen) g.pressure.push(ev.pressure);
      if (g.tool === 'draw') g.d += pathSegment(p);
      if (g.tool === 'erase') {
        // A fast swipe skips pixels: test the whole segment, not just its ends.
        const steps = Math.ceil((dist(last, p) * unitPx(g.view)) / (ERASER_PX / 2));
        for (let i = 1; i <= steps; i += 1) {
          const t = i / steps;
          eraseAt(g, [last[0] + (p[0] - last[0]) * t, last[1] + (p[1] - last[1]) * t]);
        }
      }
      added = true;
    }
    if (!added) return;
    if (g.tool === 'draw') {
      liveRef.current?.setAttribute('d', g.d);
    } else if (g.tool === 'region') {
      const a = g.raw[0]!;
      const b = g.raw[g.raw.length - 1]!;
      const rect = regionRef.current;
      rect?.setAttribute('x', String(Math.min(a[0], b[0])));
      rect?.setAttribute('y', String(Math.min(a[1], b[1])));
      rect?.setAttribute('width', String(Math.abs(a[0] - b[0])));
      rect?.setAttribute('height', String(Math.abs(a[1] - b[1])));
    } else if (g.tool !== 'erase') {
      liveRef.current?.setAttribute('d', pathD(shapeFor(g.tool, g.raw)));
    }
  };

  const finish = (e: ReactPointerEvent<SVGSVGElement>, cancelled: boolean) => {
    const g = gesture.current;
    if (!g || e.pointerId !== g.pointerId) return;
    gesture.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    resetLive();
    if (cancelled) return;
    const store = useSketchStore.getState();
    store.setViewport(projectId, Math.round(g.view.width), Math.round(g.view.height));
    if (g.tool === 'erase') {
      store.erase(projectId, [...g.erased]);
      return;
    }
    const a = g.raw[0]!;
    const b = g.raw[g.raw.length - 1]!;
    if (g.tool === 'region') {
      const hit = store.doc(projectId).strokes.filter((s) => touchesRect(s.points, a, b)).map((s) => s.id);
      store.select(projectId, hit);
      return;
    }
    const strokeTool = STROKE_TOOL[g.tool];
    if (!strokeTool) return;
    if (strokeTool !== 'pen' && dist(a, b) * unitPx(g.view) < 2 && g.tool !== 'arc' && g.tool !== 'spline') return;
    const points = strokeTool === 'pen' ? g.raw : shapeFor(g.tool, g.raw);
    if (points.length === 0) return;
    store.addStroke(projectId, {
      tool: strokeTool,
      points,
      pressure: strokeTool === 'pen' && g.pen ? g.pressure : undefined,
      width: PEN_WIDTH,
    });
  };

  // Keyboard: undo, redo, Esc, Delete. Never while typing in the composer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isEditable(e.target)) return;
      const store = useSketchStore.getState();
      const inLayer = !!rootRef.current && rootRef.current.contains(document.activeElement);
      if (store.tool === 'select' && !inLayer && store.doc(projectId).selection.length === 0) return;
      const mod = e.ctrlKey || e.metaKey;
      const key = e.key.toLowerCase();
      if (mod && key === 'z') {
        e.preventDefault();
        if (e.shiftKey) store.redo(projectId);
        else store.undo(projectId);
      } else if (mod && key === 'y') {
        e.preventDefault();
        store.redo(projectId);
      } else if (e.key === 'Escape') {
        if (gesture.current) {
          gesture.current = null;
          resetLive();
        } else if (store.doc(projectId).selection.length) {
          store.select(projectId, []);
        } else if (store.tool !== 'select') {
          store.setTool('select');
        }
      } else if ((e.key === 'Delete' || e.key === 'Backspace') && store.doc(projectId).selection.length) {
        e.preventDefault();
        store.deleteSelected(projectId);
      } else if (!mod && !e.altKey && TOOL_KEYS[key]) {
        store.setTool(TOOL_KEYS[key]!);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [projectId]);

  const drawing = isDrawingTool(tool);
  const reference = referenceNodes.find((n) => n.id === doc.referenceNodeId) ?? null;
  const store = useSketchStore.getState();

  return (
    <div
      ref={rootRef}
      className={`sk-layer${tool === 'select' ? ' is-passive' : ' is-active'}`}
      data-testid="sketch-layer"
      data-tool={tool}
    >
      {reference && (
        <figure className="sk-reference" style={{ opacity: doc.referenceOpacity }}>
          <img src={nodeFileUrl(reference.id)} alt="" draggable={false} />
        </figure>
      )}
      {reference && <span className="sk-reference-label">Reference · illustrative</span>}

      <svg
        ref={svgRef}
        className="sk-svg"
        data-testid="sketch-svg"
        viewBox={viewBoxFor(size)}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`Your sketch, ${doc.strokes.length} ${doc.strokes.length === 1 ? 'stroke' : 'strokes'}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={(e) => finish(e, false)}
        onPointerCancel={(e) => finish(e, true)}
        style={{ cursor: tool === 'erase' ? 'cell' : drawing ? 'crosshair' : undefined }}
      >
        <StrokePaths
          strokes={doc.strokes}
          selection={doc.selection}
          onSelect={(id, add) => store.select(projectId, add ? [...doc.selection, id] : [id])}
        />
        <path ref={liveRef} className="sk-live" data-testid="sketch-live" d="" vectorEffect="non-scaling-stroke" />
        <rect ref={regionRef} className="sk-region" width="0" height="0" vectorEffect="non-scaling-stroke" />
      </svg>

      <div className="sk-toolbar" role="toolbar" aria-label="Sketch tools" aria-orientation="vertical">
        {BASIC_TOOLS.map(({ tool: t, label, key, Icon }) => (
          <button
            key={t}
            type="button"
            className={`sk-tool ${tool === t || (t === 'draw' && drawing) ? 'is-on' : ''}`}
            aria-pressed={tool === t}
            aria-label={label}
            title={`${label} (${key})`}
            onClick={() => setTool(t)}
          >
            <Icon size={16} aria-hidden="true" />
          </button>
        ))}
        <button
          type="button"
          className="sk-tool"
          aria-label="Undo"
          title="Undo (Ctrl+Z)"
          disabled={doc.undo.length === 0}
          onClick={() => store.undo(projectId)}
        >
          <Undo2 size={16} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="sk-tool"
          aria-label="Redo"
          title="Redo (Ctrl+Shift+Z)"
          disabled={doc.redo.length === 0}
          onClick={() => store.redo(projectId)}
        >
          <Redo2 size={16} aria-hidden="true" />
        </button>
        {drawing && (
          <div className="sk-shapes" role="group" aria-label="Shapes and region">
            {SHAPE_TOOLS.map(({ tool: t, label, Icon }) => (
              <button
                key={t}
                type="button"
                className={`sk-tool ${tool === t ? 'is-on' : ''}`}
                aria-pressed={tool === t}
                aria-label={label}
                title={label}
                onClick={() => setTool(t)}
              >
                <Icon size={16} aria-hidden="true" />
              </button>
            ))}
          </div>
        )}
        {referenceNodes.length > 0 && (
          <ReferencePicker
            nodes={referenceNodes}
            value={doc.referenceNodeId}
            opacity={doc.referenceOpacity}
            onChange={(id) => store.setReference(projectId, id)}
            onOpacity={(v) => store.setReferenceOpacity(projectId, v)}
          />
        )}
      </div>

      {(doc.strokes.length > 0 || doc.savedRevision || doc.pendingSaveId) && (
        <div className="sk-status" role="status">
          <span className="sk-honesty" title="A human sketch is not a CAD model. Its size is unknown and it has no plane until you confirm one.">
            Your sketch · not CAD · scale unknown ·{' '}
            {placed && plane?.principal ? `on the ${VIEW_NAMES[plane.principal]} plane` : 'no plane yet'}
          </span>
          <span className="sk-save-state" data-testid="sketch-save-state">
            {doc.saveState === 'saving'
              ? 'Saving…'
              : doc.saveState === 'failed'
                ? `Not saved: ${doc.saveError ?? 'try again'}`
                : doc.pendingSaveId
                  ? 'Unsaved changes'
                  : doc.savedRevision
                    ? `Saved · ${doc.savedRevision}`
                    : ''}
          </span>
          <button
            type="button"
            className="sk-save"
            onClick={() => void save()}
            disabled={!doc.pendingSaveId || doc.saveState === 'saving'}
          >
            <Save size={14} aria-hidden="true" /> {doc.saveState === 'failed' ? 'Retry save' : 'Save sketch'}
          </button>
          {doc.itemKey && !placed && (
            <button
              type="button"
              className="sk-save"
              title="Choose the plane this sketch sits on, so it can be shown in 3D"
              aria-expanded={planePrompt}
              onClick={() => (needsReattachment(plane) ? setReattachLater(false) : setPlanePrompt((open) => !open))}
            >
              <Box size={14} aria-hidden="true" /> Use in 3D
            </button>
          )}
        </div>
      )}

      {planePrompt && doc.itemKey && !needsReattachment(plane) && (
        <PlanePrompt projectId={projectId} sketchKey={doc.itemKey} onDone={() => setPlanePrompt(false)} />
      )}

      {/* CDE slice 9: the surface the sketch was on changed; plain words in Create. */}
      {doc.itemKey && plane && needsReattachment(plane) && !reattachLater && (
        <div className="sk-plane-prompt">
          <ReattachChoices projectId={projectId} sketchKey={doc.itemKey} plane={plane} onClose={() => setReattachLater(true)} />
        </div>
      )}

      {/* FORGE-791: Make editable (the sketch compiler). */}
      <SketchCompiler
        projectId={projectId}
        strokes={doc.strokes}
        itemKey={doc.itemKey}
        savedRevision={doc.savedRevision}
        unsaved={!!doc.pendingSaveId}
        view={size}
        plane={placed && plane ? { plane_id: plane.plane_id, unit: plane.unit } : null}
      />
    </div>
  );
}

const StrokePaths = memo(function StrokePaths({
  strokes,
  selection,
  onSelect,
}: {
  strokes: SketchStroke[];
  selection: string[];
  onSelect: (id: string, add: boolean) => void;
}) {
  const paths = useMemo(() => strokes.map((s) => ({ id: s.id, tool: s.tool, width: s.width, d: pathD(s.points) })), [strokes]);
  const selected = useMemo(() => new Set(selection), [selection]);
  return (
    <g className="sk-strokes">
      {paths.map((p) => (
        <path
          key={p.id}
          d={p.d}
          data-stroke-id={p.id}
          data-tool={p.tool}
          className={`sk-stroke${selected.has(p.id) ? ' is-selected' : ''}`}
          strokeWidth={p.width}
          vectorEffect="non-scaling-stroke"
          onClick={(e) => onSelect(p.id, e.shiftKey)}
        />
      ))}
    </g>
  );
});

function ReferencePicker({
  nodes,
  value,
  opacity,
  onChange,
  onOpacity,
}: {
  nodes: TwinNode[];
  value: string | null;
  opacity: number;
  onChange: (id: string | null) => void;
  onOpacity: (v: number) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sk-ref">
      <button
        type="button"
        className={`sk-tool ${value ? 'is-on' : ''}`}
        aria-label="Reference image under the sketch"
        aria-expanded={open}
        title="Show a project image under the sketch"
        onClick={() => setOpen((o) => !o)}
      >
        <ImageIcon size={16} aria-hidden="true" />
      </button>
      {open && (
        <div className="sk-ref-panel">
          <label>
            <span>Reference (illustrative)</span>
            <select value={value ?? ''} onChange={(e) => onChange(e.target.value || null)}>
              <option value="">None</option>
              {nodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.name}
                </option>
              ))}
            </select>
          </label>
          {value && (
            <label>
              <span>Opacity</span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={opacity}
                onChange={(e) => onOpacity(Number(e.target.value))}
              />
            </label>
          )}
        </div>
      )}
    </div>
  );
}
