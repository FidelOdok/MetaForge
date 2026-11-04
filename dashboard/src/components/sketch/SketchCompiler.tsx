import { useCallback, useMemo, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Check, Columns2, Info, Redo2, RotateCcw, Sparkles, Undo2, Wand2, X } from 'lucide-react';
import { approveParametricSketch, promoteInterpretation, storeInterpretation } from '../../api/endpoints/sketchCompiler';
import { refusalOf } from '../../api/endpoints/create';
import { twinKeys } from '../../hooks/use-twin';
import { acceptConfident, applyChoice, confirmDimension, interpret, isConfident, measured, setStatus, setUnit, toDrawn, toWire, type CompiledSketch } from '../../lib/sketch/compiler';
import { constraintLabel, formatValue, primitiveLabels } from '../../lib/sketch/describe';
import { DIMENSION_TYPES, type Primitive, type SketchConstraint, type SketchUnit } from '../../lib/sketch/model';
import { CONSTRAINT_GLYPH, constraintAnchor, primitivePolyline } from '../../lib/sketch/render';
import { useSketchCompilerStore } from '../../store/sketch-compiler-store';
import type { SketchStroke } from '../../store/sketch-store';
import { generateId } from '../../utils/id';
import { pathD, toScreen, unitPx, viewBoxFor, type Viewport } from './geometry';
import '../../styles/sketch-compiler.css';

export interface SketchCompilerProps {
  projectId: string;
  strokes: readonly SketchStroke[];
  /** The sketch item and the revision last saved (`SKT-...@n`). */
  itemKey: string | null;
  savedRevision: string | null;
  /** True while the drawing has changes not saved yet. */
  unsaved: boolean;
  view: Viewport;
  /** The sketch's plane, when it is placed (carried into the proposal, never read). */
  plane: { plane_id: string; unit: string } | null;
}

/** How many proposed rules the panel lists before *Show all*. */
const RULES_SHOWN = 6;

const STATUS_TONE: Record<string, string> = { fully: 'is-good', under: 'is-info', over: 'is-bad', not_solved: 'is-info' };

/**
 * *Make editable*: the sketch compiler on the Create stage (FORGE-791).
 *
 * Fits the saved sketch in the browser, draws the shapes over the strokes
 * (solid when confident, dashed when not), proposes rules as small badges to
 * accept, lists dimensions as estimates to confirm by typing, and explains
 * what is left in plain words. Every change re-solves from the current
 * shapes; nothing is recognised again. The proposal is stored as a
 * `sketch_interpretation`, and *Make it a parametric sketch* promotes the
 * accepted rules for sign-off.
 */
