import { useMemo, useRef, useState, type ChangeEvent, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Box, Download, ExternalLink, Search, Upload } from 'lucide-react';
import { PageHeader } from '../components/ui/PageHeader';
import { EmptyState } from '../components/ui/EmptyState';
import { Button } from '../components/ui/Button';
import { useToast } from '../components/ui/Toast';
import { PartPreview, activeMakerModel } from '../components/library/PartPreview';
import { CATEGORIES, categoryOf, type Category } from '../lib/libraryCategory';
import './LibraryPage.css';
import {
  exportLibrary,
  importLibrary,
  listComponents,
  listMachines,
  listMaterials,
  type LibraryComponent,
  type LibraryMachine,
  type LibraryMaterial,
  type SourcedValue,
} from '../api/endpoints/library';

type Tab = 'components' | 'materials' | 'machines';
const TABS: Array<[Tab, string]> = [
  ['components', 'Components'],
  ['materials', 'Materials'],
  ['machines', 'Machines'],
];

/** A title the research import marked as a part being discontinued. */
const ENDING = /^\[ENDING[^\]]*\]\s*/;

function isEnding(c: LibraryComponent): boolean {
  return ENDING.test(c.title);
}

function cleanTitle(title: string): string {
  return title.replace(ENDING, '');
}

/** "Nordic nRF52840-QIAA-R: BLE SoC" -> "BLE SoC": the title without its maker and part. */
function describe(c: LibraryComponent): string {
  const title = cleanTitle(c.title || c.name);
  const cut = title.indexOf(': ');
  return cut > 0 ? title.slice(cut + 2) : title;
}

