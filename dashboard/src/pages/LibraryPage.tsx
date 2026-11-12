import { useMemo, useRef, useState, type ChangeEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Upload } from 'lucide-react';
import { PageHeader } from '../components/ui/PageHeader';
import { EmptyState } from '../components/ui/EmptyState';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { useToast } from '../components/ui/Toast';
import { PartPreview, activeMakerModel } from '../components/library/PartPreview';
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

function cleanTitle(title: string): string {
  return title.replace(ENDING, '');
}

function hasModel(c: LibraryComponent): boolean {
  return Boolean(activeMakerModel(c) || c.form_code);
}

function formatValue(v: SourcedValue): string {
  const value = Array.isArray(v.value) ? v.value.join(' x ') : String(v.value);
  return `${value} ${v.unit}`;
}

function SourcedTable({ values, label }: { values: Record<string, SourcedValue>; label: string }) {
  const rows = Object.entries(values || {});
  if (rows.length === 0) return <p className="mf-library-muted">No typed {label} yet.</p>;
  return (
    <table className="mf-library-table">
      <tbody>
        {rows.map(([key, v]) => (
          <tr key={key}>
            <td>{key.replace(/_/g, ' ')}</td>
            <td className="font-mono">{formatValue(v)}</td>
            <td className="mf-library-muted">
              {v.source_ref?.startsWith('http') ? (
                <a href={v.source_ref.split(' ')[0]} target="_blank" rel="noopener noreferrer">
                  {v.source_kind}
                </a>
              ) : (
                v.source_kind
              )}
              {v.needs_review ? ' (needs review)' : ''}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ComponentDetail({ part }: { part: LibraryComponent }) {
  const models = (part.models || []).filter((m) => (m.status ?? 'active') === 'active');
  return (
    <article className="mf-library-detail" data-testid="library-detail">
      <header>
        <h2>{cleanTitle(part.title || part.name)}</h2>
        <p className="mf-library-muted">
          {[part.manufacturer, part.mpn].filter(Boolean).join(' · ')} · v{part.version} ·{' '}
          <code>{part.name}</code>
        </p>
        <div className="mf-library-badges">
          <Badge>{part.family}</Badge>
          {ENDING.test(part.title) && <Badge variant="warning">being discontinued: check status</Badge>}
          {activeMakerModel(part) && <Badge variant="info">maker model</Badge>}
        </div>
      </header>
      <PartPreview part={part} />
      {part.material && (
        <section>
          <h3>Material</h3>
          <p>{part.material}</p>
        </section>
      )}
      <section>
        <h3>Specs</h3>
        <SourcedTable values={part.specs} label="specs" />
      </section>
      {(part.suppliers || []).length > 0 && (
        <section>
          <h3>Suppliers</h3>
          <ul className="mf-library-list-plain">
            {(part.suppliers || []).map((s) => (
              <li key={`${s.supplier}-${s.sku}`}>
                {s.url ? (
                  <a href={s.url} target="_blank" rel="noopener noreferrer">
                    {s.supplier} {s.sku}
                  </a>
                ) : (
                  `${s.supplier} ${s.sku}`
                )}
                {s.offer?.breaks?.length ? (
                  <span className="font-mono">
                    {' '}
                    {s.offer.breaks.map((b) => `${b.qty}+: ${s.offer?.currency ?? ''} ${b.unit_price}`).join(', ')}
                  </span>
                ) : null}
                {s.offer?.read_on && <span className="mf-library-muted"> (read {s.offer.read_on})</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
      {models.length > 0 && (
        <section>
          <h3>Model files</h3>
          <ul className="mf-library-list-plain">
            {models.map((m) => (
              <li key={`${m.model_id}-${m.version}`}>
                {m.model_id} v{m.version} ({m.engine}): {m.usage}
                <span className="mf-library-muted"> Licence: {m.artifacts[0]?.licence}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {part.datasheet && (
        <p>
          <a href={part.datasheet} target="_blank" rel="noopener noreferrer">
            Datasheet
          </a>
        </p>
      )}
      <details>
        <summary>Notes and what is assumed</summary>
        <pre className="mf-library-notes">{part.notes}</pre>
      </details>
    </article>
  );
}

function ComponentsTab({ parts }: { parts: LibraryComponent[] }) {
  const [query, setQuery] = useState('');
  const [family, setFamily] = useState('');
  const [only3d, setOnly3d] = useState(false);
  const [hideEnding, setHideEnding] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const families = useMemo(() => [...new Set(parts.map((p) => p.family))].sort(), [parts]);
  const shown = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return parts
      .filter((p) => !family || p.family === family)
      .filter((p) => !only3d || hasModel(p))
      .filter((p) => !hideEnding || !ENDING.test(p.title))
      .filter((p) => {
        if (words.length === 0) return true;
        const hay = `${p.name} ${p.title} ${p.manufacturer ?? ''} ${p.mpn ?? ''} ${p.notes}`.toLowerCase();
        return words.every((w) => hay.includes(w));
      })
      .sort((a, b) => cleanTitle(a.title).localeCompare(cleanTitle(b.title)));
  }, [parts, query, family, only3d, hideEnding]);
  const current = parts.find((p) => p.name === selected) ?? shown[0] ?? null;

  return (
    <div className="mf-library-split">
      <div className="mf-library-browser">
        <div className="mf-library-filters">
          <input
            type="search"
            aria-label="Search components"
            placeholder="Search maker, part, specs, notes"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select aria-label="Type" value={family} onChange={(e) => setFamily(e.target.value)}>
            <option value="">All types</option>
            {families.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
          <label>
            <input type="checkbox" checked={only3d} onChange={(e) => setOnly3d(e.target.checked)} /> Only with 3D
          </label>
          <label>
            <input type="checkbox" checked={hideEnding} onChange={(e) => setHideEnding(e.target.checked)} /> Hide
            discontinued
          </label>
          <span className="mf-library-muted font-mono" data-testid="library-count">
            {shown.length} of {parts.length}
          </span>
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
                <span className="mf-library-row-title">{cleanTitle(p.title || p.name)}</span>
                <span className="mf-library-row-meta">
                  {[p.manufacturer, p.family].filter(Boolean).join(' · ')}
                  {hasModel(p) ? ' · 3D' : ''}
                  {ENDING.test(p.title) ? ' · discontinued?' : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      {current ? <ComponentDetail part={current} /> : <EmptyState title="No component matches" description="Clear the search or the filters." />}
    </div>
  );
}

function MaterialsTab({ materials }: { materials: LibraryMaterial[] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <ul className="mf-library-cards" data-testid="library-materials">
      {materials.map((m) => (
        <li key={m.id}>
          <button type="button" onClick={() => setOpen(open === m.name ? null : m.name)} aria-expanded={open === m.name}>
            <span className="mf-library-row-title">{m.title || m.name}</span>
            <span className="mf-library-row-meta">
              {m.family} · {Object.keys(m.properties || {}).length} sourced properties
            </span>
          </button>
          {open === m.name && (
            <div className="mf-library-card-body">
              <SourcedTable values={m.properties} label="properties" />
              <pre className="mf-library-notes">{m.notes}</pre>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

function MachinesTab({ machines }: { machines: LibraryMachine[] }) {
  return (
    <table className="mf-library-table" data-testid="library-machines">
      <thead>
        <tr>
          <th>Machine</th>
          <th>Kind</th>
          <th>Layer</th>
          <th>Processes</th>
        </tr>
      </thead>
      <tbody>
        {machines.map((m) => (
          <tr key={m.id}>
            <td>{m.layer === 'instance' ? m.name : `${m.make ?? ''} ${m.model ?? ''}`}</td>
            <td>{m.kind}</td>
            <td>{m.layer}</td>
            <td>{(m.processes || []).join(', ')}</td>
          </tr>
        ))}
      </tbody>
    </table>
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

  const loading = components.isLoading;
  const parts = components.data ?? [];
  const counts = `${parts.length} components · ${materials.data?.length ?? 0} materials · ${machines.data?.length ?? 0} machines`;

  return (
    <div className="mf-library">
      <PageHeader
        title="Library"
        meta={counts}
        description="Bought parts, materials and machines the agents design with. Every value carries its source."
        actions={
          <>
            <Button variant="secondary" size="sm" onClick={onExport} disabled={busy}>
              <Download size={14} aria-hidden="true" /> Export
            </Button>
            <Button variant="secondary" size="sm" onClick={() => fileInput.current?.click()} disabled={busy}>
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
        {TABS.map(([t, label]) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
            {label}
          </button>
        ))}
      </div>
      {components.isError && (
        <EmptyState title="The library could not be read" description="Check that the gateway is running." />
      )}
      {!loading && !components.isError && tab === 'components' && parts.length === 0 && (
        <EmptyState title="The library is empty" description="Import a library export, or let an agent research parts." />
      )}
      {tab === 'components' && parts.length > 0 && <ComponentsTab parts={parts} />}
      {tab === 'materials' && <MaterialsTab materials={materials.data ?? []} />}
      {tab === 'machines' && <MachinesTab machines={machines.data ?? []} />}
    </div>
  );
}
