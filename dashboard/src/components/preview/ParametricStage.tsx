import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Loader2, RotateCcw } from 'lucide-react';
import { getNodeModel } from '../../api/endpoints/twin';
import { resolveGatewayHref } from '../../lib/gatewayConfig';
import { amountOf, type ComparisonRecord } from '../../lib/preview/definition';
import type { PreviewHandle } from '../../lib/preview/buildPreview';
import type { CommitExtra, ParametricPreviewState } from '../../hooks/use-parametric-preview';
import { ParametricCanvas } from './ParametricCanvas';

/**
 * A parametric component on the stage (C3D slice 5), in Create and in
 * Engineering: the L2 preview drawn from the shared definition, the swap to
 * the validated L3 model, the failure state, Strategy A drag handles and
 * their keyboard fallback (a numeric field per supported parameter).
 */
interface ParametricStageProps {
  pp: ParametricPreviewState;
  variant: 'create' | 'engineering';
}

function fmt(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
}

function NumberField({
  id,
  label,
  value,
  onChange,
  onCommit,
  min = 0.01,
  max,
}: {
  id: string;
  label: string;
  value: number;
  onChange: (v: number) => void;
  onCommit: () => void;
  min?: number;
  max?: number;
}) {
  const [text, setText] = useState(fmt(value));
  useEffect(() => setText(fmt(value)), [value]);
  return (
    <label className="cv-param-field" htmlFor={id}>
      <span>{label}</span>
      <input
        id={id}
        type="number"
        inputMode="decimal"
        step="0.1"
        min={min}
        max={max}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          const v = Number(e.target.value);
          if (Number.isFinite(v) && v >= min && (max === undefined || v <= max)) onChange(v);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            onCommit();
          }
        }}
      />
    </label>
  );
}

