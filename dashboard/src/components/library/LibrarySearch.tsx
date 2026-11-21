import { useEffect, useState } from 'react';
import { OwnerBadge } from './Ownership';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Search } from 'lucide-react';
import { EmptyState } from '../ui/EmptyState';
import { ORG_REGIONS, searchLibrary, type SearchHit, type SearchKind } from '../../api/endpoints/library';

const KIND_TEXT: Record<SearchKind, string> = {
  component: 'Part',
  organisation: 'Organisation',
  material: 'Material',
  process: 'Process',
  machine: 'Machine',
  appearance: 'Appearance',
  ergonomic_limit: 'Ergonomic limit',
};

/** The query a person typed, once they stop typing for a moment. */
function useSettled(value: string, ms = 250): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}

function HitRow({ hit, onOpen }: { hit: SearchHit; onOpen: (hit: SearchHit) => void }) {
  const meta =
    hit.kind === 'component'
      ? [hit.manufacturer, hit.mpn].filter(Boolean).join(' · ')
      : hit.kind === 'organisation'
        ? (hit.roles ?? []).join(', ')
        : '';
  return (
    <li>
      <button type="button" onClick={() => onOpen(hit)} data-testid="library-search-hit">
        <span className="mf-library-row-line">
          <span className="mf-library-search-kind">{KIND_TEXT[hit.kind]}</span>
          <span className="mf-library-row-title">
            {hit.title} <OwnerBadge shared={hit.shared} />
          </span>
          {(hit.blocked?.length || hit.status === 'blocked') && (
            <span className="mf-library-org-status" data-status="blocked" title={(hit.blocked ?? []).join('; ')}>
              <AlertTriangle size={11} aria-hidden="true" /> Blocked
            </span>
          )}
        </span>
        <span className="mf-library-row-meta">
          {[meta, `matched ${hit.matched.join(', ')}`].filter(Boolean).join(' · ')}
        </span>
      </button>
    </li>
  );
}

/**
 * One search box over the whole library (FORGE-895): parts with their makers' and sellers'
 * every spelling, organisations, materials, processes, machines and more, best first.
 * Clicking a result opens it in its own tab.
 */
export function LibrarySearch({ onOpen }: { onOpen: (hit: SearchHit) => void }) {
  const [text, setText] = useState('');
  const [region, setRegion] = useState('');
  const q = useSettled(text.trim());
  const active = q.length > 0 || region !== '';
  const result = useQuery({
    queryKey: ['library', 'search', q, region],
    queryFn: () => searchLibrary({ q, region: region || undefined }),
    enabled: active,
    staleTime: 30_000,
  });
  return (
    <section className="mf-library-global-search" aria-label="Search the library">
      <div className="mf-library-toolbar">
        <label className="mf-library-search mf-library-search-wide">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            aria-label="Search the whole library"
            placeholder="Search the whole library: parts, makers, sellers, fabricators, materials, machines"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <select
          aria-label="Search region"
          className="mf-library-chip mf-library-more"
          data-on={region || undefined}
          value={region}
          onChange={(e) => setRegion(e.target.value)}
          title="Only organisations serving this region, and what they make, sell or run"
        >
          <option value="">Any region</option>
          {ORG_REGIONS.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </div>
      {active && (
        <div className="mf-library-browser" data-testid="library-search-results">
          <div className="mf-library-browser-head">
            {result.isLoading
              ? 'Searching…'
              : result.isError
                ? 'The search could not run'
                : `${result.data?.total ?? 0} found${(result.data?.total ?? 0) > (result.data?.results.length ?? 0) ? `, best ${result.data?.results.length} shown` : ''}`}
          </div>
          {result.data && result.data.results.length === 0 ? (
            <EmptyState title="Nothing in the library matches" description="Every word must match; try fewer words." />
          ) : (
            <ul className="mf-library-rows" aria-label="Search results">
              {result.data?.results.map((hit) => (
                <HitRow key={`${hit.kind}-${hit.id}`} hit={hit} onOpen={onOpen} />
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
