import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { listSources } from '../api/endpoints/knowledge';
import { useProjects } from '../hooks/use-projects';
import { formatRelativeTime } from '../utils/format-time';
import type { KnowledgeType, SourceSummary } from '../types/knowledge';

// ---------------------------------------------------------------------------
// Knowledge-type chip styling — pulled from the Kinetic Console palette so
// chips read as a coherent set against the dark surface.
// ---------------------------------------------------------------------------

const TYPE_CHIP: Record<KnowledgeType, { color: string; bg: string }> = {
  design_decision: { color: '#86cfff', bg: 'rgba(134,207,255,0.1)' },
  component:       { color: '#3dd68c', bg: 'rgba(61,214,140,0.1)'  },
  failure:         { color: '#ffb4ab', bg: 'rgba(255,180,171,0.1)' },
  constraint:      { color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' },
  session:         { color: '#ffb783', bg: 'rgba(230,126,34,0.12)' },
  other:           { color: '#9a9aaa', bg: 'rgba(154,154,170,0.1)' },
};

const KNOWLEDGE_TYPES: ReadonlyArray<KnowledgeType> = [
  'design_decision',
  'component',
  'failure',
  'constraint',
  'session',
  'other',
];

// Sort helper — supports the four sortable columns.
type SortKey = 'source_path' | 'knowledge_type' | 'fragment_count' | 'indexed_at';
type SortDir = 'asc' | 'desc';

function compareSources(a: SourceSummary, b: SourceSummary, key: SortKey, dir: SortDir): number {
  const sign = dir === 'asc' ? 1 : -1;
  switch (key) {
    case 'source_path':
      return sign * a.source_path.localeCompare(b.source_path);
    case 'knowledge_type':
      return sign * (a.knowledge_type ?? '').localeCompare(b.knowledge_type ?? '');
    case 'fragment_count':
      return sign * (a.fragment_count - b.fragment_count);
    case 'indexed_at': {
      const ta = new Date(a.indexed_at).getTime() || 0;
      const tb = new Date(b.indexed_at).getTime() || 0;
      return sign * (ta - tb);
    }
  }
}

// ---------------------------------------------------------------------------
// Knowledge type chip
// ---------------------------------------------------------------------------

function TypeChip({ type }: { type: KnowledgeType | null }) {
  const key: KnowledgeType = type ?? 'other';
  const { color, bg } = TYPE_CHIP[key];
  return (
    <span
      style={{
        fontFamily: 'monospace',
        fontSize: 10,
        color,
        background: bg,
        padding: '2px 6px',
        borderRadius: 3,
        flexShrink: 0,
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
        whiteSpace: 'nowrap',
      }}
    >
      {key.replace(/_/g, ' ')}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Sort header cell
// ---------------------------------------------------------------------------

interface SortHeaderProps {
  label: string;
  field: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  align?: 'left' | 'right';
  width?: string | number;
}

function SortHeader({ label, field, sortKey, sortDir, onSort, align = 'left', width }: SortHeaderProps) {
  const active = sortKey === field;
  return (
    <button
      type="button"
      onClick={() => onSort(field)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        justifyContent: align === 'right' ? 'flex-end' : 'flex-start',
        width,
        background: 'transparent',
        border: 'none',
        cursor: 'pointer',
        fontFamily: 'monospace',
        fontSize: 10,
        textTransform: 'uppercase',
        letterSpacing: '0.07em',
        color: active ? '#e2e2eb' : '#9a9aaa',
        padding: 0,
        textAlign: align,
      }}
    >
      <span>{label}</span>
      {active && (
        <span className="material-symbols-outlined" style={{ fontSize: 12 }} aria-hidden="true">
          {sortDir === 'asc' ? 'arrow_upward' : 'arrow_downward'}
        </span>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const GLASS: React.CSSProperties = {
  background: 'rgba(30,31,38,0.85)',
  backdropFilter: 'blur(16px)',
  border: '1px solid rgba(65,72,90,0.2)',
  borderRadius: 4,
};

function metadataField(metadata: Record<string, unknown>, key: string): string {
  const value = metadata[key];
  if (value === undefined || value === null || value === '') return '—';
  return String(value);
}

export function KnowledgePage() {
  const navigate = useNavigate();
  const [filterType, setFilterType] = useState<KnowledgeType | 'all'>('all');
  // MET-452: project dropdown replaced the UUID-paste input. ``''`` is
  // the sentinel for "All projects (default tenant)" — anything else
  // is a real project UUID picked from useProjects().
  const [projectId, setProjectId] = useState<string>('');
  const { data: projects } = useProjects();
  const [sortKey, setSortKey] = useState<SortKey>('indexed_at');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  // Project options, sorted newest-first so the most-recently-updated
  // project sits at the top of the dropdown and gets auto-selected
  // (matches what users typically want — fresh ingest goes into the
  // newest project, default tenant is the legacy fallback).
  const projectOptions = useMemo(() => {
    if (!projects) return [];
    return [...projects].sort((a, b) => {
      const ta = new Date(a.lastUpdated).getTime() || 0;
      const tb = new Date(b.lastUpdated).getTime() || 0;
      return tb - ta;
    });
  }, [projects]);

  // Auto-select the most-recently-updated project on first load *only*
  // if the user hasn't already picked one. We use a guard ref-pattern
  // via the state initialiser so flipping back to "All projects" after
  // load stays sticky for the rest of the session.
  const [autoSelected, setAutoSelected] = useState(false);
  useEffect(() => {
    if (autoSelected) return;
    const newest = projectOptions[0];
    if (!newest) return;
    setProjectId(newest.id);
    setAutoSelected(true);
  }, [autoSelected, projectOptions]);

  // The filter chip pushes ``knowledge_type`` to the server so we don't
  // pull rows we'll just discard; the project_id filter does the same.
  const { data: sources, isLoading } = useQuery({
    queryKey: ['knowledge', 'sources', filterType, projectId.trim()],
    queryFn: () =>
      listSources({
        knowledge_type: filterType === 'all' ? undefined : filterType,
        project_id: projectId.trim() ? projectId.trim() : undefined,
      }),
    staleTime: 30_000,
  });

  const sortedSources = useMemo(() => {
    if (!sources) return [];
    return [...sources].sort((a, b) => compareSources(a, b, sortKey, sortDir));
  }, [sources, sortKey, sortDir]);

  function handleSort(field: SortKey) {
    if (field === sortKey) {
      setSortDir((dir) => (dir === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(field);
      setSortDir(field === 'fragment_count' || field === 'indexed_at' ? 'desc' : 'asc');
    }
  }

  function handleRowClick(source: SourceSummary) {
    navigate(`/knowledge/sources/${encodeURIComponent(source.source_path)}`);
  }

  const total = sources?.length ?? 0;

  return (
    <div>
      {/* ── Page header ─────────────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 12,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span style={{ fontSize: 18, fontWeight: 500, color: '#e8e8ed' }}>Knowledge</span>
          <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#9a9aaa' }}>
            {total} {total === 1 ? 'source' : 'sources'} · L1 corpus
          </span>
        </div>
      </div>

      {/* ── Filter row: type chips + project_id input ───────────────────── */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
          <button
            type="button"
            onClick={() => setFilterType('all')}
            aria-pressed={filterType === 'all'}
            style={{
              fontFamily: 'monospace',
              fontSize: 10,
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              padding: '4px 10px',
              borderRadius: 4,
              border: 'none',
              cursor: 'pointer',
              background: filterType === 'all' ? '#e67e22' : 'rgba(30,31,38,0.85)',
              color: filterType === 'all' ? '#000' : '#9a9aaa',
              transition: 'background 0.15s, color 0.15s',
            }}
          >
            all
          </button>
          {KNOWLEDGE_TYPES.map((kt) => {
            const active = filterType === kt;
            return (
              <button
                key={kt}
                type="button"
                onClick={() => setFilterType(kt)}
                aria-pressed={active}
                style={{
                  fontFamily: 'monospace',
                  fontSize: 10,
                  textTransform: 'uppercase',
                  letterSpacing: '0.06em',
                  padding: '4px 10px',
                  borderRadius: 4,
                  border: 'none',
                  cursor: 'pointer',
                  background: active ? '#e67e22' : 'rgba(30,31,38,0.85)',
                  color: active ? '#000' : '#9a9aaa',
                  transition: 'background 0.15s, color 0.15s',
                }}
              >
                {kt.replace(/_/g, ' ')}
              </button>
            );
          })}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
          <label
            htmlFor="knowledge-project-filter"
            style={{
              fontFamily: 'monospace',
              fontSize: 10,
              color: '#9a9aaa',
              textTransform: 'uppercase',
              letterSpacing: '0.07em',
            }}
          >
            project
          </label>
          <select
            id="knowledge-project-filter"
            value={projectId}
            onChange={(e) => {
              setProjectId(e.target.value);
              // Lock auto-select once the user has picked anything —
              // including switching back to "All projects" — so a later
              // ``useProjects`` refetch doesn't yank them back.
              setAutoSelected(true);
            }}
            style={{
              flex: 1,
              maxWidth: 360,
              background: 'rgba(30,31,38,0.85)',
              border: '1px solid rgba(65,72,90,0.3)',
              borderRadius: 4,
              padding: '6px 10px',
              fontFamily: 'monospace',
              fontSize: 11,
              color: '#e2e2eb',
              outline: 'none',
              cursor: 'pointer',
            }}
          >
            <option value="">All projects (default tenant)</option>
            {projectOptions.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* ── Sources table ───────────────────────────────────────────────── */}
      <div style={GLASS} role="region" aria-label="Knowledge sources">
        {/* Header */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(220px, 1.6fr) 140px 90px 130px 110px 110px',
            gap: 12,
            padding: '10px 16px',
            borderBottom: '1px solid rgba(65,72,90,0.2)',
            alignItems: 'center',
          }}
        >
          <SortHeader label="source_path"    field="source_path"    sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
          <SortHeader label="type"           field="knowledge_type" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
          <SortHeader label="fragments"      field="fragment_count" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} align="right" />
          <SortHeader label="indexed"        field="indexed_at"     sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
          <span
            style={{
              fontFamily: 'monospace',
              fontSize: 10,
              textTransform: 'uppercase',
              letterSpacing: '0.07em',
              color: '#9a9aaa',
            }}
          >
            vendor
          </span>
          <span
            style={{
              fontFamily: 'monospace',
              fontSize: 10,
              textTransform: 'uppercase',
              letterSpacing: '0.07em',
              color: '#9a9aaa',
            }}
          >
            mpn
          </span>
        </div>

        {/* Body */}
        {isLoading ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '40px 0',
              gap: 8,
            }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 20, color: '#9a9aaa' }}>
              progress_activity
            </span>
            <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#9a9aaa' }}>
              Loading…
            </span>
          </div>
        ) : sortedSources.length === 0 ? (
          <EmptySourcesState />
        ) : (
          <div role="rowgroup">
            {sortedSources.map((source) => (
              <SourceRow key={source.source_path} source={source} onClick={handleRowClick} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state — points engineers at the ingestion CLI
// ---------------------------------------------------------------------------

function EmptySourcesState() {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 10,
        padding: '48px 24px',
        minHeight: 180,
        textAlign: 'center',
      }}
    >
      <span
        className="material-symbols-outlined"
        style={{ fontSize: 32, color: '#9a9aaa', opacity: 0.4 }}
      >
        psychology
      </span>
      <span style={{ fontSize: 13, color: '#e2e2eb' }}>
        No sources ingested yet
      </span>
      <code
        style={{
          fontFamily: 'monospace',
          fontSize: 11,
          color: '#86cfff',
          background: 'rgba(20,21,26,0.9)',
          border: '1px solid rgba(65,72,90,0.3)',
          borderRadius: 3,
          padding: '4px 10px',
        }}
      >
        forge ingest &lt;path&gt;
      </code>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Source row
// ---------------------------------------------------------------------------

function SourceRow({
  source,
  onClick,
}: {
  source: SourceSummary;
  onClick: (source: SourceSummary) => void;
}) {
  const [hovered, setHovered] = useState(false);
  const indexedRel = source.indexed_at ? formatRelativeTime(source.indexed_at) : '—';
  const vendor = metadataField(source.metadata, 'vendor');
  const mpn = metadataField(source.metadata, 'mpn');

  return (
    <div
      role="row"
      tabIndex={0}
      onClick={() => onClick(source)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick(source);
        }
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(220px, 1.6fr) 140px 90px 130px 110px 110px',
        gap: 12,
        alignItems: 'center',
        padding: '8px 16px',
        borderBottom: '1px solid rgba(65,72,90,0.08)',
        cursor: 'pointer',
        background: hovered ? '#282a30' : 'transparent',
        transition: 'background 0.15s',
        outline: 'none',
      }}
    >
      <span
        title={source.source_path}
        style={{
          fontFamily: 'monospace',
          fontSize: 12,
          color: '#d4d4d8',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          minWidth: 0,
        }}
      >
        {source.source_path}
      </span>
      <TypeChip type={source.knowledge_type} />
      <span
        style={{
          fontFamily: 'monospace',
          fontSize: 12,
          color: '#e2e2eb',
          textAlign: 'right',
        }}
      >
        {source.fragment_count}
      </span>
      <span
        style={{
          fontFamily: 'monospace',
          fontSize: 11,
          color: '#9a9aaa',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
      >
        {indexedRel}
      </span>
      <span
        style={{
          fontFamily: 'monospace',
          fontSize: 11,
          color: vendor === '—' ? '#5a5a66' : '#d4d4d8',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {vendor}
      </span>
      <span
        style={{
          fontFamily: 'monospace',
          fontSize: 11,
          color: mpn === '—' ? '#5a5a66' : '#d4d4d8',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {mpn}
      </span>
    </div>
  );
}
