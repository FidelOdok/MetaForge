import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleAlert } from 'lucide-react';
import { getReconstruction, reconstructionKeys } from '../../api/endpoints/reconstruction';
import { makeStructuredMesh } from '../../api/endpoints/representation';
import type { CreationView } from '../../api/endpoints/create';
import { useBuildExact } from '../../hooks/use-build-exact';
import { twinKeys } from '../../hooks/use-twin';
import type { TwinNode } from '../../types/twin';
import { BuildExactNotice } from './BuildExactNotice';
import { MakeEditablePanel } from './MakeEditablePanel';
import { ProductStage, defaultStageNode, isEngineering, isVisual } from './ProductStage';
import { StageActions, type StageAction } from './StageActions';
import { StageDisclosures } from './StageDisclosures';
import { DEVELOP_LABEL, developPrimary, latestParametricSketch } from './stage-logic';
import { BoardSheets } from './develop/BoardSheets';
import { DevelopSide } from './develop/DevelopSide';
import { SystemDiagram } from './develop/SystemDiagram';
import type { SystemArchitectureView } from '../../types/twin';

type DevelopView = 'model' | 'system' | 'schematic' | 'pcb';
const DEVELOP_VIEWS: { id: DevelopView; label: string }[] = [
  { id: 'model', label: '3D' },
  { id: 'system', label: 'System' },
  { id: 'schematic', label: 'Schematic' },
  { id: 'pcb', label: 'PCB' },
];

/** The newest system schematic among the project's nodes. */
export function latestArchitecture(nodes: TwinNode[]): SystemArchitectureView | undefined {
  return nodes
    .filter((n) => n.systemArchitecture && n.systemArchitecture.components.length > 0)
    .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))[0]?.systemArchitecture;
}

interface DevelopStageProps {
  projectId: string;
  nodes: TwinNode[];
  view: CreationView | undefined;
  selectedId: string | null;
  onSelect: (id: string) => void;
  working: boolean;
  engineeringChecks: 'running' | 'failed' | null;
  /** Puts a request in the conversation's composer and focuses it. */
  onAsk: (text: string) => void;
  /** A Build exact fix that belongs to the sketch (the editable sketch, the plane). */
  onSketchFix: (fix: 'editable' | 'plane') => void;
}

/**
 * Develop: the twin's current model and what needs attention, with one
 * primary action. Not a second editor: changes go through the assistant, or
 * back through Imagine.
 */
