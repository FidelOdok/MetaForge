import { useMemo } from 'react';
import { Loader2 } from 'lucide-react';
import { PreviewHost } from '../preview/PreviewHost';
import { previewEngineFor, is3dEngine } from '../preview/registry';
import { useNodeVersionHistory } from '../../hooks/use-twin';
import { ParametricStage } from '../preview/ParametricStage';
import { useParametricPreview } from '../../hooks/use-parametric-preview';
import {
  cadKeyForSketch,
  latestParametricSketch,
  levelText,
  preferredDisplayNode,
  representationOf,
  truthfulLabel,
} from './stage-logic';
import type { TwinNode } from '../../types/twin';

/** Dimensionally defined outputs. Everything else visual is a concept. */
const ENGINEERING_TYPES = new Set(['cad_model', 'pcb_layout']);
const CONCEPT_TYPES = new Set(['design_sketch', 'image']);

/** The work product type (cad_model, image, …); `node.type` is only the graph node kind. */
export function wpTypeOf(node: TwinNode): string {
  return String(node.properties?.wp_type ?? '');
}

/**
 * A person's own sketch lives in Imagine's sketch paper, never shown as a
 * result. A generated concept (FORGE-794) is shown there too.
 */
const SKETCH_TYPES = new Set(['sketch_strokes', 'generated_sketch']);

export function isVisual(node: TwinNode): boolean {
  const t = wpTypeOf(node);
  if (SKETCH_TYPES.has(t)) return false;
  if (ENGINEERING_TYPES.has(t) || CONCEPT_TYPES.has(t)) return true;
  const { engine } = previewEngineFor(node);
  return is3dEngine(engine) || engine === 'image';
}

export function isEngineering(node: TwinNode): boolean {
  return ENGINEERING_TYPES.has(wpTypeOf(node));
}

/**
 * The best real representation of the current revision (SYNC-010): validated
 * exact geometry (L3), then an exact model not yet validated (L2), simple 3D
 * parts (L1), an AI 3D concept (L0), then a picture; the newest of a level.
 */
export function defaultStageNode(nodes: TwinNode[]): TwinNode | undefined {
  return preferredDisplayNode(nodes.filter(isVisual));
}

/** The plain-language line for the engineering checks behind a preview (DEF-UI-001). */
export function engineeringChecksText(checks: 'running' | 'failed' | null | undefined): string | null {
  if (checks === 'running') return 'Preview updated · Engineering checks running';
  if (checks === 'failed') return 'Engineering checks found a problem · the last checked version is kept';
  return null;
}

interface ProductStageProps {
  projectId: string;
  nodes: TwinNode[];
  selectedId: string | null;
  /** A pass is running: keep showing the last valid result, and say so. */
  working: boolean;
  /** CDE slice 6: the exact geometry checks behind the preview, in plain words. */
  engineeringChecks?: 'running' | 'failed' | null;
}

/**
 * The twin's current model, read only: the 3D view with one label saying
 * how far it can be trusted (its maturity) and which representation it is
 * (its level and source). Edits go through the assistant or Imagine rework.
 */
export function ProductStage({ projectId, nodes, selectedId, working, engineeringChecks }: ProductStageProps) {
  const visual = useMemo(() => nodes.filter(isVisual), [nodes]);
  const node = visual.find((n) => n.id === selectedId) ?? defaultStageNode(visual);
  // C3D slice 5: a parametric component leads with its L2 preview, and swaps
  // to the validated exact model built from the same definition.
  const psk = useMemo(() => latestParametricSketch(nodes), [nodes]);
  const selectedItem = String(nodes.find((n) => n.id === selectedId)?.properties?.item_key ?? '');
  const parametricActive = !!psk && (!selectedId || selectedId === psk.nodeId || selectedItem === cadKeyForSketch(psk.key));
  const pp = useParametricPreview(parametricActive ? projectId : null, parametricActive ? psk!.key : null);
  const showParametric = parametricActive && !pp.unavailable;
  const paramDisplay = showParametric ? pp.display : null;
  const history = useNodeVersionHistory(node?.id);
  const revision = history.data?.length ? Math.max(...history.data.map((r) => r.revision)) : null;

  const rep = node ? representationOf(node, engineeringChecks) : null;
  const checksText = engineeringChecksText(engineeringChecks);

  let label: { maturity: string; text: string } | null = null;
  if (paramDisplay) {
    label = {
      maturity: paramDisplay.maturity,
      text:
        paramDisplay.show === 'l3'
          ? `L3 · from ${paramDisplay.l3Ref ?? 'the validated exact model'}`
          : `L2 · preview · from ${pp.definition?.sketch_ref ?? 'the parametric sketch'}`,
    };
  } else if (node && rep) {
    const parts = [
      truthfulLabel(rep.status, rep.maturity, 'Concept · illustrative'),
      levelText(node),
      `${node.name}${revision !== null ? ` rev ${revision}` : ''}`,
    ].filter(Boolean);
    label = { maturity: rep.maturity, text: parts.join(' · ') };
  }

  return (
    <section className="cv-twin-model" aria-label="Product">
      {label && (
        <p className="cv-twin-label" data-testid="stage-status">
          <span className={`cv-maturity is-${label.maturity.toLowerCase()}`} data-testid="stage-maturity" title="How far this representation can be trusted">
            {label.maturity}
          </span>
          <span data-testid="stage-level">{label.text}</span>
        </p>
      )}
      <div className="cv-stage-canvas" data-testid="stage-canvas">
        {showParametric ? (
          <ParametricStage pp={pp} variant="create" readOnly />
        ) : node ? (
          <PreviewHost key={node.id} node={node} mode="modal" />
        ) : (
          <div className="cv-stage-empty">
            <h2>{working ? 'Designing…' : 'No model yet'}</h2>
            <p>
              {working
                ? 'The first result shows here as soon as it exists.'
                : 'The model appears here once Imagine is handed over and engineering builds it.'}
            </p>
          </div>
        )}
        {checksText ? (
          <div className="cv-stage-working" role="status">
            {engineeringChecks === 'running' && <Loader2 size={14} className="cv-spin" aria-hidden="true" />}
            {checksText}
          </div>
        ) : (
          working &&
          node && (
            <div className="cv-stage-working" role="status">
              <Loader2 size={14} className="cv-spin" aria-hidden="true" />
              Working on the next version. This is the latest finished one.
            </div>
          )
        )}
      </div>
    </section>
  );
}
