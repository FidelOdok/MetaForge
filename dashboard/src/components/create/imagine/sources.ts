/**
 * Where each studio value came from, for the Create view (2026-11-15).
 *
 * The gateway now takes a linked part's size and price, the machine and the material's
 * figures from the MetaForge library on every studio write, and marks each with
 * `derived_from: "library"` and a `source`. This reads those marks out of the studio
 * config so a person can see, per value, whether it is the library's (and which entry
 * and version), something typed in with its own source, or an estimate.
 */

export type Origin = 'library' | 'typed' | 'estimate';

export interface ValueSource {
  /** The part, machine or material the value belongs to. */
  subject: string;
  /** Size, Price, Machine or Material. */
  what: 'Size' | 'Price' | 'Machine' | 'Material';
  /** The value in a short form, when there is one to show. */
  value?: string;
  origin: Origin;
  /** Where it came from, as recorded: library entry and version, a page, a datasheet. */
  source: string;
  /** For a machine or material: which figures came from where. */
  detail?: string;
}

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const text = (v: unknown): string => (typeof v === 'string' ? v : '');
const list = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);

function fmtSize(env: unknown): string | undefined {
  if (!Array.isArray(env) || env.length !== 3 || !env.every((x) => typeof x === 'number')) return undefined;
  return `${(env as number[]).map((x) => +x.toFixed(2)).join(' × ')} mm`;
}

function originOf(item: Obj): Origin {
  if (item.derived_from === 'library') return 'library';
  if (item.size_basis === 'typical' || item.size_basis === 'generic') return 'estimate';
  return 'typed';
}

export function valueSources(config: unknown): ValueSource[] {
  if (!isObj(config)) return [];
  const rows: ValueSource[] = [];
  const context = isObj(config.context) ? config.context : {};
  const components = isObj(context.components) ? context.components : {};
  const parts = Array.isArray(components.parts) ? components.parts.filter(isObj) : [];
  for (const part of parts) {
    if (part.standIn || part.excluded) continue;
    const subject = text(part.name) || text(part.id) || 'Part';
    if (isObj(part.geometry) && part.geometry.envelope) {
      rows.push({
        subject,
        what: 'Size',
        value: fmtSize(part.geometry.envelope),
        origin: originOf(part.geometry),
        source: text(part.geometry.source) || text(part.geometry.fidelity_basis) || 'not stated',
      });
    }
    if (isObj(part.cost) && typeof part.cost.unit === 'number') {
      rows.push({
        subject,
        what: 'Price',
        value: `${part.cost.unit} ${text(part.cost.currency) || 'GBP'}`,
        origin: part.cost.derived_from === 'library' ? 'library' : 'typed',
        source: text(part.cost.source) || 'not stated',
      });
    }
  }
  const process = isObj(config.process) ? config.process : {};
  const block = isObj(context.machines) ? context.machines : {};
  const chosen = text(process.machine);
  const machines = Array.isArray(block.machines) ? block.machines.filter(isObj) : [];
  const machine = machines.find((m) => m.id === chosen);
  if (machine) {
    const fromProcess = list(machine.fromProcess);
    const defaulted = list(machine.defaulted);
    rows.push({
      subject: text(machine.name) || chosen,
      what: 'Machine',
      origin: machine.derived_from === 'library' ? 'library' : 'typed',
      source: text(machine.source) || 'not stated',
      detail: [
        fromProcess.length ? `from the process guide: ${fromProcess.join(', ')}` : '',
        defaulted.length ? `defaults: ${defaulted.join(', ')}` : '',
      ]
        .filter(Boolean)
        .join('; '),
    });
  }
  const materials = Array.isArray(block.materials) ? block.materials.filter(isObj) : [];
  for (const mat of materials.filter((m) => m.derived_from === 'library')) {
    const from = list(mat.fromLibrary);
    const defaulted = list(mat.defaulted);
    rows.push({
      subject: text(mat.name) || text(mat.id),
      what: 'Material',
      origin: 'library',
      source: text(mat.source) || 'not stated',
      detail: [from.length ? `from the library: ${from.join(', ')}` : '', defaulted.length ? `catalogue defaults: ${defaulted.join(', ')}` : '']
        .filter(Boolean)
        .join('; '),
    });
  }
  return rows;
}
