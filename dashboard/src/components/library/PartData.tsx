import { AlertTriangle, Check, CircleDashed, Eye } from 'lucide-react';
import type {
  LibraryComponent,
  LifecycleStatus,
  PartDataCheck,
  PartDataStatus,
  PartLifecycle,
  RatingRow,
} from '../../api/endpoints/library';

/**
 * FORGE-906: a library part's real part data. The checklist says what the part
 * still lacks before a design can rely on it; the ratings are the datasheet's
 * own tables, each row with its source. Nothing here fills a gap.
 */

const LIFECYCLE_TEXT: Record<LifecycleStatus, string> = {
  active: 'Active',
  nrnd: 'Not for new designs',
  eol: 'End of life',
  obsolete: 'Obsolete',
  unknown: 'Unknown',
};

export function lifecycleOf(part: LibraryComponent): PartLifecycle | undefined {
  const lc = part.lifecycle as PartLifecycle | undefined;
  return lc && 'status' in lc ? lc : undefined;
}

/** True for a part its maker has ended or is ending. */
export function isEnded(part: LibraryComponent): boolean {
  const status = lifecycleOf(part)?.status;
  return status === 'eol' || status === 'obsolete' || status === 'nrnd';
}

export function LifecycleBadge({ lifecycle }: { lifecycle?: PartLifecycle }) {
  if (!lifecycle) return <span className="mf-library-muted">not recorded</span>;
  const title = `${lifecycle.source_ref || lifecycle.source_kind}, read ${lifecycle.read_on}${
    lifecycle.last_time_buy ? `; last time buy ${lifecycle.last_time_buy}` : ''
  }`;
  return (
    <span className="mf-part-status" data-status={lifecycle.status} title={title} data-testid="part-lifecycle">
      {lifecycle.status !== 'active' && lifecycle.status !== 'unknown' && <AlertTriangle size={11} aria-hidden="true" />}
      {LIFECYCLE_TEXT[lifecycle.status]}
    </span>
  );
}

const STATUS_ICON: Record<PartDataStatus, typeof Check> = {
  ok: Check,
  review: Eye,
  missing: CircleDashed,
  bad: AlertTriangle,
};

const STATUS_TEXT: Record<PartDataStatus, string> = {
  ok: 'recorded',
  review: 'review',
  missing: 'missing',
  bad: 'ended',
};

export function PartDataChecklist({ check }: { check: PartDataCheck }) {
  return (
    <div data-testid="part-data">
      <p className="mf-part-summary" data-complete={check.complete ? 'true' : 'false'}>
        {check.complete ? 'Ready to design with: every required item is recorded.' : `Not ready: ${check.summary}.`}
      </p>
      <ul className="mf-part-checklist">
        {check.items.map((item) => {
          const Icon = STATUS_ICON[item.status];
          return (
            <li key={item.key} data-status={item.status} data-testid={`part-data-${item.key}`}>
              <Icon size={12} aria-hidden="true" />
              <span className="mf-part-check-label">
                {item.label}
                {!item.required && <span className="mf-library-muted"> (optional)</span>}
              </span>
              <span className="mf-part-check-state">{STATUS_TEXT[item.status]}</span>
              {item.detail && <span className="mf-library-muted mf-part-check-detail">{item.detail}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const KIND_TITLE: Record<RatingRow['kind'], string> = {
  abs_max: 'Absolute maximum ratings',
  recommended: 'Recommended operating conditions',
  electrical: 'Electrical characteristics',
};

const UNIT_SUFFIX = /_(v|a|w|c|c_per_w|hz|f|ohm|h|pct)$/;

function paramName(param: string): string {
  const name = param.replace(UNIT_SUFFIX, '').replace(/_/g, ' ');
  return name.replace(/\bja\b/, 'junction to ambient').replace(/\bjc\b/, 'junction to case').replace(/\besd hbm\b/, 'ESD (HBM)').replace(/\besd cdm\b/, 'ESD (CDM)');
}

function unitText(unit: string): string {
  return unit.replace('degC', '°C').replace('ohm', 'Ω');
}

function cell(v: number | null): string {
  return v === null || v === undefined ? '' : String(v);
}

function SourceCell({ row }: { row: RatingRow }) {
  const link = row.source_ref?.startsWith('http') ? row.source_ref.split(' ')[0] : undefined;
  const where = link ? row.source_ref.slice(link.length).trim() : '';
  const text = row.source_kind.replace(/_/g, ' ');
  return (
    <td className="mf-library-muted">
      {link ? (
        <a href={link} target="_blank" rel="noopener noreferrer">
          {text}
        </a>
      ) : (
        text
      )}
      {where && ` ${where}`}
      {row.needs_review ? ' · review' : ''}
    </td>
  );
}

export function RatingsTables({ ratings }: { ratings: RatingRow[] }) {
  return (
    <div className="mf-part-ratings" data-testid="part-ratings">
      {(Object.keys(KIND_TITLE) as Array<RatingRow['kind']>).map((kind) => {
        const rows = ratings.filter((r) => r.kind === kind);
        if (!rows.length) return null;
        return (
          <table key={kind} className="mf-library-table" data-testid={`part-ratings-${kind}`}>
            <caption>{KIND_TITLE[kind]}</caption>
            <thead>
              <tr>
                <th scope="col">Parameter</th>
                <th scope="col">Min</th>
                <th scope="col">Typ</th>
                <th scope="col">Max</th>
                <th scope="col">Unit</th>
                <th scope="col">Conditions</th>
                <th scope="col">Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.param}-${i}`}>
                  <td>{paramName(r.param)}</td>
                  <td className="font-mono">{cell(r.min)}</td>
                  <td className="font-mono">{cell(r.typ)}</td>
                  <td className="font-mono">{cell(r.max)}</td>
                  <td className="font-mono">{unitText(r.unit)}</td>
                  <td className="mf-library-muted">{r.conditions}</td>
                  <SourceCell row={r} />
                </tr>
              ))}
            </tbody>
          </table>
        );
      })}
    </div>
  );
}
