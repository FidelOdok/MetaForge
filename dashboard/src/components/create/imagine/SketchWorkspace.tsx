import { useMemo, useState } from 'react';
import { Paperclip, Sparkles } from 'lucide-react';
import { SketchLayer } from '../../sketch/SketchLayer';
import { previewEngineFor } from '../../preview/registry';
import { EMPTY_STAGE, useCreateLayoutStore } from '../../../store/create-layout-store';
import { StageDrawer } from '../StageDrawer';
import type { TwinNode } from '../../../types/twin';

/**
 * Sketching, inside Imagine: the drawing paper on its own (no 3D view under
 * it), with Generate, the references, the plane and the editable sketch
 * opening one at a time in the drawer beside it.
 */
export function SketchWorkspace({ projectId, nodes }: { projectId: string; nodes: TwinNode[] }) {
  const layout = useCreateLayoutStore((s) => s.stages[projectId]) ?? EMPTY_STAGE;
  const openDrawer = useCreateLayoutStore((s) => s.openDrawer);
  const [editableHost, setEditableHost] = useState<HTMLElement | null>(null);
  const references = useMemo(() => nodes.filter((n) => previewEngineFor(n).engine === 'image'), [nodes]);

  return (
    <div className="cv-sketch" data-testid="sketch-workspace">
      <div className="cv-sketch-tools" role="group" aria-label="Sketch tools">
        <button type="button" className="cv-chip-btn" onClick={() => openDrawer(projectId, 'generate')}>
          <Sparkles size={14} aria-hidden="true" /> Generate a concept
        </button>
        <button type="button" className="cv-chip-btn" onClick={() => openDrawer(projectId, 'references')}>
          <Paperclip size={14} aria-hidden="true" /> References
        </button>
      </div>
      <div className={`cv-stage-body ${layout.drawer ? 'has-drawer' : ''}`}>
        <div className="cv-paper" data-testid="sketch-paper">
          <SketchLayer
            projectId={projectId}
            referenceNodes={references}
            onRequestPanel={(p) => openDrawer(projectId, p)}
            editableHost={layout.drawer === 'editable' && !layout.drawerCollapsed ? editableHost : null}
          />
        </div>
        {layout.drawer && (
          <StageDrawer projectId={projectId} panel={layout.drawer} collapsed={layout.drawerCollapsed} onEditableHost={setEditableHost} />
        )}
      </div>
    </div>
  );
}
