import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Box,
  ChevronDown,
  Eye,
  Image as ImageIcon,
  Layers,
  Loader2,
  MessageSquare,
  Paperclip,
  PenLine,
  Pencil,
  Sparkles,
} from 'lucide-react';
import { PreviewHost } from '../preview/PreviewHost';
import { previewEngineFor, is3dEngine } from '../preview/registry';
import { useNodeVersionHistory } from '../../hooks/use-twin';
import { useConceptMesh } from '../../hooks/use-concept-mesh';
import { listGenerations } from '../../api/endpoints/sketchGeneration';
import { nodeFileUrl } from '../../api/endpoints/twin';
import { SketchLayer } from '../sketch/SketchLayer';
import { SketchOverlay } from '../sketch/SketchOverlay';
import { generationKeys } from '../sketch/GeneratePanel';
import { useSketchStore } from '../../store/sketch-store';
import { useSketchCompilerStore } from '../../store/sketch-compiler-store';
import { EMPTY_STAGE, useCreateLayoutStore, type DrawerPanel } from '../../store/create-layout-store';
import { StageDrawer } from './StageDrawer';
import {
  PRIMARY_LABEL,
  isConceptMesh,
  manufacturingNote,
  primaryActionFor,
  representationOf,
  truthfulLabel,
  type PrimaryAction,
  type StageFacts,
} from './stage-logic';
import type { TwinNode } from '../../types/twin';

/** Dimensionally defined outputs. Everything else visual is a concept. */
const ENGINEERING_TYPES = new Set(['cad_model', 'pcb_layout']);
const CONCEPT_TYPES = new Set(['design_sketch', 'image']);
/** A sketch that was made editable, or promoted to a parametric sketch. */
const EDITABLE_TYPES = new Set(['sketch_interpretation', 'parametric_sketch']);

/** The work product type (cad_model, image, …); `node.type` is only the graph node kind. */
export function wpTypeOf(node: TwinNode): string {
  return String(node.properties?.wp_type ?? '');
}

/**
 * A person's own sketch is drawn on the stage's sketch layer, never shown as a
 * result. A generated concept (FORGE-794) is shown there too, and only once
 * accepted: a proposal never takes the stage on its own.
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

/** The newest dimensioned model if there is one, else the newest concept. */
export function defaultStageNode(nodes: TwinNode[]): TwinNode | undefined {
  const visual = nodes.filter(isVisual).sort((a, b) => (b.updatedAt ?? '').localeCompare(a.updatedAt ?? ''));
  return visual.find(isEngineering) ?? visual[0];
}

interface ProductStageProps {
  /** With a project, the stage carries the sketch mode, the drawer and the next action. */
  projectId?: string;
  nodes: TwinNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDiscuss: (node: TwinNode) => void;
  /** A pass is running: keep showing the last valid result, and say so. */
  working: boolean;
  presenting?: boolean;
  /**
   * CDE slice 6: the exact geometry checks behind the preview. Said in plain
   * words only; no kernel or solver names.
   */
  engineeringChecks?: 'running' | 'failed' | null;
  /** What the page knows: the brief has content, references exist. */
  facts?: Partial<Pick<StageFacts, 'described' | 'hasReferences'>>;
  /** Describe: the conversation's composer takes focus. */
  onDescribe?: () => void;
  /** Build exact model: a design request in the composer, ready to send. */
  onBuildExact?: () => void;
  /** Prepare prototype: the Make panel. */
  onPrepare?: () => void;
}

/** The plain-language line for the engineering checks behind a preview (DEF-UI-001). */
export function engineeringChecksText(checks: 'running' | 'failed' | null | undefined): string | null {
  if (checks === 'running') return 'Preview updated · Engineering checks running';
  if (checks === 'failed') return 'Engineering checks found a problem · the last checked version is kept';
  return null;
}

/**
 * The Create product stage: the dominant artifact, its maturity, and the one
 * next action (16.2 items 3 and 5).
 *
 * **View** (the default) shows only the artifact, which can be turned and
 * zoomed. **Sketch** is entered on purpose and shows the drawing paper; the
 * 3D view is not mounted under it, or is a faint still image when *Trace
 * over the current view* is on, so the two are never both interactive.
 * Leaving Sketch keeps the sketch, and View can show it as a thin overlay.
 * The editable sketch, Generate, the plane choice and the references open
 * one at a time in a drawer beside the drawing.
 */