/** The comparison values Engineering shows (section 11: preview-versus-exact correspondence). */
export function ComparisonTable({ comparison }: { comparison: ComparisonRecord | null }) {
  if (!comparison) {
    return (
      <p className="cv-param-muted" data-testid="comparison-none">
        No preview versus exact comparison yet: it is recorded when a validated exact revision is built from this definition.
      </p>
    );
  }
  return (
    <div className="cv-param-comparison" data-testid="comparison-table">
      <div className="cv-param-heading">
        Preview versus exact · {comparison.outcome}
        {comparison.item_ref ? ` · ${comparison.item_ref}` : ''}
      </div>
      <table>
        <tbody>
          {comparison.checks.map((c) => (
            <tr key={c.check} className={`is-${c.outcome}`}>
              <td>{c.check.replace('_', ' ')}</td>
              <td>{c.outcome === 'fail' ? 'discrepancy' : c.outcome === 'pass' ? 'within tolerance' : 'not evaluated'}</td>
              <td>{c.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="cv-param-muted">
        Tolerance {fmt(comparison.tolerance.linear_mm)} mm ({comparison.tolerance.source}): {comparison.tolerance.basis}
      </p>
    </div>
  );
}

export function ParametricStage({ pp, variant }: ParametricStageProps) {
  const def = pp.definition;
  const display = pp.display;
  const [material, setMaterial] = useState('');
  const l3NodeId = display?.l3NodeId ?? null;
  const l3 = useQuery({
    queryKey: ['parametric-l3-model', l3NodeId],
    queryFn: async () => resolveGatewayHref((await getNodeModel(l3NodeId!)).glb_url),
    enabled: !!l3NodeId,
    staleTime: Infinity,
    retry: false,
  });

  if (!def || !display) {
    return (
      <div className="cv-stage-empty" data-testid="parametric-loading">
        {pp.unavailable ?? (pp.loading ? 'Loading the editable part…' : 'Nothing to preview yet.')}
      </div>
    );
  }

  const amountName = def.op === 'extrude' ? 'depth' : 'angle';
  const amount = pp.overrides.amount ?? amountOf(def);
  const unit = def.op === 'extrude' ? (def.unit === 'unknown' ? 'units' : def.unit) : 'deg';
  const needsMaterial = def.missing.includes('material');
  const onHandleChange = (h: PreviewHandle, v: number) => {
    if (h.kind === 'extrude_depth' || h.kind === 'revolve_angle') pp.setAmount(v);
    else if (h.kind === 'length' && h.constraintId) pp.setLength(h.constraintId, v);
    else if (h.constraintId) pp.setRadius(h.constraintId, v);
  };
  const onHandleRelease = (h: PreviewHandle, v: number) => {
    const extra: CommitExtra =
      h.kind === 'extrude_depth' || h.kind === 'revolve_angle'
        ? { amount: v }
        : h.kind === 'length'
          ? { length: { [h.constraintId!]: v } }
          : { radius: { [h.constraintId!]: v } };
    void pp.commit('drag', { ...extra, material: material.trim() || undefined });
  };
  const apply = () => void pp.commit('numeric', { material: material.trim() || undefined });

  return (
    <div className="cv-param" data-testid="parametric-stage" data-show={display.show} data-variant={variant}>
      <div className="cv-param-canvas">
        <ParametricCanvas
          build={pp.build}
          show={display.show}
          l3Url={l3.data ?? null}
          scaleToMm={def.scale_to_mm}
          onHandleChange={onHandleChange}
          onHandleRelease={onHandleRelease}
          handlesEnabled={!pp.committing}
        />
        <div className={`cv-param-status ${display.attention ? 'is-attention' : ''}`} role="status" data-testid="parametric-status">
          {display.attention ? (
            <AlertTriangle size={14} aria-hidden="true" />
          ) : display.pending ? (
            <Loader2 size={14} className="cv-spin" aria-hidden="true" />
          ) : null}
          <span data-testid="parametric-status-line">{display.status}</span>
          {display.secondary && <span className="cv-param-secondary">{display.secondary}</span>}
          {display.attention && display.retryOperation && (
            <button type="button" className="cv-chip-btn" onClick={() => void pp.retry()} disabled={pp.retrying}>
              <RotateCcw size={13} aria-hidden="true" /> Retry
            </button>
          )}
        </div>
        {pp.conflict && (
          <p className="cv-param-conflict" role="alert" data-testid="parametric-conflict">
            {pp.conflict}
          </p>
        )}
        {(display.notes.length > 0 || pp.buildError) && (
          <ul className="cv-param-notes" data-testid="parametric-notes">
            {pp.buildError && <li>{pp.buildError}</li>}
            {display.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}
      </div>
      <form
        className="cv-param-controls"
        aria-label="Change the part"
        onSubmit={(e) => {
          e.preventDefault();
          apply();
        }}
      >
        <NumberField
          id={`param-${def.sketch_key}-${amountName}`}
          label={`${amountName === 'depth' ? 'Depth' : 'Angle'} (${unit})`}
          value={amount}
          max={def.op === 'revolve' ? 360 : undefined}
          onChange={pp.setAmount}
          onCommit={apply}
        />
        {def.dimensions.map((d) => (
          <NumberField
            key={d.constraint_id}
            id={`param-${def.sketch_key}-${d.constraint_id}`}
            label={`${d.type === 'length' ? 'Length' : 'Radius'} ${d.primitive_id} (${d.unit === 'unknown' ? 'units' : d.unit})`}
            value={(d.type === 'length' ? pp.overrides.length : pp.overrides.radius)?.[d.constraint_id] ?? d.value}
            onChange={(v) => (d.type === 'length' ? pp.setLength(d.constraint_id, v) : pp.setRadius(d.constraint_id, v))}
            onCommit={apply}
          />
        ))}
        {needsMaterial && (
          <label className="cv-param-field">
            <span>Material</span>
            <input type="text" value={material} placeholder="e.g. PLA" onChange={(e) => setMaterial(e.target.value)} />
          </label>
        )}
        <button type="submit" className="cv-primary" disabled={pp.committing} data-testid="parametric-apply">
          {pp.committing ? 'Saving…' : 'Apply'}
        </button>
        {variant === 'engineering' && pp.buildMs !== null && (
          <span className="cv-param-muted" data-testid="parametric-build-ms">
            Preview rebuilt in {pp.buildMs.toFixed(1)} ms · {pp.build?.triangles ?? 0} triangles
          </span>
        )}
      </form>
      {variant === 'engineering' && (
        <div className="cv-param-engineering">
          <p className="cv-param-muted">
            {def.sketch_ref} · {def.feature_id} · L2 preview from the shared definition
            {def.cad.exists ? ` · exact ${def.cad.item_key}@${def.cad.head_revision} (${def.cad.geometry_state})` : ' · no exact model yet'}
          </p>
          <ComparisonTable comparison={def.cad.comparison} />
        </div>
      )}
    </div>
  );
}
