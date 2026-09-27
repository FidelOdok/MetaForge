import { Factory } from 'lucide-react';
import type { TwinNode } from '../../types/twin';
import { iconForNode } from '../../utils/wp-icons';

/**
 * What you would actually hand to a fabricator, and what is still missing.
 *
 * The other tabs answer "what does the design look like". This one answers
 * "could it be built", which is a different question and the one MetaForge
 * exists to make answerable — the prime rule is that nothing ships unless it
 * can be versioned, reviewed and built.
 *
 * So the view is a release checklist rather than another node list. Absence
 * is the interesting state: a fabrication package with no pick-and-place is
 * incomplete in a way that a list of the files you *do* have will never
 * show. Each missing output names the tool that produces it, because "not
 * produced" without a next step is just a shrug.
 */

interface OutputSpec {
  /** wp_types that satisfy this output. */
  types: string[];
  label: string;
  /** Why a fabricator needs it. */
  purpose: string;
  /** The MCP tool that produces it, for the empty state. */
  producedBy: string;
}

/** Grouped the way a fab handoff is actually assembled. */
const GROUPS: { group: string; outputs: OutputSpec[] }[] = [
  {
    group: 'Fabrication',
    outputs: [
      {
        types: ['gerber'],
        label: 'Gerbers',
        purpose: 'Copper, mask and silkscreen layers the board house images from.',
        producedBy: 'kicad.export_gerber',
      },
      {
        types: ['pick_and_place'],
        label: 'Pick and place',
        purpose: 'Component centroids and rotations for the assembly machine.',
        producedBy: 'kicad.export_bom',
      },
    ],
  },
  {
    group: 'Procurement',
    outputs: [
      {
        types: ['bom'],
        label: 'Bill of materials',
        purpose: 'What to buy, in what quantity, against which part numbers.',
        producedBy: 'kicad.export_bom',
      },
      {
        types: ['procurement_record'],
        label: 'Procurement record',
        purpose: 'Chosen distributor, pricing and availability at time of order.',
        producedBy: 'distributors.resolve_offers',
      },
    ],
  },
  {
    group: 'Mechanical',
    outputs: [
      {
        types: ['technical_drawing'],
        label: 'Technical drawing',
        purpose: 'Dimensioned drawing with tolerances for a machinist.',
        producedBy: 'freecad.export_model',
      },
      {
        types: ['cad_model'],
        label: 'CAD model',
        purpose: 'STEP geometry for quoting and CAM.',
        producedBy: 'freecad.export_geometry',
      },
    ],
  },
];

/** Anything manufacturing-related that no specific output above claims. */
const CATCH_ALL = 'manufacturing_file';

function typeOf(node: TwinNode): string {
  return node.properties.wp_type ? String(node.properties.wp_type) : '';
}

export function ManufacturingView({
  nodes,
  onSelect,
}: {
  nodes: TwinNode[];
  onSelect: (id: string) => void;
}) {
  const claimed = new Set(GROUPS.flatMap((g) => g.outputs.flatMap((o) => o.types)));
  const extras = nodes.filter((n) => typeOf(n) === CATCH_ALL && !claimed.has(CATCH_ALL));

  const resolved = GROUPS.map(({ group, outputs }) => ({
    group,
    outputs: outputs.map((spec) => ({
      spec,
      present: nodes.filter((n) => spec.types.includes(typeOf(n))),
    })),
  }));

  const all = resolved.flatMap((g) => g.outputs);
  const ready = all.filter((o) => o.present.length > 0).length;

  return (
    <div className="tw-assembly">
      <div className="tw-assembly-heading">
        <Factory size={20} />
        <h2>Manufacturing</h2>
        <span className="tw-mfg-count">
          {ready} of {all.length} outputs produced
        </span>
      </div>

      <p className="tw-mfg-intro">
        The package a fabricator needs. Missing entries are not errors — they are work not done
        yet, and each names the tool that produces it.
      </p>

      {resolved.map(({ group, outputs }) => (
        <section key={group} className="tw-mfg-group">
          <h3>{group}</h3>
          {outputs.map(({ spec, present }) => (
            <div
              key={spec.label}
              className={present.length ? 'tw-mfg-row is-present' : 'tw-mfg-row is-missing'}
            >
              <div className="tw-mfg-row-head">
                <strong>{spec.label}</strong>
                <span>{present.length ? `${present.length} in the twin` : 'Not produced'}</span>
              </div>
              <p>{spec.purpose}</p>
              {present.length ? (
                <ul>
                  {present.map((n) => (
                    <li key={n.id}>
                      <button type="button" onClick={() => onSelect(n.id)}>
                        <span className="material-symbols-outlined">{iconForNode(n)}</span>
                        {n.name}
                        <small>{n.status}</small>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="tw-mfg-hint">
                  Produce with <code>{spec.producedBy}</code>
                </p>
              )}
            </div>
          ))}
        </section>
      ))}

      {extras.length > 0 && (
        <section className="tw-mfg-group">
          <h3>Other manufacturing files</h3>
          <ul>
            {extras.map((n) => (
              <li key={n.id}>
                <button type="button" onClick={() => onSelect(n.id)}>
                  <span className="material-symbols-outlined">{iconForNode(n)}</span>
                  {n.name}
                  <small>{n.status}</small>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
