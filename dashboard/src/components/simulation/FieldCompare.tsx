import { lazy, Suspense, useMemo, useState } from 'react';
import { useSimulationField } from '../../hooks/use-simulation-results';
import type { FieldQuantity } from '../../types/simulationField';
import type { SimulationResult } from '../../types/simulationResult';
import { createCameraSync } from './cameraSync';
import { availableQuantities, autoDeformScale, sharedRange } from './fieldMath';
import { FIELD_NOT_STORED_TEXT, FieldNote } from './SimFieldPanel';

const FieldViewer = lazy(() => import('./FieldViewer'));

const MUTED = 'var(--mf-c-9a9aaa)';

/**
 * FORGE-532: two results side by side in 3D. One camera (orbiting either
 * viewer moves both), one quantity, one deformation scale and ONE colour
 * range spanning both results, so the same colour means the same value on
 * each side and a real change reads as a colour change.
 */
export function FieldCompare({ a, b }: { a: SimulationResult; b: SimulationResult }) {
  const fa = useSimulationField(a.id, a.hasField !== false);
  const fb = useSimulationField(b.id, b.hasField !== false);
  const sync = useMemo(() => createCameraSync(), []);
  const [quantity, setQuantity] = useState<FieldQuantity | null>(null);
  const [scale, setScale] = useState<number | null>(null);

  const pa = fa.data?.status === 'ok' ? fa.data.payload : null;
  const pb = fb.data?.status === 'ok' ? fb.data.payload : null;

  if (a.hasField === false || b.hasField === false || fa.data?.status === 'not_stored' || fb.data?.status === 'not_stored') {
    const missing = [a, b].filter(
      (r, i) => r.hasField === false || [fa, fb][i]?.data?.status === 'not_stored',
    );
    return (
      <div className="mt-3">
        <FieldNote testId="compare-field-not-stored">
          3D compare needs both fields. {missing.map((r) => r.name).join(' and ')}: {FIELD_NOT_STORED_TEXT}
        </FieldNote>
      </div>
    );
  }
  if (!pa || !pb) {
    return (
      <div data-testid="compare-field-loading" className="mt-3 grid grid-cols-2 gap-3">
        <div className="animate-pulse rounded-lg" style={{ height: 320, background: 'rgba(40,42,48,0.4)' }} />
        <div className="animate-pulse rounded-lg" style={{ height: 320, background: 'rgba(40,42,48,0.4)' }} />
      </div>
    );
  }

  const common = availableQuantities(pa).filter((q) => pb.fields[q] !== undefined);
  const active = quantity && common.includes(quantity) ? quantity : common[0];
  if (!active) {
    return (
      <div className="mt-3">
        <FieldNote testId="compare-field-mismatch">
          These two results share no field quantity (for example a thermal and a stress run), so there is nothing to compare in 3D.
        </FieldNote>
      </div>
    );
  }
  const range = sharedRange(pa, pb, active) ?? undefined;
  const deform = scale ?? Math.min(autoDeformScale(pa) || Infinity, autoDeformScale(pb) || Infinity);
  const deformScale = Number.isFinite(deform) ? deform : 0;

  return (
    <div data-testid="field-compare" className="mt-3">
      <div className="mb-2 flex items-center gap-3">
        <label className="font-mono" style={{ fontSize: 11, color: MUTED }}>
          Field{' '}
          <select
            aria-label="Compare quantity"
            value={active}
            onChange={(e) => setQuantity(e.target.value as FieldQuantity)}
            className="font-mono"
            style={{ fontSize: 11, background: '#1e1f26', color: 'var(--mf-c-e2e2eb)', border: '1px solid rgba(65,72,90,0.3)', borderRadius: 4 }}
          >
            {common.map((q) => (
              <option key={q} value={q}>
                {pa.fields[q]?.label ?? q}
              </option>
            ))}
          </select>
        </label>
        <span className="font-mono" style={{ fontSize: 10, color: MUTED }}>
          Shared colour scale and camera
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Suspense fallback={<div style={{ height: 320 }} />}>
          <FieldViewer
            payload={pa}
            quantity={active}
            range={range}
            deformScale={deformScale}
            onDeformScaleChange={setScale}
            cameraSync={sync}
            syncId="compare-a"
            height={320}
            title={a.name}
            showControls
          />
          <FieldViewer
            payload={pb}
            quantity={active}
            range={range}
            deformScale={deformScale}
            onDeformScaleChange={setScale}
            cameraSync={sync}
            syncId="compare-b"
            height={320}
            title={b.name}
            showControls
          />
        </Suspense>
      </div>
    </div>
  );
}
