import { useMemo } from 'react';
import { Box, Image as ImageIcon, Loader2, MessageSquare, Sparkles } from 'lucide-react';
import { PreviewHost } from '../preview/PreviewHost';
import { previewEngineFor, is3dEngine } from '../preview/registry';
import { useNodeVersionHistory } from '../../hooks/use-twin';
import type { TwinNode } from '../../types/twin';

/** Dimensionally defined outputs. Everything else visual is a concept. */
const ENGINEERING_TYPES = new Set(['cad_model', 'pcb_layout']);
const CONCEPT_TYPES = new Set(['design_sketch', 'image']);

/** The work product type (cad_model, image, …); `node.type` is only the graph node kind. */
export function wpTypeOf(node: TwinNode): string {
  return String(node.properties?.wp_type ?? '');
}

export function isVisual(node: TwinNode): boolean {
  const t = wpTypeOf(node);
  if (ENGINEERING_TYPES.has(t) || CONCEPT_TYPES.has(t)) return true;
  const { engine } = previewEngineFor(node);
  return is3dEngine(engine) || engine === 'image';
}

export function isEngineering(node: TwinNode): boolean {
  return ENGINEERING_TYPES.has(wpTypeOf(node));
}

/** The newest dimensioned model if there is one, else the newest concept. */
export function defaultStageNode(nodes: TwinNode[]): TwinNode | undefined {
  const visual = nodes.filter(isVisual).sort((a, b) => (b.updatedAt ?? '').localeCompare(a.updatedAt ?? ''));
  return visual.find(isEngineering) ?? visual[0];
}

interface ProductStageProps {
  nodes: TwinNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDiscuss: (node: TwinNode) => void;
  /** A pass is running: keep showing the last valid result, and say so. */
  working: boolean;
  presenting?: boolean;
}

export function ProductStage({ nodes, selectedId, onSelect, onDiscuss, working, presenting }: ProductStageProps) {
  const visual = useMemo(
    () => nodes.filter(isVisual).sort((a, b) => (b.updatedAt ?? '').localeCompare(a.updatedAt ?? '')),
    [nodes],
  );
  const node = visual.find((n) => n.id === selectedId) ?? defaultStageNode(visual);
  const history = useNodeVersionHistory(node?.id);
  const revision = history.data?.length ? Math.max(...history.data.map((r) => r.revision)) : null;
  const engineering = node ? isEngineering(node) : false;

  return (
    <section className={`cv-stage ${presenting ? 'is-presenting' : ''}`} aria-label="Product">
      {node ? (
        <>
          <div className="cv-stage-badges">
            <span className={`cv-truth ${engineering ? 'is-engineering' : 'is-concept'}`}>
              {engineering ? <Box size={13} aria-hidden="true" /> : <ImageIcon size={13} aria-hidden="true" />}
              {engineering ? 'Engineering model · dimensioned' : 'Concept · illustrative, not a manufacturable model'}
            </span>
            <span className="cv-revision" title="The revision on screen">
              {node.name}
              {revision !== null ? ` · revision ${revision}` : ''}
            </span>
          </div>
          <div className="cv-stage-canvas">
            <PreviewHost key={node.id} node={node} mode="modal" />
          </div>
          {working && (
            <div className="cv-stage-working" role="status">
              <Loader2 size={14} className="cv-spin" aria-hidden="true" />
              Working on the next version. This is the latest finished one.
            </div>
          )}
          <div className="cv-stage-actions">
            <button type="button" className="cv-chip-btn" onClick={() => onDiscuss(node)}>
              <MessageSquare size={14} /> Discuss this
            </button>
          </div>
          {visual.length > 1 && (
            <ol className="cv-versions" aria-label="Earlier results">
              {visual.map((n) => (
                <li key={n.id}>
                  <button
                    type="button"
                    className={`cv-version ${n.id === node.id ? 'is-on' : ''}`}
                    aria-pressed={n.id === node.id}
                    onClick={() => onSelect(n.id)}
                    title={`${n.name} · ${isEngineering(n) ? 'engineering model' : 'concept'}`}
                  >
                    {isEngineering(n) ? <Box size={14} aria-hidden="true" /> : <ImageIcon size={14} aria-hidden="true" />}
                    <span>{n.name}</span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </>
      ) : (
        <div className="cv-stage-empty">
          <div className="cv-orbit" aria-hidden="true">
            <Sparkles size={34} />
          </div>
          <h2>{working ? 'Designing…' : 'Your product will appear here'}</h2>
          <p>
            {working
              ? 'The first result shows here as soon as it exists.'
              : 'Describe it in the conversation. Sketches and photos you attach help.'}
          </p>
        </div>
      )}
    </section>
  );
}