export function SketchCompiler({ projectId, strokes, itemKey, savedRevision, unsaved, view, plane }: SketchCompilerProps) {
  const doc = useSketchCompilerStore((s) => s.docs[projectId]) ?? useSketchCompilerStore.getState().doc(projectId);
  const store = useSketchCompilerStore.getState();
  const queryClient = useQueryClient();
  const sketch = doc.sketch;
  const [showAll, setShowAll] = useState(false);

  const persist = useCallback(async (): Promise<string | null> => {
    const current = useSketchCompilerStore.getState().doc(projectId);
    if (!current.sketch || !itemKey) return null;
    if (!current.dirty && current.savedRef) return current.savedRef;
    const saveId = current.saveId ?? generateId();
    store.patch(projectId, { busy: 'saving', error: null, saveId });
    try {
      const wire = { ...toWire(current.sketch), interpretation_id: saveId };
      const saved = await storeInterpretation(projectId, itemKey, wire, current.savedRef);
      // The gateway's plane decides the unit; adopt it without an undo step.
      const unit: SketchUnit = saved.unit === 'mm' || saved.unit === 'in' ? saved.unit : 'unknown';
      const adopted = setUnit({ ...current.sketch, plane_id: saved.plane_id }, unit);
      store.patch(projectId, { sketch: adopted, lastSaved: adopted, savedRef: saved.item_ref, dirty: false, saveId: null, busy: 'idle' });
      void queryClient.invalidateQueries({ queryKey: twinKeys.all });
      return saved.item_ref;
    } catch (err) {
      store.patch(projectId, { busy: 'idle', error: refusalOf(err) });
      return null;
    }
  }, [itemKey, projectId, queryClient, store]);

  const makeEditable = useCallback(() => {
    if (!savedRevision) return;
    const next = interpret(
      strokes.map((s) => ({ id: s.id, points: s.points })),
      { interpretationId: generateId(), sketchRef: savedRevision, planeId: plane?.plane_id ?? null, unitPx: unitPx(view) },
    );
    store.open(projectId, next);
    void persist();
  }, [persist, plane, projectId, savedRevision, store, strokes, view]);

  const edit = (next: CompiledSketch) => store.apply(projectId, next);

  const promote = async () => {
    const ref = await persist();
    if (!ref) return;
    const current = useSketchCompilerStore.getState().doc(projectId);
    const promoteId = current.promoteId ?? generateId();
    store.patch(projectId, { busy: 'promoting', error: null, promoteId });
    try {
      const out = await promoteInterpretation(projectId, ref, promoteId);
      store.patch(projectId, {
        busy: 'idle',
        promotion: { nodeId: out.node_id, itemRef: out.item_ref, approvalId: out.approval_id, approved: out.approved },
      });
      void queryClient.invalidateQueries({ queryKey: twinKeys.all });
    } catch (err) {
      store.patch(projectId, { busy: 'idle', error: refusalOf(err) });
    }
  };

  const approve = async () => {
    const promotion = useSketchCompilerStore.getState().doc(projectId).promotion;
    if (!promotion) return;
    store.patch(projectId, { busy: 'approving', error: null });
    try {
      await approveParametricSketch(promotion.nodeId);
      store.patch(projectId, { busy: 'idle', promotion: { ...promotion, approved: true } });
      void queryClient.invalidateQueries({ queryKey: twinKeys.all });
    } catch (err) {
      store.patch(projectId, { busy: 'idle', error: refusalOf(err) });
    }
  };

  const labels = useMemo(() => primitiveLabels(sketch?.primitives ?? []), [sketch]);

  if (!sketch) {
    if (!itemKey || strokes.length === 0) return null;
    const why = unsaved || !savedRevision ? 'Save the sketch first' : undefined;
    return (
      <div className="skc-launch">
        <button
          type="button"
          className="skc-btn skc-primary"
          onClick={makeEditable}
          disabled={!!why}
          title={why ?? 'Turn the strokes into shapes with sizes and rules you can edit'}
          data-testid="sketch-make-editable"
        >
          <Wand2 size={14} aria-hidden="true" /> Make editable
        </button>
      </div>
    );
  }

  const solve = sketch.solve;
  const stale = sketch.sketch_ref !== savedRevision;
  const rules = sketch.constraints.filter((c) => !DIMENSION_TYPES.includes(c.type));
  const proposed = rules.filter((c) => c.status === 'proposed');
  const dims = sketch.constraints.filter((c) => DIMENSION_TYPES.includes(c.type) && c.status !== 'rejected');
  const accepted = sketch.constraints.filter((c) => c.status === 'accepted');
  const conflict = solve?.conflicts[0] ?? null;
  const busy = doc.busy !== 'idle';

  return (
    <>
      <Overlay sketch={sketch} original={doc.compare ? doc.original : null} view={view} labels={labels} onToggle={(c) => edit(setStatus(sketch, [c.id], c.status === 'accepted' ? 'proposed' : 'accepted'))} />
      <aside className="skc-panel" aria-label="Editable sketch" data-testid="sketch-compiler">
        <header className="skc-head">
          <strong>Editable sketch</strong>
          <span className="skc-tag" title="Read off your drawing. Nothing here is a measurement until you confirm it.">estimated</span>
          <button type="button" className="skc-icon" aria-label="Close editable sketch" onClick={() => store.close(projectId)}>
            <X size={14} aria-hidden="true" />
          </button>
        </header>

        {stale && (
          <p className="skc-note is-bad" role="alert">
            Your sketch changed since this was made.{' '}
            <button type="button" className="skc-link" onClick={makeEditable} disabled={unsaved}>
              Make editable again
            </button>
          </p>
        )}

        {solve && (
          <p className={`skc-status ${STATUS_TONE[solve.status] ?? ''}`} role="status" data-testid="sketch-compiler-status">
            {solve.message}
          </p>
        )}

        {conflict && (
          <div className="skc-conflict" data-testid="sketch-compiler-conflict">
            {conflict.choices.map((choice) => (
              <button key={`${choice.kind}-${choice.constraint_id}`} type="button" className="skc-btn" onClick={() => edit(applyChoice(sketch, choice))}>
                {choice.label}
              </button>
            ))}
          </div>
        )}

        {proposed.length > 0 && (
          <section className="skc-section">
            <div className="skc-row">
              <span>
                {proposed.length} {proposed.length === 1 ? 'rule' : 'rules'} to check
              </span>
              <button type="button" className="skc-btn" onClick={() => edit(acceptConfident(sketch))} data-testid="sketch-accept-likely">
                <Sparkles size={13} aria-hidden="true" /> Accept the likely ones
              </button>
            </div>
            <ul className="skc-list">
              {(showAll ? proposed : proposed.slice(0, RULES_SHOWN)).map((c) => (
                <li key={c.id} className={isConfident(c) ? '' : 'is-unsure'}>
                  <span>{constraintLabel(c, labels)}</span>
                  <button type="button" className="skc-icon" aria-label={`Accept: ${constraintLabel(c, labels)}`} onClick={() => edit(setStatus(sketch, [c.id], 'accepted'))}>
                    <Check size={13} aria-hidden="true" />
                  </button>
                  <button type="button" className="skc-icon" aria-label={`Reject: ${constraintLabel(c, labels)}`} onClick={() => edit(setStatus(sketch, [c.id], 'rejected'))}>
                    <X size={13} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
            {proposed.length > RULES_SHOWN && (
              <button type="button" className="skc-link" onClick={() => setShowAll((v) => !v)}>
                {showAll ? 'Show fewer' : `Show all ${proposed.length}`}
              </button>
            )}
          </section>
        )}

        {dims.length > 0 && (
          <section className="skc-section" aria-label="Dimensions">
            <div className="skc-row">
              <span>Sizes</span>
              <span className="skc-muted">{sketch.unit === 'unknown' ? 'unit unknown until a plane sets it' : sketch.unit}</span>
            </div>
            <ul className="skc-list">
              {dims.map((c) => (
                <DimensionRow key={c.id} c={c} sketch={sketch} labels={labels} onConfirm={(v) => edit(confirmDimension(sketch, c.id, v))} />
              ))}
            </ul>
          </section>
        )}

        <div className="skc-tools" role="toolbar" aria-label="Editable sketch history">
          <button type="button" className="skc-icon" aria-label="Undo" title="Undo" disabled={doc.undo.length === 0} onClick={() => store.undo(projectId)}>
            <Undo2 size={14} aria-hidden="true" />
          </button>
          <button type="button" className="skc-icon" aria-label="Redo" title="Redo" disabled={doc.redo.length === 0} onClick={() => store.redo(projectId)}>
            <Redo2 size={14} aria-hidden="true" />
          </button>
          <button
            type="button"
            className={`skc-icon ${doc.compare ? 'is-on' : ''}`}
            aria-pressed={doc.compare}
            aria-label="Compare with the first fit"
            title="Compare with the first fit"
            onClick={() => store.patch(projectId, { compare: !doc.compare })}
          >
            <Columns2 size={14} aria-hidden="true" />
          </button>
          <button type="button" className="skc-icon" aria-label="Restore the last saved version" title="Restore the last saved version" disabled={!doc.lastSaved || !doc.dirty} onClick={() => store.restore(projectId)}>
            <RotateCcw size={14} aria-hidden="true" />
          </button>
          <button
            type="button"
            className={`skc-icon ${doc.detail ? 'is-on' : ''}`}
            aria-pressed={doc.detail}
            aria-label="Engineering detail"
            title="Engineering detail"
            onClick={() => store.patch(projectId, { detail: !doc.detail })}
          >
            <Info size={14} aria-hidden="true" />
          </button>
          <span className="skc-muted skc-saved">{doc.busy === 'saving' ? 'Saving…' : doc.savedRef ? (doc.dirty ? 'Unsaved changes' : `Saved · ${doc.savedRef}`) : ''}</span>
        </div>

        {doc.detail && <Detail sketch={sketch} labels={labels} />}

        {doc.error && (
          <p className="skc-note is-bad" role="alert">
            {doc.error}
          </p>
        )}

        <footer className="skc-foot">
          {!doc.promotion ? (
            <button
              type="button"
              className="skc-btn skc-primary"
              disabled={busy || stale || accepted.length === 0 || solve?.status === 'over'}
              title={accepted.length === 0 ? 'Accept at least one rule or size first' : solve?.status === 'over' ? 'Fix the conflict first' : undefined}
              onClick={() => void promote()}
              data-testid="sketch-promote"
            >
              {doc.busy === 'promoting' ? 'Making it…' : 'Make it a parametric sketch'}
            </button>
          ) : doc.promotion.approved ? (
            <p className="skc-status is-good" data-testid="sketch-promoted">
              Approved · {doc.promotion.itemRef}. Depth and material are still needed before it becomes a part.
            </p>
          ) : (
            <div className="skc-row">
              <span>Waiting for your sign-off · {doc.promotion.itemRef}</span>
              <button type="button" className="skc-btn skc-primary" disabled={busy} onClick={() => void approve()} data-testid="sketch-approve">
                Approve
              </button>
            </div>
          )}
        </footer>
      </aside>
    </>
  );
}

function DimensionRow({
  c,
  sketch,
  labels,
  onConfirm,
}: {
  c: SketchConstraint;
  sketch: CompiledSketch;
  labels: Map<string, string>;
  onConfirm: (value: number) => void;
}) {
  const shown = c.source === 'human_confirmed' ? c.value : (measured(sketch, c) ?? c.value);
  const [draft, setDraft] = useState<string | null>(null);
  const name = constraintLabel({ ...c, value: shown }, labels).replace(/ = .*$/, '');
  const commit = () => {
    if (draft === null) return;
    const v = Number(draft);
    setDraft(null);
    if (Number.isFinite(v) && v > 0 && v !== shown) onConfirm(v);
  };
  const onKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') commit();
    if (e.key === 'Escape') setDraft(null);
  };
  const confirmed = c.source === 'human_confirmed';
  return (
    <li className={confirmed ? 'is-confirmed' : ''}>
      <label>
        <span>{name}</span>
        <input
          type="number"
          inputMode="decimal"
          min={0}
          step="any"
          aria-label={name}
          value={draft ?? formatValue(shown)}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={onKey}
        />
      </label>
      <span className={`skc-tag ${confirmed ? 'is-confirmed' : ''}`}>{confirmed ? 'confirmed' : 'estimated'}</span>
    </li>
  );
}

function Detail({ sketch, labels }: { sketch: CompiledSketch; labels: Map<string, string> }) {
  const solve = sketch.solve;
  return (
    <section className="skc-section skc-detail" aria-label="Engineering detail" data-testid="sketch-compiler-detail">
      {solve && (
        <dl>
          <dt>Degrees of freedom</dt>
          <dd>
            {solve.dof} ({solve.shape_dof} shape and size)
          </dd>
          <dt>Solve</dt>
          <dd>
            {solve.status}, {solve.converged ? 'converged' : 'not converged'} in {solve.iterations} steps, {solve.duration_ms} ms
          </dd>
          <dt>Largest residual</dt>
          <dd>{solve.max_residual.toExponential(1)}</dd>
          <dt>Fit</dt>
          <dd>
            {sketch.fit.duration_ms} ms, tolerance {formatValue(sketch.fit.tolerance)} sketch units
          </dd>
          {solve.redundant.length > 0 && (
            <>
              <dt>Already implied</dt>
              <dd>{solve.redundant.length}</dd>
            </>
          )}
        </dl>
      )}
      <table>
        <thead>
          <tr>
            <th>Rule</th>
            <th>Status</th>
            <th>Source</th>
            <th>Residual</th>
          </tr>
        </thead>
        <tbody>
          {sketch.constraints.map((c) => (
            <tr key={c.id}>
              <td>{constraintLabel(c, labels)}</td>
              <td>{c.status}</td>
              <td>{c.source}</td>
              <td>{solve?.residuals[c.id] !== undefined ? solve.residuals[c.id]!.toExponential(1) : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <table>
        <thead>
          <tr>
            <th>Shape</th>
            <th>Confidence</th>
            <th>Fit residual</th>
          </tr>
        </thead>
        <tbody>
          {sketch.primitives.map((p) => (
            <tr key={p.id}>
              <td>{labels.get(p.id)}</td>
              <td>{Math.round(p.confidence * 100)}%</td>
              <td>{p.residual.toExponential(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function shapePath(p: Primitive, sketch: CompiledSketch): string {
  return pathD(primitivePolyline(p).map((q) => toDrawn(sketch.frame, q)));
}

function Overlay({
  sketch,
  original,
  view,
  labels,
  onToggle,
}: {
  sketch: CompiledSketch;
  original: CompiledSketch | null;
  view: Viewport;
  labels: Map<string, string>;
  onToggle: (c: SketchConstraint) => void;
}) {
  const badges = sketch.constraints.filter((c) => !DIMENSION_TYPES.includes(c.type) && c.status !== 'rejected');
  const seen = new Map<string, number>();
  return (
    <div className="skc-overlay" data-testid="sketch-compiler-overlay">
      <svg className="skc-svg" viewBox={viewBoxFor(view)} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
        {original &&
          original.primitives
            .filter((p) => !p.construction)
            .map((p) => <path key={`o-${p.id}`} d={shapePath(p, original)} className="skc-ghost" vectorEffect="non-scaling-stroke" />)}
        {sketch.primitives.map((p) => (
          <path
            key={p.id}
            d={shapePath(p, sketch)}
            data-primitive={p.id}
            className={`skc-shape${p.construction ? ' is-construction' : isConfident(p) ? '' : ' is-unsure'}`}
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
      {badges.map((c) => {
        const at = constraintAnchor(c, sketch.primitives);
        if (!at) return null;
        const [x, y] = toScreen(toDrawn(sketch.frame, at), view);
        const k = `${Math.round(x / 6)}:${Math.round(y / 6)}`;
        const n = seen.get(k) ?? 0;
        seen.set(k, n + 1);
        const label = constraintLabel(c, labels);
        return (
          <button
            key={c.id}
            type="button"
            className={`skc-badge is-${c.status}${isConfident(c) ? '' : ' is-unsure'}`}
            style={{ left: x + 8 + n * 16, top: y - 8 }}
            aria-pressed={c.status === 'accepted'}
            aria-label={`${c.status === 'accepted' ? 'Accepted' : 'Proposed'}: ${label}`}
            title={`${label} (${Math.round(c.confidence * 100)}% sure). Click to ${c.status === 'accepted' ? 'undo accepting' : 'accept'}.`}
            onClick={() => onToggle(c)}
          >
            {CONSTRAINT_GLYPH[c.type]}
          </button>
        );
      })}
    </div>
  );
}
