import { useState } from 'react';
import { Compass, Undo2 } from 'lucide-react';
import { isPlaced, previewReassign, type SketchPlaneView, type ViewHint } from '../../api/endpoints/sketchPlanes';
import { refusalOf } from '../../api/endpoints/create';
import { useSketchPlaneActions, useSketchPlaneUi } from '../../hooks/use-sketch-plane';
import { displayScale, VIEW_NAMES, type Principal, type ReassignMode } from '../../lib/sketch/planeMath';
import { generateId } from '../../utils/id';
import { PlanePrompt } from './PlanePrompt';
import '../../styles/sketch-plane.css';

export const MODE_LABELS: Record<ReassignMode, string> = {
  preserve_local: 'Keep sketch coordinates',
  preserve_world: 'Keep world position',
  project_to_target: 'Project onto the new plane',
};

const fmtAxis = (v: readonly number[]) => v.map((c) => (c === 0 ? '0' : c > 0 ? `+${c}` : `${c}`)).join(',');

export interface SketchPlaneControlsProps {
  projectId: string;
  sketchKey: string;
  view: SketchPlaneView | null | undefined;
  modelRadius?: number | null;
}

/**
 * Contextual plane controls in the Engineering viewer while a sketch is
 * selected (UI-005, PLN-014): the active plane, its axes, the grid, the
 * unit, the state, "Normal to sketch" (camera only), and reassign with a
 * mode, a preview drawn in the scene, commit and undo. Not a permanent
 * panel: it exists only while a sketch is the selection.
 */
export function SketchPlaneControls({ projectId, sketchKey, view, modelRadius }: SketchPlaneControlsProps) {
  const plane = view?.plane ?? null;
  const placed = isPlaced(plane);
  const { reassign, undo } = useSketchPlaneActions(projectId, sketchKey);
  const showGrid = useSketchPlaneUi((s) => s.showGrid);
  const setShowGrid = useSketchPlaneUi((s) => s.setShowGrid);
  const showAxes = useSketchPlaneUi((s) => s.showAxes);
  const setShowAxes = useSketchPlaneUi((s) => s.setShowAxes);
  const preview = useSketchPlaneUi((s) => s.preview);
  const setPreview = useSketchPlaneUi((s) => s.setPreview);
  const normalToSketch = useSketchPlaneUi((s) => s.normalToSketch);
  const [target, setTarget] = useState<Principal>('XY');
  const [mode, setMode] = useState<ReassignMode>('preserve_local');
  const [error, setError] = useState<string | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [hint, setHint] = useState<ViewHint | null>(null);

  const state = plane?.state ?? 'UNASSIGNED';
  const scale = plane ? displayScale(plane, modelRadius) : null;

  const runPreview = async () => {
    setError(null);
    try {
      setPreview(await previewReassign(projectId, sketchKey, { op_id: generateId(), target, mode }));
    } catch (err) {
      setError(refusalOf(err));
    }
  };

  const commit = () => {
    setError(null);
    reassign.mutate(
      { target, mode, base_revision: view?.item_ref ?? null },
      { onError: (err) => setError(refusalOf(err)) },
    );
  };

  return (
    <div className="sk-plane-controls" role="group" aria-label="Sketch plane" data-testid="sketch-plane-controls">
      <div className="sk-plane-row">
        <strong data-testid="sketch-plane-name">
          {placed && plane?.principal ? `${VIEW_NAMES[plane.principal]} (${plane.principal})` : 'No plane'}
        </strong>
        <span className="sk-plane-badge" data-state={state} data-testid="sketch-plane-state">
          {state}
        </span>
        {view?.item_ref && <span className="font-mono">{view.item_ref}</span>}
      </div>
      {placed && plane && (
        <>
          <div className="sk-plane-row sk-plane-gizmo" aria-label="Plane axes" data-testid="sketch-plane-axes">
            <span className="u">U {fmtAxis(plane.u_axis)}</span>
            <span className="v">V {fmtAxis(plane.v_axis)}</span>
            <span className="n">N {fmtAxis(plane.normal)}</span>
          </div>
          <div className="sk-plane-row">
            <span data-testid="sketch-plane-unit">Unit: {plane.unit}</span>
            {scale?.assumed && <span title={scale.label}>· {scale.label}</span>}
          </div>
          <div className="sk-plane-row">
            <label>
              <input type="checkbox" checked={showGrid} onChange={(e) => setShowGrid(e.target.checked)} /> Grid
            </label>
            <label>
              <input type="checkbox" checked={showAxes} onChange={(e) => setShowAxes(e.target.checked)} /> Axes
            </label>
            <button
              type="button"
              onClick={() => normalToSketch?.()}
              disabled={!normalToSketch}
              title="Turn the view to face the sketch. Only the camera moves; the plane does not change."
            >
              <Compass size={11} aria-hidden="true" /> Normal to sketch
            </button>
          </div>
          <div className="sk-plane-row" aria-label="Reassign plane">
            <select aria-label="New plane" value={target} onChange={(e) => setTarget(e.target.value as Principal)}>
              {(['XZ', 'YZ', 'XY'] as Principal[]).map((p) => (
                <option key={p} value={p}>
                  {VIEW_NAMES[p]} ({p})
                </option>
              ))}
            </select>
            <select aria-label="Reassign mode" value={mode} onChange={(e) => setMode(e.target.value as ReassignMode)}>
              {(Object.keys(MODE_LABELS) as ReassignMode[]).map((m) => (
                <option key={m} value={m}>
                  {MODE_LABELS[m]}
                </option>
              ))}
            </select>
            <button type="button" onClick={() => void runPreview()}>
              Preview
            </button>
          </div>
          {preview && (
            <div className="sk-plane-row" data-testid="sketch-plane-preview">
              <span>
                Preview: {preview.exact ? 'exact' : `moves up to ${preview.max_off_plane.toPrecision(3)}`}
                {preview.warnings.length ? ` · ${preview.warnings[0]}` : ''}
              </span>
              <button type="button" onClick={commit} disabled={!preview.committable || reassign.isPending}>
                Apply
              </button>
              <button type="button" onClick={() => setPreview(null)}>
                Cancel
              </button>
            </div>
          )}
        </>
      )}
      <div className="sk-plane-row">
        {!placed && (
          <button
            type="button"
            onClick={() => {
              // Read the camera's view once, as a hint for the proposal.
              setHint(useSketchPlaneUi.getState().viewHint?.() ?? null);
              setChoosing((c) => !c);
            }}
          >
            Choose a plane
          </button>
        )}
        <button
          type="button"
          onClick={() => undo.mutate(view?.item_ref ?? null, { onError: (err) => setError(refusalOf(err)) })}
          disabled={!view?.can_undo || undo.isPending}
          title="Undo the last plane change (a new revision; history is kept)"
        >
          <Undo2 size={11} aria-hidden="true" /> Undo
        </button>
      </div>
      {choosing && !placed && (
        <PlanePrompt inline projectId={projectId} sketchKey={sketchKey} viewHint={hint} onDone={() => setChoosing(false)} />
      )}
      {error && (
        <p className="sk-plane-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
