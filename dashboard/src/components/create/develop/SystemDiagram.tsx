import type { SystemArchitectureView, SystemComponent, SystemInterface } from '../../../types/twin';

/**
 * The system schematic: boards you design, bought modules seen only by their
 * interfaces, and the cables between them, grouped by where each sits in the
 * product. A board opens its own schematic. Not every component sits on the
 * same board, so this is the level above any one board's schematic.
 */
interface SystemDiagramProps {
  arch: SystemArchitectureView;
  onOpenBoard: (board: string) => void;
}

const BOX_W = 200;
const BOX_H = 64;
const COL_GAP = 190;
const ROW_GAP = 34;
const PAD = 28;
const HEAD = 30;

interface Placed {
  c: SystemComponent;
  x: number;
  y: number;
  col: number;
}

const LANE_GAP = 26;

/** Columns by location, in the order each location first appears. */
export function layoutSystem(components: SystemComponent[]): { placed: Placed[]; columns: { name: string; x: number }[]; width: number; height: number } {
  const order: string[] = [];
  const byLoc = new Map<string, SystemComponent[]>();
  for (const c of components) {
    const loc = c.location?.trim() || 'unplaced';
    if (!byLoc.has(loc)) {
      byLoc.set(loc, []);
      order.push(loc);
    }
    byLoc.get(loc)!.push(c);
  }
  const placed: Placed[] = [];
  const columns: { name: string; x: number }[] = [];
  let tallest = 0;
  order.forEach((loc, col) => {
    const x = PAD + col * (BOX_W + COL_GAP);
    columns.push({ name: loc, x });
    const items = byLoc.get(loc)!;
    items.forEach((c, row) => placed.push({ c, x, y: PAD + HEAD + row * (BOX_H + ROW_GAP), col }));
    tallest = Math.max(tallest, items.length);
  });
  return {
    placed,
    columns,
    width: PAD * 2 + order.length * BOX_W + Math.max(0, order.length - 1) * COL_GAP,
    height: PAD * 2 + HEAD + tallest * BOX_H + Math.max(0, tallest - 1) * ROW_GAP,
  };
}

function wireLabel(i: SystemInterface): string {
  const ends = [i.from_connector, i.to_connector].filter(Boolean).join('→');
  const bits = [ends, i.wires ? `${i.wires} wires` : '', i.length_mm ? `${i.length_mm} mm` : ''].filter(Boolean);
  return bits.join(' · ') || i.interface_type || i.kind || '';
}

export function SystemDiagram({ arch, onOpenBoard }: SystemDiagramProps) {
  const { placed, columns, width, height: boxesHeight } = layoutSystem(arch.components);
  const at = new Map(placed.map((p) => [p.c.name, p]));
  // A link that skips a column runs in its own lane below every box, never
  // through a box in between (which would read as a connection to it).
  const skips = arch.interfaces.filter((i) => {
    const a = at.get(i.from);
    const b = at.get(i.to);
    return a && b && Math.abs(a.col - b.col) > 1;
  });
  const laneTop = boxesHeight - PAD + 22;
  const height = boxesHeight + skips.length * LANE_GAP + (skips.length ? 18 : 0);
  return (
    <div className="cv-system" data-testid="system-diagram">
      <svg viewBox={`0 0 ${Math.max(width, 320)} ${Math.max(height, 160)}`} role="img" aria-label={`System schematic of ${arch.systemName || 'the product'}`}>
        {columns.map((col) => (
          <text key={col.name} x={col.x} y={PAD + 12} className="cv-system-loc">
            {col.name.toUpperCase()}
          </text>
        ))}
        {arch.interfaces.map((i, n) => {
          const a = at.get(i.from);
          const b = at.get(i.to);
          if (!a || !b) return null;
          const label = wireLabel(i);
          let d: string;
          let lx: number;
          let ly: number;
          let anchor: 'start' | 'middle' = 'middle';
          const lane = skips.indexOf(i);
          if (lane >= 0) {
            const ax = a.x + BOX_W / 2;
            const bx = b.x + BOX_W / 2;
            const y = laneTop + lane * LANE_GAP;
            d = `M${ax} ${a.y + BOX_H} L${ax} ${y} L${bx} ${y} L${bx} ${b.y + BOX_H}`;
            lx = (ax + bx) / 2;
            ly = y - 6;
          } else if (a.col === b.col) {
            const top = a.y < b.y ? a : b;
            const bottom = top === a ? b : a;
            const x = a.x + BOX_W / 2;
            d = `M${x} ${top.y + BOX_H} L${x} ${bottom.y}`;
            lx = x + 8;
            ly = (top.y + BOX_H + bottom.y) / 2 + 4;
            anchor = 'start';
          } else {
            const left = a.col < b.col ? a : b;
            const right = left === a ? b : a;
            const x1 = left.x + BOX_W;
            const y1 = left.y + BOX_H / 2;
            const x2 = right.x;
            const y2 = right.y + BOX_H / 2;
            const mx = (x1 + x2) / 2;
            d = `M${x1} ${y1} L${mx} ${y1} L${mx} ${y2} L${x2} ${y2}`;
            lx = mx;
            ly = Math.min(y1, y2) - 8;
          }
          return (
            <g key={`${i.from}-${i.to}-${n}`} className={`cv-system-wire${i.kind === 'cable' ? ' is-cable' : ''}`}>
              <path d={d} />
              {label && (
                <text x={lx} y={ly} textAnchor={anchor}>
                  {label}
                </text>
              )}
            </g>
          );
        })}
        {placed.map(({ c, x, y }) => {
          const board = c.kind === 'board';
          const sub =
            c.kind === 'module'
              ? 'bought · interfaces only'
              : board
                ? `${c.parts?.length ?? 0} parts`
                : c.discipline || c.kind || '';
          return (
            <g
              key={c.name}
              className={`cv-system-box is-${c.kind ?? 'other'}`}
              role={board ? 'button' : undefined}
              tabIndex={board ? 0 : undefined}
              aria-label={board ? `Open the schematic of ${c.name}` : c.name}
              onClick={board ? () => onOpenBoard(c.name) : undefined}
              onKeyDown={
                board
                  ? (e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        onOpenBoard(c.name);
                      }
                    }
                  : undefined
              }
            >
              <rect x={x} y={y} width={BOX_W} height={BOX_H} rx={8} />
              <text x={x + 12} y={y + 26} className="cv-system-name">
                {c.name}
              </text>
              <text x={x + 12} y={y + 46} className="cv-system-sub">
                {sub}
              </text>
            </g>
          );
        })}
      </svg>
      <p className="cv-muted cv-system-key">Solid: a board you design · dashed: a bought module · click a board to open its schematic</p>
    </div>
  );
}