export function DevelopStage({
  projectId,
  nodes,
  view,
  selectedId,
  onSelect,
  working,
  engineeringChecks,
  onAsk,
  onSketchFix,
}: DevelopStageProps) {
  const qc = useQueryClient();
  const psk = useMemo(() => latestParametricSketch(nodes), [nodes]);
  const exact = useBuildExact(projectId, psk?.key ?? null);
  const reconstruction = useQuery({
    queryKey: reconstructionKeys.project(projectId),
    queryFn: ({ signal }) => getReconstruction(projectId, signal),
    retry: false,
  });
  const [editableOpen, setEditableOpen] = useState(false);
  const [openDetail, setOpenDetail] = useState<string | null>(null);
  const [partsState, setPartsState] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const [devView, setDevView] = useState<DevelopView>('model');
  const [board, setBoard] = useState<string | null>(null);
  const arch = useMemo(() => latestArchitecture(nodes), [nodes]);
  const activeBoard = board && arch?.boards.includes(board) ? board : (arch?.boards[0] ?? null);
  const openBoard = (b: string, sheet: 'schematic' | 'pcb') => {
    setBoard(b);
    setDevView(sheet);
  };

  const visual = nodes.filter(isVisual);
  const node = visual.find((n) => n.id === selectedId) ?? defaultStageNode(visual);
  const hasEngineering = nodes.some(isEngineering);
  const conceptAccepted = !!reconstruction.data?.concept_accepted;
  const action = developPrimary({
    hasEngineering,
    hasParametricSketch: !!psk,
    conceptAccepted,
    editableFromConcept: !!reconstruction.data?.accepted,
  });

  /** Show as simple 3D parts (L1): free and fast, from the concept layout. */
  const showSimpleParts = async () => {
    setPartsState({ busy: true, error: null });
    try {
      const made = await makeStructuredMesh(projectId);
      await qc.invalidateQueries({ queryKey: twinKeys.all });
      onSelect(made.node_id);
      setPartsState({ busy: false, error: null });
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setPartsState({ busy: false, error: typeof detail === 'string' ? detail : 'The simple 3D parts could not be made.' });
    }
  };

  const run = (a: typeof action) => {
    switch (a) {
      case 'build_exact':
        void exact.start();
        return;
      case 'reconstruct':
        setEditableOpen(true);
        return;
      case 'request_exact':
        onAsk('Build an exact, dimensioned model from the handed-over concept.');
        return;
      case 'describe_change':
        onAsk('');
        return;
    }
  };

  const more: StageAction[] = [];
  if (action !== 'reconstruct' && conceptAccepted && !hasEngineering) {
    more.push({ id: 'reconstruct', label: DEVELOP_LABEL.reconstruct, onClick: () => run('reconstruct') });
  }
  if (!hasEngineering) more.push({ id: 'simple_parts', label: 'Show as simple 3D parts', onClick: () => void showSimpleParts() });
  if (node) more.push({ id: 'discuss', label: 'Discuss this model', onClick: () => onAsk(`About ${node.name}: `) });

  const blockers = view?.readiness.blockers ?? [];

  return (
    <div className="cv-stage-col is-model">
      <div className="cv-develop" data-testid="develop-studio">
        <div className="cv-develop-main">
          <div className="cv-develop-bar">
            <div className="cv-seg" role="tablist" aria-label="Develop view">
              {DEVELOP_VIEWS.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  role="tab"
                  aria-selected={devView === v.id}
                  className={devView === v.id ? 'is-on' : ''}
                  onClick={() => setDevView(v.id)}
                >
                  {v.label}
                </button>
              ))}
            </div>
            {(devView === 'schematic' || devView === 'pcb') && arch && arch.boards.length > 1 && (
              <div className="cv-seg" role="group" aria-label="Board">
                {arch.boards.map((b) => (
                  <button key={b} type="button" aria-pressed={activeBoard === b} className={activeBoard === b ? 'is-on' : ''} onClick={() => setBoard(b)}>
                    {b}
                  </button>
                ))}
              </div>
            )}
          </div>
          {devView === 'model' && (
            <ProductStage
              projectId={projectId}
              nodes={nodes}
              selectedId={selectedId}
              working={working}
              engineeringChecks={engineeringChecks}
            />
          )}
          {devView === 'system' &&
            (arch ? (
              <SystemDiagram arch={arch} onOpenBoard={(b) => openBoard(b, 'schematic')} />
            ) : (
              <div className="cv-empty" data-testid="system-empty">
                <h2>No system schematic yet</h2>
                <p className="cv-muted">Which boards, bought modules and cables the product has, and where each sits.</p>
                <button type="button" className="cv-primary" onClick={() => onAsk('Define the system architecture: the boards, bought modules and cables, with each part on one board.')}>
                  Describe the system
                </button>
              </div>
            ))}
          {(devView === 'schematic' || devView === 'pcb') && <BoardSheets nodes={nodes} board={activeBoard} sheet={devView} onAsk={onAsk} />}
        </div>
        <DevelopSide arch={arch} nodes={nodes} onAsk={onAsk} onOpenBoard={openBoard} />
      </div>

      <StageDisclosures
        open={openDetail}
        onOpen={setOpenDetail}
        lead={
          <StageActions
            primary={{
              id: action,
              label: DEVELOP_LABEL[action],
              onClick: () => run(action),
              disabled: action === 'build_exact' && exact.state.phase !== 'idle' && exact.state.phase !== 'started',
            }}
            more={more}
          />
        }
        items={[
          {
            id: 'attention',
            label: 'Needs attention',
            badge: String(blockers.length),
            tone: blockers.length ? 'warn' : 'ok',
            icon: <CircleAlert size={14} aria-hidden="true" />,
            content: blockers.length ? (
              <ul className="cv-blockers" data-testid="develop-attention">
                {blockers.map((b) => (
                  <li key={b.id}>
                    <CircleAlert size={13} aria-hidden="true" />
                    <span>
                      {b.message}
                      {b.action && <span className="cv-muted"> · {b.action}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="cv-muted">Nothing needs you right now.</p>
            ),
          },
        ]}
      />

      {exact.state.phase !== 'idle' && (
        <BuildExactNotice
          state={exact.state}
          onAnswer={(v) => void exact.answer(v)}
          onFix={(fix) => {
            exact.dismiss();
            onSketchFix(fix);
          }}
          onDismiss={exact.dismiss}
        />
      )}
      {(partsState.busy || partsState.error) && (
        <div className={`cv-stage-notice ${partsState.error ? 'is-error' : ''}`} role={partsState.error ? 'alert' : 'status'} data-testid="simple-parts-notice">
          <span>{partsState.error ?? 'Making simple 3D parts…'}</span>
          {partsState.error && (
            <button type="button" className="cv-chip-btn" onClick={() => setPartsState({ busy: false, error: null })}>
              Dismiss
            </button>
          )}
        </div>
      )}
      {editableOpen && <MakeEditablePanel projectId={projectId} onClose={() => setEditableOpen(false)} />}

    </div>
  );
}
