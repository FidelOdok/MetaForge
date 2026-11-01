import { useMemo, useState } from 'react';
import { parseCsv, type ParsedTable } from '../parsers/csv';
import { tokenize, type TokenKind } from '../parsers/highlight';
import { PC, type PreviewMode } from '../tokens';

// ── Tables (csv, bom) ───────────────────────────────────────────────────────

/** Rows rendered before "show all": a 10k-line CSV must not freeze the page. */
const ROW_LIMIT: Record<PreviewMode, number> = { compact: 8, panel: 25, modal: 500 };

function DataTable({ table, mode, numericCols, testId }: { table: ParsedTable; mode: PreviewMode; numericCols?: Set<number>; testId: string }) {
  const [all, setAll] = useState(false);
  const limit = ROW_LIMIT[mode];
  const rows = all ? table.rows : table.rows.slice(0, limit);

  if (table.header.length === 0) {
    return <p style={{ fontSize: 12, color: PC.onSurfaceVariant }}>This table is empty.</p>;
  }

  return (
    <div data-testid={testId}>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
          <thead>
            <tr>
              {table.header.map((h, i) => (
                <th key={i} style={{ textAlign: numericCols?.has(i) ? 'right' : 'left', padding: '5px 8px', borderBottom: `1px solid ${PC.borderMid}`, color: PC.onSurfaceVariant, fontWeight: 600, whiteSpace: 'nowrap', position: 'sticky', top: 0, background: PC.surface }}>
                  {h || `Column ${i + 1}`}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>
                {row.map((c, i) => (
                  <td key={i} className={numericCols?.has(i) ? 'font-mono' : undefined} style={{ padding: '4px 8px', borderBottom: `1px solid ${PC.border}`, color: PC.onSurface, textAlign: numericCols?.has(i) ? 'right' : 'left', verticalAlign: 'top' }}>
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-3 mt-2 font-mono" style={{ fontSize: 10, color: PC.onSurfaceVariant }}>
        <span>
          {table.rows.length} row{table.rows.length === 1 ? '' : 's'} · {table.header.length} column{table.header.length === 1 ? '' : 's'}
        </span>
        {table.rows.length > limit && (
          <button type="button" onClick={() => setAll((v) => !v)} style={{ background: 'transparent', border: 'none', color: PC.teal, cursor: 'pointer', fontFamily: 'inherit', fontSize: 10, padding: 0 }}>
            {all ? `Show first ${limit}` : `Show all ${table.rows.length}`}
          </button>
        )}
      </div>
    </div>
  );
}

function numericColumns(table: ParsedTable): Set<number> {
  const out = new Set<number>();
  table.header.forEach((_, i) => {
    const vals = table.rows.map((r) => r[i] ?? '').filter((v) => v !== '');
    if (vals.length > 0 && vals.every((v) => /^-?[$€£]?\d[\d,]*(\.\d+)?$/.test(v))) out.add(i);
  });
  return out;
}

export function CsvPreview({ source, format, mode }: { source: string; format: string; mode: PreviewMode }) {
  const table = useMemo(() => parseCsv(source, format === 'tsv' ? '\t' : undefined), [source, format]);
  const numeric = useMemo(() => numericColumns(table), [table]);
  return <DataTable table={table} mode={mode} numericCols={numeric} testId="preview-csv" />;
}

const QTY_HEADERS = /^(qty|quantity|count)$/i;
const REF_HEADERS = /^(ref|refs|reference|references|designator|designators|refdes)$/i;

/** A BOM is a CSV whose quantity column is worth summing and whose line count is the story. */
export function BomPreview({ source, mode }: { source: string; mode: PreviewMode }) {
  const table = useMemo(() => parseCsv(source), [source]);
  const numeric = useMemo(() => numericColumns(table), [table]);
  const qtyCol = table.header.findIndex((h) => QTY_HEADERS.test(h));
  const refCol = table.header.findIndex((h) => REF_HEADERS.test(h));
  const totalQty =
    qtyCol >= 0 ? table.rows.reduce((sum, r) => sum + (Number.parseFloat(r[qtyCol] ?? '') || 0), 0) : null;

  return (
    <div data-testid="preview-bom">
      <div className="flex gap-2 mb-2" style={{ flexWrap: 'wrap' }}>
        <Stat label="Line items" value={String(table.rows.length)} />
        {totalQty !== null && <Stat label="Total quantity" value={String(totalQty)} />}
        {refCol >= 0 && <Stat label="Designators" value={table.header[refCol] ?? ''} />}
      </div>
      <DataTable table={table} mode={mode} numericCols={numeric} testId="preview-bom-table" />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded px-2 py-1" style={{ background: PC.surfaceHigh }}>
      <div className="font-mono uppercase" style={{ fontSize: 9, letterSpacing: '0.1em', color: PC.onSurfaceVariant }}>{label}</div>
      <div className="font-mono" style={{ fontSize: 13, color: PC.onSurface }}>{value}</div>
    </div>
  );
}

// ── JSON tree ───────────────────────────────────────────────────────────────

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

function JsonValue({ value }: { value: Json }) {
  if (value === null) return <span style={{ color: PC.onSurfaceVariant }}>null</span>;
  if (typeof value === 'string') return <span style={{ color: PC.green }}>&quot;{value}&quot;</span>;
  if (typeof value === 'number') return <span style={{ color: PC.teal }}>{value}</span>;
  if (typeof value === 'boolean') return <span style={{ color: PC.amber }}>{String(value)}</span>;
  return null;
}

function JsonNode({ name, value, depth, openDepth }: { name?: string; value: Json; depth: number; openDepth: number }) {
  const isContainer = value !== null && typeof value === 'object';
  const [open, setOpen] = useState(depth < openDepth);
  const label = name !== undefined && <span style={{ color: PC.onSurface }}>{name}: </span>;
  if (!isContainer) {
    return (
      <div style={{ paddingLeft: 14 }}>
        {label}
        <JsonValue value={value} />
      </div>
    );
  }
  const entries: [string, Json][] = Array.isArray(value)
    ? value.map((v, i) => [String(i), v])
    : Object.entries(value);
  const summary = Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`;
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="font-mono"
        style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'inherit', fontSize: 'inherit', padding: 0, textAlign: 'left' }}
      >
        <span className="material-symbols-outlined" style={{ fontSize: 13, verticalAlign: 'middle', color: PC.onSurfaceVariant }}>
          {open ? 'expand_more' : 'chevron_right'}
        </span>
        {label}
        <span style={{ color: PC.onSurfaceVariant }}>{summary}</span>
      </button>
      {open && (
        <div style={{ paddingLeft: 14, borderLeft: `1px solid ${PC.border}`, marginLeft: 6 }}>
          {entries.map(([k, v]) => (
            <JsonNode key={k} name={k} value={v} depth={depth + 1} openDepth={openDepth} />
          ))}
        </div>
      )}
    </div>
  );
}

/** Collapsible JSON. `source` may be a JSON string or an already-parsed value. */
export function JsonTree({ source, value, openDepth = 2 }: { source?: string; value?: unknown; openDepth?: number }) {
  const parsed = useMemo((): { ok: true; value: Json } | { ok: false } => {
    if (value !== undefined) return { ok: true, value: value as Json };
    try {
      return { ok: true, value: JSON.parse(source ?? '') as Json };
    } catch {
      return { ok: false };
    }
  }, [source, value]);

  if (!parsed.ok) {
    return (
      <div data-testid="preview-json-invalid">
        <p style={{ fontSize: 12, color: PC.amber, margin: '0 0 8px' }}>This file is not valid JSON, so it is shown as text.</p>
        <CodeView source={source ?? ''} language="" />
      </div>
    );
  }
  return (
    <div data-testid="preview-json" className="font-mono" style={{ fontSize: 12, lineHeight: 1.6, color: PC.onSurfaceVariant }}>
      <JsonNode value={parsed.value} depth={0} openDepth={openDepth} />
    </div>
  );
}

// ── Code ────────────────────────────────────────────────────────────────────

const TOKEN_COLORS: Record<TokenKind, string | undefined> = {
  comment: 'var(--mf-c-9a9aaa)',
  string: 'var(--mf-c-3dd68c)',
  keyword: 'var(--mf-c-ffb783)',
  number: 'var(--mf-c-86cfff)',
  preproc: 'var(--mf-c-f5b04d)',
  type: 'var(--mf-c-86cfff)',
  plain: undefined,
};

export function CodeView({ source, language }: { source: string; language: string }) {
  const lines = useMemo(() => {
    const tokens = tokenize(source, language);
    // Split tokens on newlines so each line gets a gutter number.
    const out: { kind: TokenKind; text: string }[][] = [[]];
    for (const t of tokens) {
      const parts = t.text.split('\n');
      parts.forEach((p, i) => {
        if (i > 0) out.push([]);
        if (p) out[out.length - 1]?.push({ kind: t.kind, text: p });
      });
    }
    return out;
  }, [source, language]);

  return (
    <pre
      data-testid="preview-code"
      data-language={language || 'text'}
      className="font-mono"
      style={{ margin: 0, padding: '12px 0', fontSize: 12, lineHeight: 1.6, color: PC.onSurface, background: PC.surfaceLow, border: `1px solid ${PC.border}`, borderRadius: 6, overflowX: 'auto' }}
    >
      {lines.map((line, n) => (
        <div key={n} style={{ display: 'flex' }}>
          <span aria-hidden style={{ width: 44, flexShrink: 0, textAlign: 'right', paddingRight: 12, color: 'var(--mf-c-33343b)', userSelect: 'none' }}>
            {n + 1}
          </span>
          <span style={{ whiteSpace: 'pre', paddingRight: 16 }}>
            {line.map((t, i) => (
              <span key={i} data-token={t.kind === 'plain' ? undefined : t.kind} style={{ color: TOKEN_COLORS[t.kind], fontStyle: t.kind === 'comment' ? 'italic' : undefined }}>
                {t.text}
              </span>
            ))}
            {line.length === 0 && ' '}
          </span>
        </div>
      ))}
    </pre>
  );
}
