import { useRef } from 'react';
import { ChevronLeft, ChevronRight, Paperclip, X } from 'lucide-react';
import { referenceRecord } from '../../api/endpoints/create';
import { needsReattachment } from '../../api/endpoints/sketchPlanes';
import { useCreation } from '../../hooks/use-creation';
import { useSketchPlane } from '../../hooks/use-sketch-plane';
import { useCreateStore } from '../../store/create-store';
import { DRAWER_LABEL, useCreateLayoutStore, type DrawerPanel } from '../../store/create-layout-store';
import { useSketchStore } from '../../store/sketch-store';
import { GeneratePanel } from '../sketch/GeneratePanel';
import { PlanePrompt } from '../sketch/PlanePrompt';
import { ReattachChoices } from '../sketch/ReattachChoices';
import { ReferenceChip } from './ReferenceChip';

interface StageDrawerProps {
  projectId: string;
  panel: DrawerPanel;
  collapsed: boolean;
  /** The editable sketch renders itself into this element (a portal from the sketch layer). */
  onEditableHost: (el: HTMLElement | null) => void;
}

/**
 * The stage's one contextual drawer: the editable sketch, Generate, the plane
 * choice or the references, one at a time. It sits beside the drawing, never
 * over the stage's own controls, and it can be collapsed to a slim tab or
 * closed. The panels inside are the existing ones.
 */
export function StageDrawer({ projectId, panel, collapsed, onEditableHost }: StageDrawerProps) {
  const close = useCreateLayoutStore((s) => s.closeDrawer);
  const setCollapsed = useCreateLayoutStore((s) => s.setDrawerCollapsed);
  const label = DRAWER_LABEL[panel];

  if (collapsed) {
    return (
      <aside className="cv-drawer is-collapsed" aria-label={label} data-testid="stage-drawer" data-panel={panel}>
        <button
          type="button"
          className="cv-drawer-tab"
          aria-label={`Expand ${label}`}
          aria-expanded={false}
          title={`Expand ${label}`}
          onClick={() => setCollapsed(projectId, false)}
        >
          <ChevronLeft size={14} aria-hidden="true" />
          <span>{label}</span>
        </button>
      </aside>
    );
  }

  return (
    <aside className="cv-drawer" aria-label={label} data-testid="stage-drawer" data-panel={panel}>
      <header className="cv-drawer-head">
        <strong>{label}</strong>
        <button
          type="button"
          className="cv-icon-btn"
          aria-label={`Collapse ${label}`}
          aria-expanded={true}
          title="Collapse"
          onClick={() => setCollapsed(projectId, true)}
        >
          <ChevronRight size={14} />
        </button>
        <button type="button" className="cv-icon-btn" aria-label={`Close ${label}`} title="Close" onClick={() => close(projectId)}>
          <X size={14} />
        </button>
      </header>
      <div className="cv-drawer-body">
        {panel === 'editable' ? (
          <div ref={onEditableHost} className="cv-drawer-host" data-testid="drawer-editable-host" />
        ) : panel === 'generate' ? (
          <DrawerGenerate projectId={projectId} />
        ) : panel === 'plane' ? (
          <DrawerPlane projectId={projectId} onDone={() => close(projectId)} />
        ) : (
          <DrawerReferences projectId={projectId} />
        )}
      </div>
    </aside>
  );
}

function DrawerGenerate({ projectId }: { projectId: string }) {
  const doc = useSketchStore((s) => s.docs[projectId]);
  const strokes = doc?.strokes.length ?? 0;
  return (
    <GeneratePanel
      projectId={projectId}
      docked
      sketchKey={strokes > 0 ? (doc?.itemKey ?? null) : null}
      sketchRevision={doc?.savedRevision ?? null}
      sketchUnsaved={!!doc?.pendingSaveId}
    />
  );
}

function DrawerPlane({ projectId, onDone }: { projectId: string; onDone: () => void }) {
  const sketchKey = useSketchStore((s) => s.docs[projectId]?.itemKey ?? null);
  const plane = useSketchPlane(projectId, sketchKey).data?.plane ?? null;
  if (!sketchKey) return <p className="cv-muted">Save a sketch first; then choose the plane it sits on.</p>;
  if (plane && needsReattachment(plane)) {
    return <ReattachChoices projectId={projectId} sketchKey={sketchKey} plane={plane} onClose={onDone} />;
  }
  return <PlanePrompt projectId={projectId} sketchKey={sketchKey} onDone={onDone} inline />;
}

function DrawerReferences({ projectId }: { projectId: string }) {
  const brief = useCreation(projectId).data?.creation.brief;
  const attach = useCreateStore((s) => s.attach);
  const input = useRef<HTMLInputElement>(null);
  const ids = brief?.references ?? [];
  return (
    <div className="cv-drawer-refs">
      <p className="cv-muted">Photos, drawings and documents that inform the design. Each one says what it is for.</p>
      {ids.length > 0 ? (
        <div className="cv-drawer-ref-list" aria-label="References">
          {ids.map((id) => (
            <ReferenceChip key={id} projectId={projectId} record={referenceRecord(brief, id)} />
          ))}
        </div>
      ) : (
        <p className="cv-muted">No references yet.</p>
      )}
      <button type="button" className="cv-btn" onClick={() => input.current?.click()}>
        <Paperclip size={14} aria-hidden="true" /> Add a reference
      </button>
      <input
        ref={input}
        type="file"
        multiple
        hidden
        data-testid="drawer-reference-input"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length) attach(projectId, files);
          e.target.value = '';
        }}
      />
    </div>
  );
}
