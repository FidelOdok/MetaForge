import { useParametricPreview } from '../../hooks/use-parametric-preview';
import { ParametricStage } from '../preview/ParametricStage';
import type { TwinNode } from '../../types/twin';

/**
 * Engineering's view of a parametric component (C3D slice 5): the same L2
 * preview, drag handles and numeric fields as Create, plus the preview
 * versus exact comparison values and the preview rebuild time. Shown for a
 * parametric sketch, and for the CAD item one builds into (`CAD-X` from
 * `PSK-X`); nothing is shown when there is no parametric definition.
 */
export function parametricSketchKeyOf(node: TwinNode): string | null {
  const wpType = String(node.properties?.wp_type ?? '');
  const key = String(node.properties?.item_key ?? '');
  if (!key) return null;
  if (wpType === 'parametric_sketch') return key;
  if (wpType === 'cad_model' && key.startsWith('CAD-')) return `PSK-${key.slice(4)}`;
  return null;
}

export function ParametricPreviewSection({ node, projectId }: { node: TwinNode; projectId: string | null }) {
  const sketchKey = parametricSketchKeyOf(node);
  const pp = useParametricPreview(projectId, sketchKey);
  if (!sketchKey || !projectId || pp.unavailable || (!pp.loading && !pp.definition)) return null;
  return (
    <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p1)' }} data-testid="parametric-section">
      <div className="font-mono uppercase mb-1.5" style={{ fontSize: 10, letterSpacing: '0.1em' }}>
        Parametric preview and exact correspondence
      </div>
      <div style={{ height: 420, background: '#0c0e14', borderRadius: 6, overflow: 'hidden' }}>
        <ParametricStage pp={pp} variant="engineering" />
      </div>
    </div>
  );
}
