import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { fetchNodeNamedFileText, nodeNamedFileUrl } from '../../../api/endpoints/twin';
import type { DesignFilesView as DesignFiles, TwinNode } from '../../../types/twin';
import { gerberToSvg, unionViewBox, withViewBox } from '../parsers/gerberToSvg';
import { PC, type PreviewMode } from '../tokens';
import { CodeView, CsvPreview, JsonTree } from './DataEngines';

/**
 * FORGE-687: a work product kept by twin.record_design_files, shown the way
 * its files are meant to be read.
 *
 * - schematic / pcb_layout: the SVGs kicad.export_svg made when it was
 *   recorded (one per sheet, or the board's top side).
 * - gerber: every layer drawn by tracespace in one shared box, so the stack
 *   lines up; each layer can be switched off.
 * - firmware_source: the project's files, read one at a time, with the build
 *   output to download.
 * - pick_and_place / pinmap: the file as a table.
 *
 * Every SVG is shown through an <img>, so nothing in a file can run script.
 */

const TYPE_LABELS: Record<string, string> = {
  schematic: 'Schematic',
  pcb_layout: 'Board layout',
  gerber: 'Gerber set',
  pick_and_place: 'Pick and place',
  pinmap: 'Pin map',
  firmware_source: 'Firmware project',
};

/** Layer colours on the dark canvas, drawn bottom to top in this order. */
const LAYER_STYLE: Record<string, { color: string; opacity: number; order: number }> = {
  outline: { color: '#9aa4b2', opacity: 1, order: 0 },
  copper: { color: '#e8b04a', opacity: 0.85, order: 1 },
  soldermask: { color: '#3dd68c', opacity: 0.35, order: 2 },
  solderpaste: { color: '#c0c0c8', opacity: 0.6, order: 3 },
  silkscreen: { color: '#f2f2f2', opacity: 0.9, order: 4 },
  drill: { color: '#0a0b0f', opacity: 1, order: 5 },
};

