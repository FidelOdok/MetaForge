import { useMemo, useState } from 'react';
import { OwnerBadge, OwnerNote } from './Ownership';
import { AlertTriangle, ExternalLink } from 'lucide-react';
import { EmptyState } from '../ui/EmptyState';
import {
  ORG_REGIONS,
  type LibraryComponent,
  type LibraryMachine,
  type LibraryOrganisation,
  type OrgRole,
  type OrgStatus,
} from '../../api/endpoints/library';

const ROLES: Array<[OrgRole, string]> = [
  ['manufacturer', 'Makers'],
  ['distributor', 'Sellers'],
  ['fabricator', 'Fabricators'],
  ['test_lab', 'Test labs'],
];

const STATUS_TEXT: Record<OrgStatus, string> = {
  approved: 'Approved',
  unreviewed: 'Not reviewed',
  blocked: 'Blocked',
};

export function roleText(roles: OrgRole[]): string {
  return roles.map((r) => (r === 'test_lab' ? 'test lab' : r)).join(', ');
}

/** Approved, not reviewed or blocked, with the reason a blocked one carries. */
export function OrgStatusBadge({ org }: { org: LibraryOrganisation }) {
  return (
    <span
      className="mf-library-org-status"
      data-status={org.status}
      title={org.status === 'blocked' ? org.status_reason : undefined}
    >
      {org.status === 'blocked' && <AlertTriangle size={11} aria-hidden="true" />}
      {STATUS_TEXT[org.status]}
    </span>
  );
}