/** A short handle for the part: its part number, else its title. */
function handle(c: LibraryComponent): string {
  const mpn = (c.mpn || '').split(/[,(;]/)[0]?.trim();
  return mpn || cleanTitle(c.title || c.name);
}

function hasModel(c: LibraryComponent): boolean {
  return Boolean(activeMakerModel(c) || c.form_code);
}

const ACRONYMS = /\b(fdm|sla|sls|cnc|pcb|led|cob|ic|mcu|usb)\b/gi;

function label(family: string): string {
  const text = family.replace(/_/g, ' ').replace(ACRONYMS, (a) => a.toUpperCase());
  return text.charAt(0).toUpperCase() + text.slice(1);
}


function formatValue(v: SourcedValue): string {
  if (Array.isArray(v.value)) {
    const numbers = v.value.every((x) => typeof x === 'number');
    return numbers ? `${v.value.join(' x ')} ${v.unit}`.trim() : v.value.join(', ');
  }
  return `${v.value} ${v.unit}`.trim();
}

function SourcedTable({
  values,
  inherited = {},
}: {
  values: Record<string, SourcedValue>;
  /** Values that come from somewhere else (a machine's model), marked as such. */
  inherited?: Record<string, SourcedValue>;
}) {
  const rows: Array<[string, SourcedValue, boolean]> = [
    ...Object.entries(values).map(([k, v]) => [k, v, false] as [string, SourcedValue, boolean]),
    ...Object.entries(inherited)
      .filter(([k]) => !(k in values))
      .map(([k, v]) => [k, v, true] as [string, SourcedValue, boolean]),
  ];
  return (
    <table className="mf-library-table">
      <tbody>
        {rows.map(([key, v, fromModel]) => (
          <tr key={key}>
            <td>{key.replace(/_(mm|kg|kn|n|v|mah|rpm|mpa|c|pct|w_mk|um_mk|kg_m3)$/, '').replace(/_/g, ' ')}</td>
            <td className="font-mono">{formatValue(v)}</td>
            <td className="mf-library-muted">
              {v.source_ref?.startsWith('http') ? (
                <a href={v.source_ref.split(' ')[0]} target="_blank" rel="noopener noreferrer">
                  {v.source_kind.replace(/_/g, ' ')}
                </a>
              ) : (
                v.source_kind.replace(/_/g, ' ')
              )}
              {v.needs_review ? ' · review' : ''}
              {fromModel ? ' · from the model' : ''}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** A collapsible section that says how much it holds; nothing at all when it holds nothing. */
function Section({ title, count, open, children }: { title: string; count?: number; open?: boolean; children: ReactNode }) {
  if (count === 0) return null;
  return (
    <details className="mf-library-section" open={open}>
      <summary>
        {title}
        {count !== undefined && <span className="mf-library-count">{count}</span>}
      </summary>
      <div className="mf-library-section-body">{children}</div>
    </details>
  );
}

function ComponentDetail({ part }: { part: LibraryComponent }) {
  const models = (part.models || []).filter((m) => (m.status ?? 'active') === 'active');
  const specs = Object.keys(part.specs || {}).length;
  const suppliers = part.suppliers || [];
  const facts: Array<[string, ReactNode]> = [
    ...(part.manufacturer ? ([['Maker', part.manufacturer]] as Array<[string, ReactNode]>) : []),
    ['Part', <span className="font-mono mf-library-break">{part.mpn || part.name}</span>],
    ['Category', categoryOf(part)],
    ['Type', label(part.family)],
  ];
  if (part.material) facts.push(['Material', part.material.split('; library materials')[0]]);
  if (part.datasheet)
    facts.push([
      'Datasheet',
      <a href={part.datasheet} target="_blank" rel="noopener noreferrer">
        Open <ExternalLink size={11} aria-hidden="true" />
      </a>,
    ]);

  return (
    <article className="mf-library-detail" data-testid="library-detail">
      <PartPreview part={part} />
      <header className="mf-library-detail-head">
        <h2>{describe(part)}</h2>
        {isEnding(part) && (
          <p className="mf-library-warning">
            <AlertTriangle size={13} aria-hidden="true" /> The research found this part being discontinued: check its
            status before designing it in.
          </p>
        )}
      </header>
      <dl className="mf-library-facts">
        {facts.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <Section title="Specs" count={specs} open>
        <SourcedTable values={part.specs} />
      </Section>
      <Section title="Suppliers" count={suppliers.length}>
        <ul className="mf-library-list-plain">
          {suppliers.map((s) => (
            <li key={`${s.supplier}-${s.sku}`}>
              {s.url ? (
                <a href={s.url} target="_blank" rel="noopener noreferrer">
                  {s.supplier} {s.sku}
                </a>
              ) : (
                `${s.supplier} ${s.sku}`
              )}
              {s.offer?.breaks?.length ? (
                <span className="font-mono mf-library-muted">
                  {' '}
                  {s.offer.breaks.map((b) => `${b.qty}+ ${s.offer?.currency ?? ''} ${b.unit_price}`).join(' · ')}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Model files" count={models.length}>
        <ul className="mf-library-list-plain">
          {models.map((m) => (
            <li key={`${m.model_id}-${m.version}`}>
              <p>{m.usage}</p>
              <p className="mf-library-muted">Licence: {m.artifacts[0]?.licence}</p>
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Notes and what is assumed">
        <pre className="mf-library-notes">{part.notes}</pre>
      </Section>
    </article>
  );
}

function Chip({
  on,
  onClick,
  children,
  title,
}: {
  on: boolean;
  onClick: () => void;
  children: ReactNode;
  title?: string;
}) {
  return (
    <button type="button" className="mf-library-chip" aria-pressed={on} onClick={onClick} title={title}>
      {children}
    </button>
  );
}

function ComponentsTab({ parts }: { parts: LibraryComponent[] }) {
  const [query, setQuery] = useState('');
  const [group, setGroup] = useState<Category | ''>('');
  const [only3d, setOnly3d] = useState(false);
  const [hideEnding, setHideEnding] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const groups = useMemo(() => {
    const counts = new Map<Category, number>();
    parts.forEach((p) => counts.set(categoryOf(p), (counts.get(categoryOf(p)) ?? 0) + 1));
    return CATEGORIES.filter((c) => counts.has(c)).map((c) => [c, counts.get(c) ?? 0] as const);
  }, [parts]);
  const shown = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return parts
      .filter((p) => !group || categoryOf(p) === group)
      .filter((p) => !only3d || hasModel(p))
      .filter((p) => !hideEnding || !isEnding(p))
      .filter((p) => {
        if (words.length === 0) return true;
        const hay = `${p.name} ${p.title} ${p.manufacturer ?? ''} ${p.mpn ?? ''} ${p.notes}`.toLowerCase();
        return words.every((w) => hay.includes(w));
      })
      .sort((a, b) => `${a.manufacturer} ${handle(a)}`.localeCompare(`${b.manufacturer} ${handle(b)}`));
  }, [parts, query, group, only3d, hideEnding]);
  const current = parts.find((p) => p.name === selected && shown.includes(p)) ?? shown[0] ?? null;

  return (
    <>
      <div className="mf-library-toolbar">
        <label className="mf-library-search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            aria-label="Search components"
            placeholder="Search parts, makers, specs"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="mf-library-chips" role="group" aria-label="Category">
          <Chip on={group === ''} onClick={() => setGroup('')}>
            All
          </Chip>
          {groups.map(([c, n]) => (
            <Chip key={c} on={group === c} onClick={() => setGroup(group === c ? '' : c)}>
              {c} <span className="mf-library-count">{n}</span>
            </Chip>
          ))}
        </div>
        <div className="mf-library-chips" role="group" aria-label="Show">
          <Chip on={only3d} onClick={() => setOnly3d(!only3d)} title="Only parts with a 3D model">
            <Box size={12} aria-hidden="true" /> 3D only
          </Chip>
          <Chip on={hideEnding} onClick={() => setHideEnding(!hideEnding)} title="Hide parts being discontinued">
            Hide discontinued
          </Chip>
        </div>
      </div>
      <div className="mf-library-split">
        <div className="mf-library-browser">
          <div className="mf-library-browser-head" data-testid="library-count">
            {shown.length === parts.length ? `${parts.length} parts` : `${shown.length} of ${parts.length}`}
          </div>
          <ul className="mf-library-rows" role="listbox" aria-label="Components">
            {shown.map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={current?.name === p.name}
                  className={current?.name === p.name ? 'is-selected' : undefined}
                  onClick={() => setSelected(p.name)}
                >
                  <span className="mf-library-row-line">
                    <span className="mf-library-row-title">{handle(p)}</span>
                    <span className="mf-library-row-icons">
                      {isEnding(p) && <AlertTriangle size={12} aria-label="being discontinued" className="is-warning" />}
                      {hasModel(p) && <Box size={12} aria-label="has a 3D model" />}
                    </span>
                  </span>
                  <span className="mf-library-row-meta">
                    {[p.manufacturer, describe(p) !== handle(p) ? describe(p) : label(p.family)]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
        {current ? (
          <ComponentDetail part={current} />
        ) : (
          <EmptyState title="No part matches" description="Clear the search or a filter." />
        )}
      </div>
    </>
  );
}

function MaterialsTab({ materials }: { materials: LibraryMaterial[] }) {
  return (
    <div className="mf-library-cards" data-testid="library-materials">
      {materials.map((m) => (
        <details key={m.id} className="mf-library-section">
          <summary>
            {m.title || m.name}
            <span className="mf-library-count">{Object.keys(m.properties || {}).length}</span>
          </summary>
          <div className="mf-library-section-body">
            <p className="mf-library-muted">{label(m.family)}</p>
            {Object.keys(m.properties || {}).length > 0 && <SourcedTable values={m.properties} />}
            <pre className="mf-library-notes">{m.notes}</pre>
          </div>
        </details>
      ))}
    </div>
  );
}

const KIND_NAMES: Record<string, string> = {
  fdm_printer: '3D printers',
  cnc_router: 'CNC routers',
  laser_cutter: 'Laser cutters',
};

function machineName(m: LibraryMachine): string {
  return m.layer === 'instance' ? m.name || m.id : `${m.make ?? ''} ${m.model ?? ''}`.trim();
}

function MachineCard({ machine, model }: { machine: LibraryMachine; model?: LibraryMachine }) {
  const own = machine.capabilities || {};
  const inherited = model?.capabilities || {};
  const total = new Set([...Object.keys(own), ...Object.keys(inherited)]).size;
  const facts: Array<[string, ReactNode]> = [['Kind', label(machine.kind)]];
  if (machine.processes?.length) facts.push(['Processes', machine.processes.map(label).join(', ')]);
  if (machine.layer === 'instance') {
    if (model) facts.push(['Model', machineName(model)]);
    if (machine.location) facts.push(['Location', machine.location]);
    if (machine.status) facts.push(['Status', label(machine.status)]);
    if (machine.route) facts.push(['Route', machine.route === 'in_house' ? 'In house' : 'Vendor']);
    if (machine.connection?.protocol && machine.connection.protocol !== 'none')
      facts.push(['Connection', label(machine.connection.protocol)]);
  }
  return (
    <details className="mf-library-section" data-testid="library-machine">
      <summary>
        {machineName(machine)}
        <span className="mf-library-count">{total}</span>
      </summary>
      <div className="mf-library-section-body">
        <dl className="mf-library-facts mf-library-facts-tight">
          {facts.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
        {total > 0 ? (
          <SourcedTable values={own} inherited={inherited} />
        ) : (
          <p className="mf-library-muted">No capabilities recorded yet.</p>
        )}
      </div>
    </details>
  );
}

function MachinesTab({ machines }: { machines: LibraryMachine[] }) {
  const byId = new Map(machines.map((m) => [m.id, m]));
  const bench = machines.filter((m) => m.layer === 'instance');
  const models = machines.filter((m) => m.layer === 'model');
  const kinds = [...new Set(models.map((m) => m.kind))].sort();
  return (
    <div className="mf-library-machines" data-testid="library-machines">
      {bench.length > 0 && (
        <section>
          <h3 className="mf-library-group-title">On the bench</h3>
          <div className="mf-library-cards">
            {bench.map((m) => (
              <MachineCard key={m.id} machine={m} model={m.model_id ? byId.get(m.model_id) : undefined} />
            ))}
          </div>
        </section>
      )}
      {kinds.map((k) => (
        <section key={k}>
          <h3 className="mf-library-group-title">{KIND_NAMES[k] ?? label(k)}</h3>
          <div className="mf-library-cards">
            {models
              .filter((m) => m.kind === k)
              .map((m) => (
                <MachineCard key={m.id} machine={m} />
              ))}
          </div>
        </section>
      ))}
    </div>
  );
}

/** The component, material and machine library: browse it, see each part in 3D, save or load it. */
export function LibraryPage() {
  const [tab, setTab] = useState<Tab>('components');
  const toast = useToast();
  const client = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const components = useQuery({ queryKey: ['library', 'components'], queryFn: listComponents });
  const materials = useQuery({ queryKey: ['library', 'materials'], queryFn: listMaterials });
  const machines = useQuery({ queryKey: ['library', 'machines'], queryFn: listMachines });

  async function onExport() {
    setBusy(true);
    try {
      const blob = await exportLibrary(true);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `metaforge-library-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error('The library could not be exported.');
    } finally {
      setBusy(false);
    }
  }

  async function onImport(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setBusy(true);
    try {
      const report = await importLibrary(JSON.parse(await file.text()));
      const added = Object.values(report.added).reduce((a, b) => a + b, 0);
      const skipped = Object.values(report.skipped).reduce((a, b) => a + b, 0);
      toast.success(
        `Imported ${added} entries; ${skipped} already here were left as they are` +
          (report.failed.length ? `; ${report.failed.length} could not be added.` : '.'),
      );
      await client.invalidateQueries({ queryKey: ['library'] });
    } catch {
      toast.error('That file is not a library export, or the gateway refused it.');
    } finally {
      setBusy(false);
    }
  }

  const parts = components.data ?? [];
  const counts: Record<Tab, number | undefined> = {
    components: components.data?.length,
    materials: materials.data?.length,
    machines: machines.data?.length,
  };

  return (
    <div className="mf-library">
      <PageHeader
        title="Library"
        actions={
          <>
            <Button variant="ghost" size="sm" onClick={onExport} disabled={busy} title="Save the whole library as a file">
              <Download size={14} aria-hidden="true" /> Export
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => fileInput.current?.click()}
              disabled={busy}
              title="Add the entries of a library file"
            >
              <Upload size={14} aria-hidden="true" /> Import
            </Button>
            <input
              ref={fileInput}
              type="file"
              accept="application/json,.json"
              hidden
              data-testid="library-import-file"
              onChange={onImport}
            />
          </>
        }
      />
      <div className="mf-library-tabs" role="tablist">
        {TABS.map(([t, name]) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
            {name}
            {counts[t] !== undefined && <span className="mf-library-count">{counts[t]}</span>}
          </button>
        ))}
      </div>
      {components.isError && (
        <EmptyState title="The library could not be read" description="Check that the gateway is running." />
      )}
      {!components.isLoading && !components.isError && tab === 'components' && parts.length === 0 && (
        <EmptyState title="The library is empty" description="Import a library file, or let an agent research parts." />
      )}
      {tab === 'components' && parts.length > 0 && <ComponentsTab parts={parts} />}
      {tab === 'materials' && <MaterialsTab materials={materials.data ?? []} />}
      {tab === 'machines' && <MachinesTab machines={machines.data ?? []} />}
    </div>
  );
}