export function ProductStage({
  projectId,
  nodes,
  selectedId,
  onSelect,
  onDiscuss,
  working,
  presenting,
  engineeringChecks,
  facts,
  onDescribe,
  onBuildExact,
  onPrepare,
}: ProductStageProps) {
  const visual = useMemo(
    () => nodes.filter(isVisual).sort((a, b) => (b.updatedAt ?? '').localeCompare(a.updatedAt ?? '')),
    [nodes],
  );
  const node = visual.find((n) => n.id === selectedId) ?? defaultStageNode(visual);
  const history = useNodeVersionHistory(node?.id);
  const revision = history.data?.length ? Math.max(...history.data.map((r) => r.revision)) : null;
  const references = useMemo(() => visual.filter((n) => previewEngineFor(n).engine === 'image'), [visual]);

  const layout = useCreateLayoutStore((s) => (projectId ? s.stages[projectId] : undefined)) ?? EMPTY_STAGE;
  const { setMode, setTrace, setOverlay, openDrawer } = useCreateLayoutStore.getState();
  const sketchDoc = useSketchStore((s) => (projectId ? s.docs[projectId] : undefined));
  const compiled = useSketchCompilerStore((s) => (projectId ? s.docs[projectId]?.sketch : undefined));
  const generations = useQuery({
    queryKey: generationKeys.project(projectId ?? ''),
    queryFn: ({ signal }) => listGenerations(projectId!, signal),
    enabled: !!projectId,
  });
  const mesh = useConceptMesh(projectId);
  const [editableHost, setEditableHost] = useState<HTMLElement | null>(null);
  const [traceSrc, setTraceSrc] = useState<string | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);

  // A new 3D concept takes the stage once its job finishes.
  const latestMesh = mesh.state?.latest?.node_id ?? null;
  const seenMesh = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (seenMesh.current !== undefined && latestMesh && latestMesh !== seenMesh.current && nodes.some((n) => n.id === latestMesh)) {
      onSelect(latestMesh);
      seenMesh.current = latestMesh;
    } else if (seenMesh.current === undefined && mesh.state) {
      seenMesh.current = latestMesh;
    }
  }, [latestMesh, mesh.state, nodes, onSelect]);

  const hasSketch = (sketchDoc?.strokes.length ?? 0) > 0;
  const acceptedConcept = (generations.data?.lines ?? []).some((l) => !!l.head);
  const stageFacts: StageFacts = {
    described: !!facts?.described,
    hasReferences: !!facts?.hasReferences,
    hasConcept: acceptedConcept || nodes.some((n) => isVisual(n) && !isEngineering(n)),
    hasConcept3d: nodes.some(isConceptMesh) || !!mesh.state?.latest,
    hasSketch,
    hasEditable: !!compiled || nodes.some((n) => EDITABLE_TYPES.has(wpTypeOf(n))),
    hasEngineering: nodes.some(isEngineering),
  };
  const primary = projectId ? primaryActionFor(stageFacts) : null;
  const mode = projectId ? layout.mode : 'view';

  const rep = node ? representationOf(node, engineeringChecks) : null;
  const checksText = engineeringChecksText(engineeringChecks);
  const illustrative = rep && (rep.kind === 'concept' || rep.kind === 'concept3d');

  /** Enter Sketch: keep a still of the view for tracing, then unmount the viewer. */
  const enterSketch = () => {
    if (!projectId) return;
    let still: string | null = null;
    if (node && previewEngineFor(node).engine === 'image') {
      still = nodeFileUrl(node.id);
    } else {
      try {
        const url = canvasRef.current?.querySelector('canvas')?.toDataURL('image/png');
        if (url && url.length > 'data:,'.length) still = url;
      } catch {
        // A tainted or lost WebGL canvas: there is simply no still to trace.
      }
    }
    setTraceSrc(still);
    setMode(projectId, 'sketch');
  };

  const run = (action: PrimaryAction | 'describe' | 'sketch' | 'reference' | DrawerPanel) => {
    if (!projectId) return;
    switch (action) {
      case 'describe':
        onDescribe?.();
        return;
      case 'sketch':
        enterSketch();
        useSketchStore.getState().setTool('draw');
        return;
      case 'reference':
      case 'references':
        openDrawer(projectId, 'references');
        return;
      case 'concept_3d':
        mesh.start(false);
        return;
      case 'make_editable':
      case 'editable':
        if (mode !== 'sketch') enterSketch();
        openDrawer(projectId, 'editable');
        return;
      case 'plane':
        if (mode !== 'sketch') enterSketch();
        openDrawer(projectId, 'plane');
        return;
      case 'generate':
        openDrawer(projectId, 'generate');
        return;
      case 'build_exact':
        onBuildExact?.();
        return;
      case 'prepare_prototype':
        onPrepare?.();
        return;
      case 'start':
        return;
    }
  };

  const more: { id: string; label: string; onClick: () => void }[] = [];
  if (projectId) {
    more.push({ id: 'generate', label: 'Generate a concept', onClick: () => run('generate') });
    if (primary !== 'concept_3d') more.push({ id: 'concept_3d', label: PRIMARY_LABEL.concept_3d, onClick: () => run('concept_3d') });
    if (hasSketch && primary !== 'make_editable') more.push({ id: 'editable', label: 'Editable sketch', onClick: () => run('editable') });
    if (sketchDoc?.itemKey) more.push({ id: 'plane', label: 'Sketch plane', onClick: () => run('plane') });
    if (primary !== 'start') {
      more.push({ id: 'reference', label: 'Add reference', onClick: () => run('reference') });
      if (mode === 'view') more.push({ id: 'sketch', label: 'Sketch it', onClick: () => run('sketch') });
      more.push({ id: 'describe', label: 'Describe a change', onClick: () => run('describe') });
    }
    if (primary !== 'build_exact' && primary !== 'prepare_prototype' && primary !== 'start') {
      more.push({ id: 'build_exact', label: PRIMARY_LABEL.build_exact, onClick: () => run('build_exact') });
    }
  }
  if (node) more.push({ id: 'discuss', label: 'Discuss this', onClick: () => onDiscuss(node) });

  const drawerOpen = !!projectId && !!layout.drawer;
  const sketch =
    projectId && mode === 'sketch' ? (
      <SketchLayer
        projectId={projectId}
        referenceNodes={references}
        onRequestPanel={(p) => openDrawer(projectId, p)}
        editableHost={layout.drawer === 'editable' && !layout.drawerCollapsed ? editableHost : null}
      />
    ) : null;

  return (
    <section className={`cv-stage ${presenting ? 'is-presenting' : ''}`} aria-label="Product" data-mode={mode}>
      <div className="cv-stage-bar">
        {projectId && (
          <div className="cv-mode-switch" role="group" aria-label="Stage mode">
            <button type="button" aria-pressed={mode === 'view'} className={mode === 'view' ? 'is-on' : ''} onClick={() => setMode(projectId, 'view')}>
              <Eye size={14} aria-hidden="true" /> View
            </button>
            <button type="button" aria-pressed={mode === 'sketch'} className={mode === 'sketch' ? 'is-on' : ''} onClick={() => mode !== 'sketch' && enterSketch()}>
              <Pencil size={14} aria-hidden="true" /> Sketch
            </button>
          </div>
        )}

        <div className="cv-stage-badges">
          {mode === 'sketch' ? (
            <span className="cv-truth is-concept" data-testid="stage-status">
              <PenLine size={13} aria-hidden="true" /> Sketching · your drawing saves as you go
            </span>
          ) : (
            node &&
            rep && (
              <>
                <span className={`cv-maturity is-${rep.maturity.toLowerCase()}`} data-testid="stage-maturity" title="How far this representation can be trusted">
                  {rep.maturity}
                </span>
                <span
                  className={`cv-truth ${rep.kind === 'engineering' ? 'is-engineering' : 'is-concept'}`}
                  data-testid="stage-status"
                  title={illustrative ? 'Illustrative: not a manufacturable model' : undefined}
                >
                  {rep.kind === 'engineering' ? <Box size={13} aria-hidden="true" /> : <ImageIcon size={13} aria-hidden="true" />}
                  {truthfulLabel(rep.status, rep.maturity, 'Concept · illustrative')}
                </span>
                <span className="cv-revision" title="The revision on screen">
                  {node.name}
                  {revision !== null ? ` · revision ${revision}` : ''}
                </span>
                {illustrative && (
                  <span className="cv-mfg-note" data-testid="stage-mfg-note">
                    {manufacturingNote(node.properties?.manufacturing_note)}
                  </span>
                )}
                {isConceptMesh(node) && node.properties?.fit_to_envelope === true && (
                  <span className="cv-revision" title="Scaled uniformly to the concept layout's envelope">
                    Sized to the concept layout
                  </span>
                )}
              </>
            )
          )}
        </div>

        {projectId && (
          <div className="cv-stage-next">
            {mode === 'sketch' && (
              <label className="cv-toggle" title={traceSrc ? 'A faint still of the view under the paper' : 'There is no still of the current view to trace'}>
                <input type="checkbox" checked={layout.trace} disabled={!traceSrc} onChange={(e) => setTrace(projectId, e.target.checked)} />
                Trace over the current view
              </label>
            )}
            {mode === 'view' && hasSketch && (
              <button
                type="button"
                className={`cv-chip-btn ${layout.overlay ? 'is-on' : ''}`}
                aria-pressed={layout.overlay}
                onClick={() => setOverlay(projectId, !layout.overlay)}
                title="Show your saved sketch as a thin overlay"
              >
                <Layers size={14} aria-hidden="true" /> My sketch
              </button>
            )}
            {primary === 'start' ? (
              <div className="cv-start" role="group" aria-label="Start">
                <button type="button" className="cv-primary" onClick={() => run('describe')}>
                  <MessageSquare size={14} aria-hidden="true" /> Describe
                </button>
                <button type="button" className="cv-primary" onClick={() => run('sketch')}>
                  <Pencil size={14} aria-hidden="true" /> Sketch it
                </button>
                <button type="button" className="cv-primary" onClick={() => run('reference')}>
                  <Paperclip size={14} aria-hidden="true" /> Add reference
                </button>
              </div>
            ) : (
              primary && (
                <button
                  type="button"
                  className="cv-primary"
                  data-testid="stage-primary"
                  disabled={primary === 'concept_3d' && (mesh.starting || mesh.running)}
                  title={primary === 'concept_3d' ? 'A paid generation of about two and a half minutes. The result is illustrative.' : undefined}
                  onClick={() => run(primary)}
                >
                  {primary === 'concept_3d' ? <Sparkles size={14} aria-hidden="true" /> : null}
                  {primary === 'concept_3d' && mesh.running ? 'Making a 3D concept…' : PRIMARY_LABEL[primary]}
                </button>
              )
            )}
            {more.length > 0 && <MoreMenu items={more} />}
          </div>
        )}
      </div>

      {(mesh.spendPrompt || mesh.error) && (
        <div className={`cv-stage-notice ${mesh.spendPrompt ? 'is-spend' : 'is-error'}`} role="alert" data-testid="concept-mesh-notice">
          <span>{mesh.spendPrompt ?? mesh.error}</span>
          {mesh.spendPrompt && (
            <>
              <button type="button" className="cv-chip-btn" onClick={() => mesh.start(true)}>
                Make it anyway
              </button>
              <button type="button" className="cv-chip-btn" onClick={mesh.dismissSpend}>
                Not now
              </button>
            </>
          )}
        </div>
      )}

      <div className={`cv-stage-body ${drawerOpen ? 'has-drawer' : ''}`}>
        <div ref={canvasRef} className={`cv-stage-canvas is-${mode}`} data-testid="stage-canvas">
          {mode === 'sketch' ? (
            <div className="cv-paper" data-testid="sketch-paper">
              {layout.trace && traceSrc && (
                <img className="cv-trace" src={traceSrc} alt="" aria-hidden="true" draggable={false} data-testid="stage-trace" />
              )}
              {sketch}
            </div>
          ) : node ? (
            <>
              <PreviewHost key={node.id} node={node} mode="modal" />
              {projectId && layout.overlay && hasSketch && <SketchOverlay projectId={projectId} />}
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
                  : 'Describe it, sketch it, or add a reference. Each one feeds the same design.'}
              </p>
            </div>
          )}
          {mode === 'view' &&
            (checksText ? (
              <div className="cv-stage-working" role="status">
                {engineeringChecks === 'running' && <Loader2 size={14} className="cv-spin" aria-hidden="true" />}
                {checksText}
              </div>
            ) : mesh.running ? (
              <div className="cv-stage-working" role="status">
                <Loader2 size={14} className="cv-spin" aria-hidden="true" />
                Making a 3D concept. {node ? 'This is the latest finished one.' : ''}
              </div>
            ) : (
              working &&
              node && (
                <div className="cv-stage-working" role="status">
                  <Loader2 size={14} className="cv-spin" aria-hidden="true" />
                  Working on the next version. This is the latest finished one.
                </div>
              )
            ))}
        </div>
        {projectId && layout.drawer && (
          <StageDrawer projectId={projectId} panel={layout.drawer} collapsed={layout.drawerCollapsed} onEditableHost={setEditableHost} />
        )}
      </div>

      {mode === 'view' && visual.length > 1 && node && (
        <ol className="cv-versions" aria-label="Earlier results">
          {visual.map((n) => {
            const r = representationOf(n);
            return (
              <li key={n.id}>
                <button
                  type="button"
                  className={`cv-version ${n.id === node.id ? 'is-on' : ''}`}
                  aria-pressed={n.id === node.id}
                  onClick={() => onSelect(n.id)}
                  title={`${n.name} · ${r.status}`}
                >
                  {isEngineering(n) ? <Box size={14} aria-hidden="true" /> : <ImageIcon size={14} aria-hidden="true" />}
                  <span>{n.name}</span>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

/** Everything that is not the one primary action (UXS-002). */
function MoreMenu({ items }: { items: { id: string; label: string; onClick: () => void }[] }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);
  return (
    <div ref={root} className="cv-more">
      <button type="button" className="cv-chip-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        More <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open && (
        <div className="cv-more-menu" role="menu" aria-label="More actions">
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                item.onClick();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
