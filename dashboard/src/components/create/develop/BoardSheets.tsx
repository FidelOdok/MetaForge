import { nodeNamedFileUrl } from '../../../api/endpoints/twin';
import type { TwinNode } from '../../../types/twin';
import { AuthedImg } from '../../shared/AuthedImg';
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
  const previews = node.designFiles?.previews ?? [];
  return (
    <div className="cv-board-sheets" data-testid={`board-${sheet}`}>
      {previews.length === 0 ? (
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
