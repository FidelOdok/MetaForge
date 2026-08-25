import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { listSources, searchKnowledge } from '../api/endpoints/knowledge';
import { useActiveProject } from '../hooks/use-active-project';
import { formatRelativeTime } from '../utils/format-time';
import type { KnowledgeSearchResult, KnowledgeType, SourceSummary } from '../types/knowledge';

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
  // Context UI: project scope now comes from the single global active
  // project every page shares (Topbar switcher), rather than this page's
  // own auto-select-newest + empty-fallback logic.
  const { activeProjectId } = useActiveProject();
  const [sortKey, setSortKey] = useState<SortKey>('indexed_at');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  // Semantic search (GET /v1/knowledge/search) — a real, previously-unused
  // backend capability. Debounced so we don't fire a request per keystroke;
  // a non-empty debounced query replaces the source-listing table below
  // with matched entries instead of filtering it client-side.
  const [searchInput, setSearchInput] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  useEffect(() => {
    const handle = setTimeout(() => setDebouncedSearch(searchInput.trim()), 300);
    return () => clearTimeout(handle);
  }, [searchInput]);
  const isSearching = debouncedSearch.length > 0;

  const { data: searchResults, isLoading: isSearchLoading } = useQuery({
    queryKey: ['knowledge', 'search', debouncedSearch, filterType, activeProjectId ?? ''],
    queryFn: () =>
      searchKnowledge({
        query: debouncedSearch,
        knowledge_type: filterType === 'all' ? undefined : filterType,
        project_id: activeProjectId ?? undefined,
        limit: 20,
      }),
    enabled: isSearching,
    staleTime: 30_000,
  });

  // The filter chip pushes ``knowledge_type`` to the server so we don't
  // pull rows we'll just discard; the project_id filter does the same.
  const { data: sources, isLoading } = useQuery({
    queryKey: ['knowledge', 'sources', filterType, activeProjectId ?? ''],
    queryFn: () =>
      listSources({
        knowledge_type: filterType === 'all' ? undefined : filterType,
        project_id: activeProjectId ?? undefined,
      }),
    staleTime: 30_000,
    enabled: !isSearching,
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
            {isSearching
              ? `${searchResults?.length ?? 0} match${(searchResults?.length ?? 0) === 1 ? '' : 'es'}`
              : `${total} ${total === 1 ? 'source' : 'sources'} · L1 corpus`}
          </span>
        </div>
        <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
          <span
            className="material-symbols-outlined"
            style={{ position: 'absolute', left: 8, fontSize: 14, color: '#9a9aaa', pointerEvents: 'none' }}
          >
            search
          </span>
          <input
            type="text"
            aria-label="Search knowledge"
            placeholder="Search knowledge…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            style={{
              width: 260,
              padding: '6px 10px 6px 28px',
              background: 'rgba(30,31,38,0.85)',
              border: '1px solid rgba(65,72,90,0.3)',
              borderRadius: 4,
              fontFamily: 'monospace',
              fontSize: 11,
              color: '#e2e2eb',
              outline: 'none',
            }}
          />
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
      </div>

      {isSearching ? (
        /* ── Search results ────────────────────────────────────────────── */
        <div style={GLASS} role="region" aria-label="Search results">
          {isSearchLoading ? (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '40px 0', gap: 8 }}>
              <span className="material-symbols-outlined" style={{ fontSize: 20, color: '#9a9aaa' }}>
                progress_activity
              </span>
              <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#9a9aaa' }}>Searching…</span>
            </div>
          ) : !searchResults || searchResults.length === 0 ? (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 8,
                padding: '48px 24px',
                minHeight: 160,
                textAlign: 'center',
              }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 32, color: '#9a9aaa', opacity: 0.4 }}>
                search_off
              </span>
              <span style={{ fontSize: 13, color: '#e2e2eb' }}>No matches for &ldquo;{debouncedSearch}&rdquo;</span>
            </div>
          ) : (
            <div role="rowgroup">
              {searchResults.map((result) => (
                <SearchResultRow key={result.id} result={result} onOpenSource={navigate} />
              ))}
            </div>
          )}
        </div>
      ) : (
      /* ── Sources table ───────────────────────────────────────────────── */
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
      )}
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
// Search result row
// ---------------------------------------------------------------------------

function SearchResultRow({
  result,
  onOpenSource,
}: {
  result: KnowledgeSearchResult;
  onOpenSource: (path: string) => void;
}) {
  const [hovered, setHovered] = useState(false);
  const clickable = !!result.source_path;

  return (
    <div
      role="row"
      tabIndex={clickable ? 0 : undefined}
      onClick={() => {
        if (result.source_path) onOpenSource(`/knowledge/sources/${encodeURIComponent(result.source_path)}`);
      }}
      onKeyDown={(e) => {
        if (clickable && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          onOpenSource(`/knowledge/sources/${encodeURIComponent(result.source_path!)}`);
        }
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        padding: '10px 16px',
        borderBottom: '1px solid rgba(65,72,90,0.08)',
        cursor: clickable ? 'pointer' : 'default',
        background: hovered ? '#282a30' : 'transparent',
        transition: 'background 0.15s',
        outline: 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <TypeChip type={result.knowledge_type} />
        {result.source_path && (
          <span
            style={{
              fontFamily: 'monospace',
              fontSize: 11,
              color: '#9a9aaa',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {result.source_path}
          </span>
        )}
      </div>
      <span style={{ fontSize: 12, color: '#d4d4d8', lineHeight: 1.5 }}>
        {result.content.length > 240 ? `${result.content.slice(0, 240)}…` : result.content}
      </span>
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
