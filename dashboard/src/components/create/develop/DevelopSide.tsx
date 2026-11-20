import { useState } from 'react';
import type { SystemArchitectureView, TwinNode } from '../../../types/twin';
import { boardSheetNode } from './BoardSheets';
import { isCable } from './SystemDiagram';

interface DevelopSideProps {
  arch: SystemArchitectureView | undefined;
  nodes: TwinNode[];
  onAsk: (text: string) => void;
  onOpenBoard: (board: string, sheet: 'schematic' | 'pcb') => void;
}

interface Next {
  title: string;
  text: string;
  action: string;
  ask: string;
}

/** The nearest thing between the design and its electronics being complete. */
export function developNext(arch: SystemArchitectureView | undefined, nodes: TwinNode[]): Next | null {
  if (!arch) {
    return {
      title: 'Describe the system',
      text: 'Which boards, bought modules and cables the product has, and where each sits. Each board then gets its own schematic.',
      action: 'Describe the system',
      ask: 'Define the system architecture: the boards, bought modules and cables, with each part on one board.',
    };
  }
  for (const b of arch.boards) {
    if (!boardSheetNode(nodes, b, 'schematic')) {
      return { title: `${b} has no schematic`, text: `Generate it from the ${b}'s parts and run ERC.`, action: 'Generate schematic', ask: `Generate the schematic for ${b} and run ERC.` };
    }
    if (!boardSheetNode(nodes, b, 'pcb')) {
      return { title: `${b} has no layout`, text: `Lay out and route it, then run DRC.`, action: 'Lay out and route', ask: `Lay out and route ${b}, then run DRC.` };
    }
  }
  return null;
}

/** Imagine's pattern for Develop: one next step, then rows that open. */
export function DevelopSide({ arch, nodes, onAsk, onOpenBoard }: DevelopSideProps) {
  const [open, setOpen] = useState<string | null>(null);
  const next = developNext(arch, nodes);
  const cables = (arch?.interfaces ?? []).filter(isCable);
  const boards = arch?.boards ?? [];
  const done = boards.filter((b) => boardSheetNode(nodes, b, 'schematic') && boardSheetNode(nodes, b, 'pcb')).length;
  const rows = [
    {
      id: 'boards',
      title: 'Boards',
      tone: boards.length && done === boards.length ? 'ok' : boards.length ? 'warn' : 'none',
      summary: boards.length ? `${done} of ${boards.length} laid out` : 'none defined',
      body: boards.length ? (
        <ul className="cv-dev-list">
          {boards.map((b) => {
            const sch = boardSheetNode(nodes, b, 'schematic');
            const pcb = boardSheetNode(nodes, b, 'pcb');
            return (
              <li key={b}>
                <span>{b}</span>
                <span className="cv-dev-links">
                  <button type="button" className="cv-link" onClick={() => onOpenBoard(b, 'schematic')}>
                    {sch ? 'schematic' : 'no schematic'}
                  </button>
                  <button type="button" className="cv-link" onClick={() => onOpenBoard(b, 'pcb')}>
                    {pcb ? 'layout' : 'no layout'}
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="cv-muted">No boards in the system schematic yet.</p>
      ),
    },
    {
      id: 'cables',
      title: 'Cables',
      tone: cables.length ? 'ok' : 'none',
      summary: cables.length ? `${cables.length} between boards` : 'none',
      body: cables.length ? (
        <ul className="cv-dev-list">
          {cables.map((c, n) => (
            <li key={`${c.from}-${c.to}-${n}`}>
              <span>
                {c.from}
                {c.from_connector ? ` ${c.from_connector}` : ''} → {c.to}
                {c.to_connector ? ` ${c.to_connector}` : ''}
              </span>
              <span className="cv-muted">{[c.wires ? `${c.wires} wires` : '', c.length_mm ? `${c.length_mm} mm` : ''].filter(Boolean).join(' · ')}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="cv-muted">No cables recorded between boards.</p>
      ),
    },
  ];
  return (
    <aside className="cv-dev-side" aria-label="Next step and checks" data-testid="develop-side">
      {next && (
        <section className="cv-dev-next" data-testid="develop-next">
          <span className="cv-dev-kicker">Next step</span>
          <h3>{next.title}</h3>
          <p>{next.text}</p>
          <button type="button" className="cv-primary" onClick={() => onAsk(next.ask)}>
            {next.action}
          </button>
        </section>
      )}
      {rows.map((r) => {
        const isOpen = open === r.id;
        return (
          <section key={r.id} className={`cv-dev-row${isOpen ? ' is-open' : ''}`}>
            <button type="button" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : r.id)}>
              <strong>{r.title}</strong>
              <span className="cv-dev-sum">
                <i className={`cv-dot is-${r.tone}`} aria-hidden="true" />
                {r.summary}
              </span>
            </button>
            {isOpen && <div className="cv-dev-body">{r.body}</div>}
          </section>
        );
      })}
    </aside>
  );
}
