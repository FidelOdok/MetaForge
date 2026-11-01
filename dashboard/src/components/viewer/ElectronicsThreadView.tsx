import { CircuitBoard } from 'lucide-react';
import { nodeNamedFileUrl } from '../../api/endpoints/twin';
import type { TwinNode, TwinRelationship } from '../../types/twin';
import { iconForNode } from '../../utils/wp-icons';

/**
 * FORGE-687: the electronics and firmware thread, from the parts chosen to
 * the files a fab and a programmer receive.
 *
 *   BOM -> schematic -> board -> gerbers + placement
 *              \-> pin map -> firmware
 *
 * Each stage shows the current revision of what is recorded (an item's
 * highest revision; a node with no item stands for itself), with the sheet or
 * board preview where there is one. Each arrow says whether the downstream
 * node really is linked GENERATED_FROM the upstream one in the graph, since
 * that link is what lets a changed part be traced to every file it touches.
 * A link to an earlier revision of the upstream item is shown as such: the
 * board was made from schematic @1 and the schematic is now @2, so the board
 * is out of date, which is different from never having been linked.
 * A stage with nothing recorded names the tools that produce it.
 */

interface Stage {
  type: string;
  label: string;
  producedBy: string;
}

const BOM: Stage = { type: 'bom', label: 'Bill of materials', producedBy: 'twin.record_component_selection' };
const SCHEMATIC: Stage = {
  type: 'schematic',
  label: 'Schematic',
  producedBy: 'kicad.generate_schematic, then twin.record_design_files',
};
const BOARD: Stage = {
  type: 'pcb_layout',
  label: 'Board',
  producedBy: 'kicad.generate_pcb and kicad.autoroute_pcb, then twin.record_design_files',
};
const GERBER: Stage = {
  type: 'gerber',
  label: 'Gerbers',
  producedBy: 'kicad.export_gerber and kicad.export_drill, then twin.record_design_files',
};
const PLACEMENT: Stage = {
  type: 'pick_and_place',
  label: 'Pick and place',
  producedBy: 'kicad.export_pos, then twin.record_design_files',
};
const PINMAP: Stage = {
  type: 'pinmap',
  label: 'Pin map',
  producedBy: 'kicad.get_pin_mapping, then twin.record_design_files',
};
const FIRMWARE: Stage = {
  type: 'firmware_source',
  label: 'Firmware',
  producedBy: 'firmware.build, then twin.record_design_files',
};

/** Downstream stage -> the stage it is generated from. */
export const THREAD_LINKS: [Stage, Stage][] = [
  [SCHEMATIC, BOM],
  [BOARD, SCHEMATIC],
  [GERBER, BOARD],
  [PLACEMENT, BOARD],
  [PINMAP, SCHEMATIC],
  [FIRMWARE, PINMAP],
];

const ALL_STAGES = [BOM, SCHEMATIC, BOARD, GERBER, PLACEMENT, PINMAP, FIRMWARE];

function wpType(n: TwinNode): string {
  return n.properties.wp_type ? String(n.properties.wp_type) : '';
}

/** The current revision of each item of `type`; a node with no item stands for itself. */
export function currentOf(nodes: TwinNode[], type: string): TwinNode[] {
  const heads = new Map<string, TwinNode>();
  for (const n of nodes) {
    if (wpType(n) !== type) continue;
    const key = n.properties.item_key ? String(n.properties.item_key) : n.id;
    const rev = Number(n.properties.item_revision ?? 0);
    const held = heads.get(key);
    if (!held || rev > Number(held.properties.item_revision ?? 0)) heads.set(key, n);
  }
  return [...heads.values()].sort((a, b) => a.name.localeCompare(b.name));
}

export function isLinked(down: TwinNode[], up: TwinNode[], relationships: TwinRelationship[]): boolean {
  const d = new Set(down.map((n) => n.id));
  const u = new Set(up.map((n) => n.id));
  return relationships.some((r) => r.type === 'generated_from' && d.has(r.sourceId) && u.has(r.targetId));
}

export type LinkState = 'linked' | 'stale' | 'none';

/**
 * 'linked' when a current downstream node is generated from a current
 * upstream one; 'stale' when it is generated from an earlier revision of an
 * upstream item (`staleFrom` names the revision); else 'none'.
 */
export function linkState(
  down: TwinNode[],
  up: TwinNode[],
  all: TwinNode[],
  relationships: TwinRelationship[],
): { state: LinkState; staleFrom?: string } {
  if (isLinked(down, up, relationships)) return { state: 'linked' };
  const d = new Set(down.map((n) => n.id));
  const keys = new Set(up.map((n) => n.properties.item_key).filter(Boolean).map(String));
  const byId = new Map(all.map((n) => [n.id, n]));
  for (const r of relationships) {
    if (r.type !== 'generated_from' || !d.has(r.sourceId)) continue;
    const target = byId.get(r.targetId);
    const key = target?.properties.item_key ? String(target.properties.item_key) : '';
    if (key && keys.has(key)) return { state: 'stale', staleFrom: `@${target?.properties.item_revision ?? '?'}` };
  }
  return { state: 'none' };
}