function fmtBytes(n: number | undefined): string {
  if (n === undefined) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function useNamedText(nodeId: string, path: string | null) {
  const [state, setState] = useState<{ text: string | null; error: string | null; loading: boolean }>({
    text: null,
    error: null,
    loading: path !== null,
  });
  useEffect(() => {
    if (path === null) return;
    let cancelled = false;
    setState({ text: null, error: null, loading: true });
    fetchNodeNamedFileText(nodeId, path)
      .then((text) => !cancelled && setState({ text, error: null, loading: false }))
      .catch(() => !cancelled && setState({ text: null, error: `Could not read ${path}.`, loading: false }));
    return () => {
      cancelled = true;
    };
  }, [nodeId, path]);
  return state;
}

function Note({ children }: { children: ReactNode }) {
  return (
    <div className="font-mono" style={{ fontSize: 12, color: PC.onSurfaceVariant, padding: 12 }}>
      {children}
    </div>
  );
}

function Tabs({ items, active, onPick }: { items: string[]; active: string; onPick: (v: string) => void }) {
  if (items.length < 2) return null;
  return (
    <div role="tablist" className="flex flex-wrap gap-1" style={{ marginBottom: 8 }}>
      {items.map((item) => (
        <button
          key={item}
          type="button"
          role="tab"
          aria-selected={item === active}
          onClick={() => onPick(item)}
          className="font-mono"
          style={{
            fontSize: 11,
            padding: '3px 8px',
            borderRadius: 4,
            cursor: 'pointer',
            border: `1px solid ${item === active ? PC.orange : PC.border}`,
            background: item === active ? PC.orangeFaint : 'transparent',
            color: PC.onSurface,
          }}
        >
          {item.split('/').pop()}
        </button>
      ))}
    </div>
  );
}

function Sheets({ node, files, mode }: { node: TwinNode; files: DesignFiles; mode: PreviewMode }) {
  // FORGE-710: which sheet is picked has to be derived from the node in hand,
  // not just seeded from it. Selecting a node whose data react-query already
  // holds re-renders this component instead of remounting it, so a plain
  // useState initialiser keeps the sheet picked for the PREVIOUS node -- and
  // the url is built from the new node's id with that old path, which 404s
  // and leaves the browser showing the alt text.
  const [picked, setPicked] = useState<string | null>(null);
  const active = picked !== null && files.previews.includes(picked) ? picked : (files.previews[0] ?? '');
  if (!files.previews.length) {
    return (
      <Note>
        No preview was made when this was recorded
        {files.previewError ? `: ${files.previewError}` : '.'} The files are below.
      </Note>
    );
  }
  const url = nodeNamedFileUrl(node.id, active);
  return (
    <div data-testid="design-sheets">
      <Tabs items={files.previews} active={active} onPick={setPicked} />
      <a href={url} target="_blank" rel="noreferrer" title="Open full size">
        <img
          src={url}
          alt={`${node.name}: ${active.split('/').pop()}`}
          style={{
            width: '100%',
            height: mode === 'modal' ? '70vh' : mode === 'compact' ? 200 : 260,
            objectFit: 'contain',
            background: '#f5f4ef',
            borderRadius: 4,
            border: `1px solid ${PC.border}`,
          }}
        />
      </a>
    </div>
  );
}

interface DrawnLayer {
  path: string;
  layer: string;
  svg: string | null;
  box: [number, number, number, number] | null;
}

function GerberStack({ node, files, mode }: { node: TwinNode; files: DesignFiles; mode: PreviewMode }) {
  const drawable = useMemo(
    () =>
      files.layers
        .filter((l) => l.layer !== 'job' && l.layer !== 'other')
        .sort((a, b) => (LAYER_STYLE[a.layer]?.order ?? 9) - (LAYER_STYLE[b.layer]?.order ?? 9)),
    [files.layers],
  );
  const [drawn, setDrawn] = useState<DrawnLayer[] | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    let cancelled = false;
    // FORGE-710: drop the layers already drawn before fetching this node's,
    // or a switch between two gerber sets shows the previous board until the
    // new one finishes drawing.
    setDrawn(null);
    Promise.all(
      drawable.map(async (l): Promise<DrawnLayer> => {
        try {
          const text = await fetchNodeNamedFileText(node.id, l.path);
          const out = gerberToSvg(text, LAYER_STYLE[l.layer]?.color);
          return { ...l, svg: out.empty ? null : out.svg, box: out.empty ? null : out.viewBox };
        } catch {
          return { ...l, svg: null, box: null };
        }
      }),
    ).then((layers) => !cancelled && setDrawn(layers));
    return () => {
      cancelled = true;
    };
  }, [node.id, drawable]);

  if (!drawable.length) return <Note>No gerber layers in this set.</Note>;
  if (drawn === null) return <Note>Drawing {drawable.length} layers…</Note>;
  const box = unionViewBox(drawn.map((d) => d.box));
  if (!box) return <Note>The Gerber renderer could not draw any layer of this set.</Note>;
  const toggle = (path: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  return (
    <div data-testid="design-gerber-stack" className={mode === 'modal' ? 'flex gap-4' : undefined}>
      <div
        style={{
          position: 'relative',
          flex: 1,
          height: mode === 'modal' ? '70vh' : mode === 'compact' ? 200 : 260,
          background: '#0a0b0f',
          borderRadius: 4,
          border: `1px solid ${PC.border}`,
        }}
      >
        {drawn.map((d) =>
          d.svg && !hidden.has(d.path) ? (
            <img
              key={d.path}
              alt={d.path}
              src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(withViewBox(d.svg, box))}`}
              style={{
                position: 'absolute',
                inset: 8,
                width: 'calc(100% - 16px)',
                height: 'calc(100% - 16px)',
                objectFit: 'contain',
                opacity: LAYER_STYLE[d.layer]?.opacity ?? 0.8,
                pointerEvents: 'none',
              }}
            />
          ) : null,
        )}
      </div>
      <ul className="font-mono" style={{ fontSize: 11, listStyle: 'none', padding: 0, margin: mode === 'modal' ? 0 : '8px 0 0', minWidth: 220 }}>
        {drawn.map((d) => (
          <li key={d.path} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '2px 0' }}>
            <input
              type="checkbox"
              aria-label={`Show ${d.path}`}
              checked={!hidden.has(d.path)}
              disabled={!d.svg}
              onChange={() => toggle(d.path)}
            />
            <span style={{ width: 10, height: 10, borderRadius: 2, background: LAYER_STYLE[d.layer]?.color ?? '#888' }} />
            <span style={{ color: d.svg ? PC.onSurface : PC.onSurfaceVariant }}>{d.path}</span>
            <span style={{ color: PC.onSurfaceVariant }}>{d.svg ? d.layer : 'not drawable'}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const CODE_LANG: Record<string, string> = { c: 'c', h: 'c', cpp: 'cpp', hpp: 'cpp', cc: 'cpp', ino: 'cpp', s: 'asm' };

function FirmwareProject({ node, files, mode }: { node: TwinNode; files: DesignFiles; mode: PreviewMode }) {
  const outputs = new Set(files.buildOutputs);
  const sources = files.files.map((f) => f.path).filter((p) => !outputs.has(p));
  // FORGE-710: same hazard as Sheets above -- moving between two firmware
  // nodes without a remount would otherwise read the previous project's path
  // out of the new node.
  const [picked, setPicked] = useState<string | null>(null);
  const active = picked !== null && sources.includes(picked) ? picked : (sources[0] ?? null);
  const file = useNamedText(node.id, active);
  const ext = (active ?? '').split('.').pop()?.toLowerCase() ?? '';
  return (
    <div data-testid="design-firmware" className={mode === 'modal' ? 'flex gap-4' : undefined}>
      <ul className="font-mono" style={{ fontSize: 11, listStyle: 'none', padding: 0, margin: 0, minWidth: 200 }}>
        {sources.map((p) => (
          <li key={p}>
            <button
              type="button"
              onClick={() => setPicked(p)}
              style={{
                background: p === active ? PC.orangeFaint : 'transparent',
                color: PC.onSurface,
                border: 'none',
                padding: '2px 6px',
                cursor: 'pointer',
                textAlign: 'left',
                width: '100%',
              }}
            >
              {p}
            </button>
          </li>
        ))}
        {files.buildOutputs.length > 0 && (
          <li style={{ marginTop: 8, color: PC.onSurfaceVariant }}>
            Build output
            {files.buildOutputs.map((p) => (
              <a key={p} href={nodeNamedFileUrl(node.id, p)} download style={{ display: 'block', color: PC.teal }}>
                {p.split('/').pop()}
              </a>
            ))}
          </li>
        )}
      </ul>
      <div style={{ flex: 1, minWidth: 0, maxHeight: mode === 'modal' ? '70vh' : 260, overflow: 'auto' }}>
        {file.loading ? (
          <Note>Loading…</Note>
        ) : file.error || file.text === null ? (
          <Note>{file.error ?? 'Pick a file.'}</Note>
        ) : (
          <CodeView source={file.text} language={CODE_LANG[ext] ?? ''} />
        )}
      </div>
    </div>
  );
}

function Table({ node, files, mode }: { node: TwinNode; files: DesignFiles; mode: PreviewMode }) {
  const path = files.files[0]?.path ?? null;
  const file = useNamedText(node.id, path);
  if (file.loading) return <Note>Loading…</Note>;
  if (file.error || file.text === null) return <Note>{file.error ?? 'No file.'}</Note>;
  const low = path?.toLowerCase() ?? '';
  if (low.endsWith('.json')) return <JsonTree source={file.text} />;
  // KiCad's ASCII .pos is space-aligned columns, readable as it is.
  if (!low.endsWith('.csv')) return <CodeView source={file.text} language="" />;
  return <CsvPreview source={file.text} format="csv" mode={mode} />;
}

function FileList({ node, files }: { node: TwinNode; files: DesignFiles }) {
  return (
    <details style={{ marginTop: 10 }}>
      <summary className="font-mono" style={{ fontSize: 11, color: PC.onSurfaceVariant, cursor: 'pointer' }}>
        {files.files.length} file{files.files.length === 1 ? '' : 's'}
      </summary>
      <ul className="font-mono" style={{ fontSize: 11, listStyle: 'none', padding: '4px 0 0', margin: 0 }}>
        {files.files.map((f) => (
          <li key={f.path} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
            <a href={nodeNamedFileUrl(node.id, f.path)} download style={{ color: PC.teal }}>
              {f.path}
            </a>
            <span style={{ color: PC.onSurfaceVariant }}>{fmtBytes(f.bytes)}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

export function DesignFilesView({ node, mode }: { node: TwinNode; mode: PreviewMode }) {
  const files = node.designFiles;
  if (!files) return <Note>This work product has no design files.</Note>;
  const wpType = String(node.properties.wp_type ?? '');
  const total = files.files.reduce((n, f) => n + (f.bytes ?? 0), 0);
  const ref = node.properties.item_key
    ? `${node.properties.item_key}@${node.properties.item_revision ?? '?'}`
    : '';

  let body: ReactNode;
  if (wpType === 'schematic' || wpType === 'pcb_layout') body = <Sheets node={node} files={files} mode={mode} />;
  else if (wpType === 'gerber') body = <GerberStack node={node} files={files} mode={mode} />;
  else if (wpType === 'firmware_source') body = <FirmwareProject node={node} files={files} mode={mode} />;
  else body = <Table node={node} files={files} mode={mode} />;

  return (
    <div data-testid="preview-design-files" data-wp-type={wpType}>
      <div className="font-mono flex flex-wrap gap-3" style={{ fontSize: 11, color: PC.onSurfaceVariant, marginBottom: 8 }}>
        <strong style={{ color: PC.onSurface }}>{TYPE_LABELS[wpType] ?? wpType}</strong>
        {ref && <span>{ref}</span>}
        <span>
          {files.files.length} file{files.files.length === 1 ? '' : 's'}, {fmtBytes(total)}
        </span>
        {files.sources.length > 0 && <span>made from {files.sources.length}</span>}
      </div>
      {body}
      {mode !== 'compact' && <FileList node={node} files={files} />}
    </div>
  );
}
