import { useState } from 'react';
import { nodeNamedFileUrl } from '../../../api/endpoints/twin';
import type { TwinNode } from '../../../types/twin';
import { AuthedImg } from '../../shared/AuthedImg';
import { BoardModel3D } from './BoardModel3D';
import { wpTypeOf } from '../ProductStage';

export type BoardSheet = 'schematic' | 'pcb';

const TYPES: Record<BoardSheet, ReadonlySet<string>> = {
  schematic: new Set(['schematic']),
  pcb: new Set(['pcb_layout', 'pcb']),
};

/** The newest design-files node of this kind for this board, if any. */
export function boardSheetNode(nodes: TwinNode[], board: string | null, sheet: BoardSheet): TwinNode | undefined {
  return nodes
    .filter((n) => TYPES[sheet].has(wpTypeOf(n)) && n.designFiles && (board === null || n.designFiles.board === board))
    .sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))[0];
}

interface BoardSheetsProps {
  nodes: TwinNode[];
  board: string | null;
  sheet: BoardSheet;
  onAsk: (text: string) => void;
}

/** One board's schematic sheets or layout, from the files twin.record_design_files kept. */
export function BoardSheets({ nodes, board, sheet, onAsk }: BoardSheetsProps) {
  const node = boardSheetNode(nodes, board, sheet);
  const what = sheet === 'schematic' ? 'schematic' : 'layout';
  if (!node) {
    const target = board ?? 'this board';
    return (
      <div className="cv-empty" data-testid={`board-${sheet}-empty`}>
        <h2>No {what} for {target} yet</h2>
        <p className="cv-muted">Each board has its own {what}. Generate it from the board's parts in the system schematic.</p>
        <button
          type="button"
          className="cv-primary"
          onClick={() => onAsk(sheet === 'schematic' ? `Generate the schematic for ${target} and run ERC.` : `Lay out and route ${target}, then run DRC.`)}
        >
          {sheet === 'schematic' ? 'Generate schematic' : 'Lay out and route'}
        </button>
      </div>
    );
  }
  const all = node.designFiles?.previews ?? [];
  // A recorded layout also keeps the populated board as a GLB (kicad.export_3d).
  const model = sheet === 'pcb' ? all.find((p) => p.toLowerCase().endsWith('.glb')) : undefined;
  const previews = all.filter((p) => !p.toLowerCase().endsWith('.glb'));
  return <Sheets node={node} sheet={sheet} previews={previews} model={model} board={board} />;
}

interface SheetsProps {
  node: TwinNode;
  sheet: BoardSheet;
  previews: string[];
  model?: string;
  board: string | null;
}

function Sheets({ node, sheet, previews, model, board }: SheetsProps) {
  const [view, setView] = useState<'layout' | '3d'>('layout');
  const label = board ?? node.name;
  return (
    <div className="cv-board-sheets" data-testid={`board-${sheet}`}>
      {model !== undefined && (
        <div className="cv-seg cv-board-view" role="tablist" aria-label="How to show the board">
          {(['layout', '3d'] as const).map((v) => (
            <button key={v} type="button" role="tab" aria-selected={view === v} className={view === v ? 'is-on' : ''} onClick={() => setView(v)}>
              {v === 'layout' ? 'Layout' : '3D'}
            </button>
          ))}
        </div>
      )}
      {model !== undefined && view === '3d' ? (
        <BoardModel3D src={nodeNamedFileUrl(node.id, model)} label={label} />
      ) : previews.length === 0 ? (
        <p className="cv-muted" role="status">
          {node.designFiles?.storeError
            ? `The preview was made but could not be stored: ${node.designFiles.storeError}`
            : node.designFiles?.previewError || `No preview was made for ${node.name}.`}
        </p>
      ) : (
        previews.map((p) => <AuthedImg key={p} src={nodeNamedFileUrl(node.id, p)} alt={`${node.name}: ${p}`} />)
      )}
    </div>
  );
}