function OrgDetail({
  org,
  parts,
  machines,
}: {
  org: LibraryOrganisation;
  parts: LibraryComponent[];
  machines: LibraryMachine[];
}) {
  const made = parts.filter((p) => p.manufacturer_org === org.id);
  const sold = parts.filter((p) => (p.suppliers ?? []).some((s) => s.org === org.id));
  const run = machines.filter((m) => m.operator_org === org.id);
  const facts: Array<[string, string]> = [['Roles', roleText(org.roles)]];
  if (org.country) facts.push(['Country', org.country]);
  if (org.regions.length) facts.push(['Serves', org.regions.join(', ')]);
  if (org.aliases.length) facts.push(['Also written', org.aliases.join(', ')]);
  if (org.certifications.length) facts.push(['Certifications', org.certifications.join(', ')]);
  if (org.lifecycle_policy) facts.push(['Lifecycle', org.lifecycle_policy]);
  return (
    <article className="mf-library-detail" data-testid="library-org-detail">
      <header className="mf-library-detail-head">
        <h2>
          {org.title} <OrgStatusBadge org={org} /> <OwnerBadge shared={org.shared} />
        </h2>
        <OwnerNote shared={org.shared} />
        {org.status === 'blocked' && (
          <p className="mf-library-warning">
            <AlertTriangle size={13} aria-hidden="true" /> Blocked: {org.status_reason}. Parts made or sold only by it
            are flagged wherever they are chosen.
          </p>
        )}
        {org.status === 'unreviewed' && (
          <p className="mf-library-muted">Recorded from the names the library held; nobody has reviewed it yet.</p>
        )}
      </header>
      <dl className="mf-library-facts">
        {facts.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
        {org.website && (
          <div>
            <dt>Website</dt>
            <dd>
              <a href={org.website} target="_blank" rel="noopener noreferrer">
                Open <ExternalLink size={11} aria-hidden="true" />
              </a>
            </dd>
          </div>
        )}
      </dl>
      <details className="mf-library-section" open={made.length > 0}>
        <summary>
          Parts it makes <span className="mf-library-count">{made.length}</span>
        </summary>
        <ul className="mf-library-list-plain mf-library-section-body">
          {made.slice(0, 50).map((p) => (
            <li key={p.id}>{p.title || p.name}</li>
          ))}
        </ul>
      </details>
      <details className="mf-library-section">
        <summary>
          Parts it sells <span className="mf-library-count">{sold.length}</span>
        </summary>
        <ul className="mf-library-list-plain mf-library-section-body">
          {sold.slice(0, 50).map((p) => (
            <li key={p.id}>{p.title || p.name}</li>
          ))}
        </ul>
      </details>
      <details className="mf-library-section" open={run.length > 0 && made.length === 0}>
        <summary>
          Machines it runs <span className="mf-library-count">{run.length}</span>
        </summary>
        <ul className="mf-library-list-plain mf-library-section-body">
          {run.map((m) => (
            <li key={m.id}>{m.name}</li>
          ))}
        </ul>
      </details>
      {org.notes && (
        <details className="mf-library-section">
          <summary>Notes</summary>
          <pre className="mf-library-notes">{org.notes}</pre>
        </details>
      )}
    </article>
  );
}

/** Manufacturers, distributors, fabricators and test labs, by role, region and status. */
export function OrganisationsTab({
  orgs,
  parts,
  machines,
  focus,
}: {
  orgs: LibraryOrganisation[];
  parts: LibraryComponent[];
  machines: LibraryMachine[];
  focus?: string | null;
}) {
  const [role, setRole] = useState<OrgRole | ''>('');
  const [region, setRegion] = useState('');
  const [status, setStatus] = useState<OrgStatus | ''>('');
  const [selected, setSelected] = useState<string | null>(focus ?? null);
  const shown = useMemo(
    () =>
      orgs
        .filter((o) => !role || o.roles.includes(role))
        .filter((o) => !region || o.regions.includes(region) || o.regions.includes('Global'))
        .filter((o) => !status || o.status === status)
        .sort((a, b) => a.title.localeCompare(b.title)),
    [orgs, role, region, status],
  );
  const current = shown.find((o) => o.id === selected) ?? shown[0];

  if (orgs.length === 0) {
    return (
      <EmptyState
        title="No organisations yet"
        description="Run the organisation migration, or register makers, sellers and fabricators."
      />
    );
  }
  return (
    <>
      <div className="mf-library-toolbar">
        <div className="mf-library-chips" role="group" aria-label="Role">
          <button type="button" className="mf-library-chip" aria-pressed={role === ''} onClick={() => setRole('')}>
            All
          </button>
          {ROLES.map(([r, name]) => (
            <button
              key={r}
              type="button"
              className="mf-library-chip"
              aria-pressed={role === r}
              onClick={() => setRole(role === r ? '' : r)}
            >
              {name} <span className="mf-library-count">{orgs.filter((o) => o.roles.includes(r)).length}</span>
            </button>
          ))}
        </div>
        <select
          aria-label="Region"
          className="mf-library-chip mf-library-more"
          data-on={region || undefined}
          value={region}
          onChange={(e) => setRegion(e.target.value)}
        >
          <option value="">Any region</option>
          {ORG_REGIONS.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <select
          aria-label="Status"
          className="mf-library-chip mf-library-more"
          data-on={status || undefined}
          value={status}
          onChange={(e) => setStatus(e.target.value as OrgStatus | '')}
        >
          <option value="">Any status</option>
          {(Object.keys(STATUS_TEXT) as OrgStatus[]).map((s) => (
            <option key={s} value={s}>
              {STATUS_TEXT[s]}
            </option>
          ))}
        </select>
      </div>
      <div className="mf-library-split">
        <div className="mf-library-browser">
          <div className="mf-library-browser-head" data-testid="library-org-count">
            {shown.length === orgs.length ? `${orgs.length} organisations` : `${shown.length} of ${orgs.length}`}
          </div>
          <ul className="mf-library-rows" role="listbox" aria-label="Organisations">
            {shown.map((o) => (
              <li key={o.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={current?.id === o.id}
                  className={current?.id === o.id ? 'is-selected' : undefined}
                  onClick={() => setSelected(o.id)}
                >
                  <span className="mf-library-row-line">
                    <span className="mf-library-row-title">
                      {o.title} <OwnerBadge shared={o.shared} />
                    </span>
                    {o.status === 'blocked' && (
                      <AlertTriangle size={12} aria-label="blocked" className="is-warning" />
                    )}
                  </span>
                  <span className="mf-library-row-meta">
                    {[roleText(o.roles), o.country, o.regions.join(' ')].filter(Boolean).join(' · ')}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
        {current ? (
          <OrgDetail org={current} parts={parts} machines={machines} />
        ) : (
          <EmptyState title="No organisation matches" description="Clear a filter." />
        )}
      </div>
    </>
  );
}
