import { createContext, useContext } from 'react';
import { authClient } from 'metaforge:auth';

/**
 * Whose library entry this is (FORGE-876). On a hosted MetaForge, shared by
 * several accounts, the library has two layers: the shared one, curated for
 * everyone and changed only by the people who run the deployment, and each
 * account's own. Every entry the gateway returns says which (`shared`).
 *
 * A local MetaForge has one library: every entry comes back `shared`, and
 * calling it that would be wrong, so nothing here shows unless the library
 * really is layered.
 */
export type OwnerFilter = 'all' | 'shared' | 'yours';

interface Owned {
  shared?: boolean;
}

/** A signed-in deployment, or any entry an account owns: the library has layers. */
export function isLayered(...lists: Array<Owned[] | undefined>): boolean {
  return authClient !== null || lists.some((l) => (l ?? []).some((e) => e.shared === false));
}

export function byOwner<T extends Owned>(entries: T[], owner: OwnerFilter): T[] {
  if (owner === 'shared') return entries.filter((e) => e.shared !== false);
  if (owner === 'yours') return entries.filter((e) => e.shared === false);
  return entries;
}

const LayeredContext = createContext(false);
export const LayeredLibrary = LayeredContext.Provider;

export function useLayered(): boolean {
  return useContext(LayeredContext);
}

const SHARED_HELP = 'From the shared library every account sees. Only the people who run MetaForge change it.';
const YOURS_HELP = 'Your own: only your account sees and changes it.';

/** "Shared" or "Yours", only where the library has layers. */
export function OwnerBadge({ shared }: { shared?: boolean }) {
  const layered = useLayered();
  if (!layered || shared === undefined) return null;
  return (
    <span
      className={`mf-library-owner ${shared ? 'is-shared' : 'is-yours'}`}
      title={shared ? SHARED_HELP : YOURS_HELP}
      data-testid="library-owner"
    >
      {shared ? 'Shared' : 'Yours'}
    </span>
  );
}

/** What an entry's layer means for the person reading it. */
export function OwnerNote({ shared }: { shared?: boolean }) {
  const layered = useLayered();
  if (!layered || shared === undefined) return null;
  return (
    <p className="mf-library-muted mf-library-owner-note" data-testid="library-owner-note">
      {shared
        ? 'Shared library entry: every account sees it, and only the people who run MetaForge can change it. ' +
          'To change it for your account, register your own version under the same name; it appears beside ' +
          'this one, marked Yours.'
        : 'Your own entry: only your account sees it, and you can change it.'}
    </p>
  );
}

/** All / Shared / Yours, with how many of each. */
export function OwnerFilterBar({
  value,
  onChange,
  counts,
}: {
  value: OwnerFilter;
  onChange: (v: OwnerFilter) => void;
  counts: Record<OwnerFilter, number>;
}) {
  const items: Array<[OwnerFilter, string]> = [
    ['all', 'All'],
    ['shared', 'Shared'],
    ['yours', 'Yours'],
  ];
  return (
    <div className="mf-library-chips mf-library-owner-filter" role="group" aria-label="Whose entries">
      {items.map(([v, name]) => (
        <button
          key={v}
          type="button"
          className="mf-library-chip"
          aria-pressed={value === v}
          onClick={() => onChange(v)}
          title={v === 'shared' ? SHARED_HELP : v === 'yours' ? YOURS_HELP : 'Shared and your own'}
        >
          {name} <span className="mf-library-count">{counts[v]}</span>
        </button>
      ))}
    </div>
  );
}
