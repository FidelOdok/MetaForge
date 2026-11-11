import { MessageSquare } from 'lucide-react';
import { engineHref } from '../../../api/endpoints/imagine';
import { refusalOf } from '../../../api/endpoints/create';
import { useStudio } from '../../../hooks/use-imagine';
import type { TwinNode } from '../../../types/twin';
import { HandoverPanel } from './HandoverPanel';
import { SketchWorkspace } from './SketchWorkspace';
import { StudioFrame } from './StudioFrame';

export type ImagineView = 'studio' | 'sketch';

const EMPTY: Record<string, unknown> = {};

interface ImagineStageProps {
  projectId: string;
  nodes: TwinNode[];
  view: ImagineView;
  onView: (view: ImagineView) => void;
  /** Opens the conversation with a request to start the studio. */
  onAskAssistant?: () => void;
  /** After handover: send a design turn in Develop (FORGE-824). */
  onStartDeveloping?: () => void;
}

/**
 * Imagine: intent and concept capture. The studio the assistant made for
 * this product, or the sketch paper, then the handover to engineering.
 * Checks here are screening only; Develop, Check and Make follow the twin.
 */
export function ImagineStage({ projectId, nodes, view, onView, onAskAssistant, onStartDeveloping }: ImagineStageProps) {
  const studio = useStudio(projectId);
  const data = studio.data;
  // The frame puts the revision in the address when a new one must load as a new page;
  // a change to the form alone is pushed into the running page instead.
  const src = data ? engineHref(data) : null;
  const hasStudio = !!data && data.status !== 'none' && !!src;

  return (
    <div className={`cv-stage-col cv-imagine${view === 'studio' && hasStudio ? ' is-wide' : ''}`} data-testid="imagine-stage">
      <div className="cv-seg" role="tablist" aria-label="Imagine">
        <button type="button" role="tab" aria-selected={view === 'studio'} className={view === 'studio' ? 'is-on' : ''} onClick={() => onView('studio')}>
          Studio
        </button>
        <button type="button" role="tab" aria-selected={view === 'sketch'} className={view === 'sketch' ? 'is-on' : ''} onClick={() => onView('sketch')}>
          Sketch
        </button>
      </div>

      {view === 'sketch' ? (
        <SketchWorkspace projectId={projectId} nodes={nodes} />
      ) : studio.isLoading ? (
        <p className="cv-muted">Loading the studio…</p>
      ) : hasStudio ? (
        <StudioFrame
          projectId={projectId}
          src={src}
          collections={data.collections ?? EMPTY}
          readOnly={data.status === 'handed_over'}
          revision={data.revision ?? 0}
          config={data.config}
        />
      ) : (
        <div className="cv-empty" data-testid="studio-empty">
          <h2>No studio yet</h2>
          <p className="cv-muted">
            {studio.isError
              ? `The studio could not be loaded: ${refusalOf(studio.error)}`
              : 'Ask the assistant to start the studio for this product. It captures what the product is for, the choices and the assumptions, ready for engineering.'}
          </p>
          {onAskAssistant && !studio.isError && (
            <button type="button" className="cv-primary" onClick={onAskAssistant}>
              <MessageSquare size={14} aria-hidden="true" /> Ask the assistant
            </button>
          )}
        </div>
      )}

      {data && data.status !== 'none' && <HandoverPanel projectId={projectId} studio={data} onStartDeveloping={onStartDeveloping} />}
    </div>
  );
}