function StageCard({
  stage,
  nodes,
  onSelect,
}: {
  stage: Stage;
  nodes: TwinNode[];
  onSelect: (id: string) => void;
}) {
  if (!nodes.length) {
    return (
      <div className="tw-thread-card is-missing" data-testid={`thread-${stage.type}`}>
        <strong>{stage.label}</strong>
        <span className="tw-thread-state">Not recorded</span>
        <p>
          Produce with <code>{stage.producedBy}</code>
        </p>
      </div>
    );
  }
  return (
    <div className="tw-thread-card is-present" data-testid={`thread-${stage.type}`}>
      <strong>{stage.label}</strong>
      {nodes.map((n) => {
        const preview = n.designFiles?.previews[0];
        const ref = n.properties.item_key ? `${n.properties.item_key}@${n.properties.item_revision ?? '?'}` : '';
        return (
          <button key={n.id} type="button" onClick={() => onSelect(n.id)} className="tw-thread-node">
            {preview ? (
              <img src={nodeNamedFileUrl(n.id, preview)} alt={`${n.name} preview`} />
            ) : (
              <span className="material-symbols-outlined">{iconForNode(n)}</span>
            )}
            <span className="tw-thread-name">{n.name}</span>
            <small>
              {ref}
              {n.designFiles ? ` · ${n.designFiles.files.length} files` : ''}
            </small>
          </button>
        );
      })}
    </div>
  );
}

function Arrow({ state, staleFrom, present }: { state: LinkState; staleFrom?: string; present: boolean }) {
  const label = !present ? '' : state === 'linked' ? 'generated from' : state === 'stale' ? `from ${staleFrom}` : 'not linked';
  return (
    <div
      className={`tw-thread-arrow ${state === 'linked' ? 'is-linked' : state === 'stale' ? 'is-stale' : 'is-unlinked'}`}
      aria-label={label || 'waiting'}
      title={
        state === 'linked'
          ? 'Linked GENERATED_FROM in the twin'
          : state === 'stale'
            ? `Made from revision ${staleFrom} of the earlier stage, which has been revised since: regenerate it`
            : present
              ? 'Both are recorded, but not linked: pass source_node_ids when recording'
              : 'Waiting for both ends to be recorded'
      }
    >
      <span>{label}</span>
    </div>
  );
}

export function ElectronicsThreadView({
  nodes,
  relationships,
  onSelect,
}: {
  nodes: TwinNode[];
  relationships: TwinRelationship[];
  onSelect: (id: string) => void;
}) {
  const current = new Map(ALL_STAGES.map((s) => [s.type, currentOf(nodes, s.type)]));
  const at = (s: Stage) => current.get(s.type) ?? [];
  const link = (down: Stage, up: Stage) => ({
    ...linkState(at(down), at(up), nodes, relationships),
    present: at(down).length > 0 && at(up).length > 0,
  });
  const recorded = ALL_STAGES.filter((s) => at(s).length > 0).length;
  const states = THREAD_LINKS.map(([d, u]) => link(d, u).state);
  const linkedCount = states.filter((s) => s === 'linked').length;
  const staleCount = states.filter((s) => s === 'stale').length;

  return (
    <div className="tw-assembly tw-thread" data-testid="electronics-thread">
      <div className="tw-assembly-heading">
        <CircuitBoard size={20} />
        <h2>Electronics thread</h2>
        <span className="tw-mfg-count">
          {recorded} of {ALL_STAGES.length} recorded, {linkedCount} of {THREAD_LINKS.length} links
          {staleCount > 0 ? `, ${staleCount} out of date` : ''}
        </span>
      </div>
      <p className="tw-mfg-intro">
        From the parts chosen to the files the fab and the programmer receive. An arrow is solid
        when the later work product is linked to the one it was made from, so a changed part can
        be followed to every file it touches.
      </p>

      <h3>Board</h3>
      <div className="tw-thread-lane">
        <StageCard stage={BOM} nodes={at(BOM)} onSelect={onSelect} />
        <Arrow {...link(SCHEMATIC, BOM)} />
        <StageCard stage={SCHEMATIC} nodes={at(SCHEMATIC)} onSelect={onSelect} />
        <Arrow {...link(BOARD, SCHEMATIC)} />
        <StageCard stage={BOARD} nodes={at(BOARD)} onSelect={onSelect} />
        <div className="tw-thread-fan">
          <div className="tw-thread-lane">
            <Arrow {...link(GERBER, BOARD)} />
            <StageCard stage={GERBER} nodes={at(GERBER)} onSelect={onSelect} />
          </div>
          <div className="tw-thread-lane">
            <Arrow {...link(PLACEMENT, BOARD)} />
            <StageCard stage={PLACEMENT} nodes={at(PLACEMENT)} onSelect={onSelect} />
          </div>
        </div>
      </div>

      <h3>Firmware</h3>
      <div className="tw-thread-lane">
        <div className="tw-thread-from">from the schematic</div>
        <Arrow {...link(PINMAP, SCHEMATIC)} />
        <StageCard stage={PINMAP} nodes={at(PINMAP)} onSelect={onSelect} />
        <Arrow {...link(FIRMWARE, PINMAP)} />
        <StageCard stage={FIRMWARE} nodes={at(FIRMWARE)} onSelect={onSelect} />
      </div>
    </div>
  );
}
