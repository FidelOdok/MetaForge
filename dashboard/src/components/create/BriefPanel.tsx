import { useEffect, useRef, useState } from 'react';
import { Check, Plus, Ruler, Trash2 } from 'lucide-react';
import {
  BRIEF_KEYS,
  conflictOf,
  putBrief,
  refusalOf,
  type Brief,
  type BriefItem,
  type BriefKey,
  type CreationView,
  type LengthUnit,
  type Measurement,
  type MeasurementStatus,
} from '../../api/endpoints/create';
import { useCreationWrite } from '../../hooks/use-creation';
import { AutoGrowTextarea } from './AutoGrowTextarea';
import { BRIEF_COPY, MEASUREMENT_STATUS_COPY } from './copy';

const UNITS: LengthUnit[] = ['mm', 'cm', 'm', 'in', 'ft'];
const SAVE_DELAY_MS = 900;

type SaveState = { kind: 'saved' } | { kind: 'saving' } | { kind: 'dirty' } | { kind: 'failed'; reason: string } | { kind: 'conflict' };

interface BriefPanelProps {
  projectId: string;
  view: CreationView;
}

let measurementSeq = 0;

/**
 * The brief, editable, saved as you go. Every item says whether it is
 * confirmed, an assumption or still open; a measurement says whether a
 * person confirmed it. A save that lands after someone else's (another view,
 * an agent) is refused, and the person chooses which to keep.
 */
export function BriefPanel({ projectId, view }: BriefPanelProps) {
  const server = view.creation;
  const [brief, setBrief] = useState<Brief>(server.brief);
  const [baseVersion, setBaseVersionState] = useState(server.version);
  // Read when a delayed save fires, so an edit made during a save builds on it.
  const versionRef = useRef(server.version);
  const setBaseVersion = (v: number) => {
    versionRef.current = v;
    setBaseVersionState(v);
  };
  const [save, setSave] = useState<SaveState>({ kind: 'saved' });
  const dirty = save.kind === 'dirty' || save.kind === 'saving' || save.kind === 'failed' || save.kind === 'conflict';
  const timer = useRef<number | undefined>(undefined);

  // Follow the stored brief while there is nothing unsaved here.
  useEffect(() => {
    if (!dirty && server.version !== baseVersion) {
      setBrief(server.brief);
      setBaseVersion(server.version);
    }
  }, [server, dirty, baseVersion]);

  const write = useCreationWrite(projectId, (vars: { brief: Brief; expected: number }) =>
    putBrief(projectId, vars.brief, { expected_version: vars.expected }),
  );

  const persist = (next: Brief, expected: number) => {
    setSave({ kind: 'saving' });
    write.mutate(
      { brief: next, expected },
      {
        onSuccess: (saved) => {
          setBaseVersion(saved.creation.version);
          setSave({ kind: 'saved' });
        },
        onError: (error) => setSave(conflictOf(error) ? { kind: 'conflict' } : { kind: 'failed', reason: refusalOf(error) }),
      },
    );
  };

  const edit = (next: Brief) => {
    setBrief(next);
    setSave({ kind: 'dirty' });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => persist(next, versionRef.current), SAVE_DELAY_MS);
  };
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const setItem = (key: BriefKey, change: Partial<BriefItem>) => {
    const current: BriefItem = brief.items[key] ?? { value: '', status: 'missing', source: 'user' };
    const merged = { ...current, ...change };
    if (change.value !== undefined) {
      // What a person types is theirs: confirmed, or open again when cleared.
      merged.source = 'user';
      merged.status = change.value.trim() ? 'confirmed' : 'missing';
    }
    edit({ ...brief, items: { ...brief.items, [key]: merged } });
  };

  const setMeasurement = (id: string, change: Partial<Measurement>) =>
    edit({ ...brief, measurements: brief.measurements.map((m) => (m.id === id ? { ...m, ...change } : m)) });

  const filled = BRIEF_KEYS.filter((k) => brief.items[k] && brief.items[k]!.status !== 'missing');
  const open = BRIEF_KEYS.filter((k) => !brief.items[k] || brief.items[k]!.status === 'missing');
  const [showAll, setShowAll] = useState(false);
  // Ask at the point of need: the first open questions, not a questionnaire.
  const asked = showAll ? open : open.slice(0, 2);

  return (
    <div className="cv-panel-body">
      <SaveStatus
        state={save}
        onRetry={() => persist(brief, versionRef.current)}
        onLoadLatest={() => {
          setBrief(server.brief);
          setBaseVersion(server.version);
          setSave({ kind: 'saved' });
        }}
        onKeepMine={() => persist(brief, server.version)}
      />

      {filled.map((key) => (
        <BriefField key={key} k={key} item={brief.items[key]!} onChange={(c) => setItem(key, c)} />
      ))}

      {open.length > 0 && (
        <div className="cv-questions">
          <p className="cv-eyebrow">{filled.length ? 'Still open' : 'A few questions when you are ready'}</p>
          {asked.map((key) => (
            <BriefField key={key} k={key} item={brief.items[key] ?? { value: '', status: 'missing', source: 'user' }} onChange={(c) => setItem(key, c)} />
          ))}
          {open.length > asked.length && (
            <button type="button" className="cv-link" onClick={() => setShowAll(true)}>
              Show {open.length - asked.length} more
            </button>
          )}
        </div>
      )}

      <section className="cv-subsection" aria-labelledby="cv-measurements">
        <h4 id="cv-measurements">
          <Ruler size={14} aria-hidden="true" /> Measurements
        </h4>
        <p className="cv-fineprint">
          A camera picture alone is not a calibrated measurement. Dimensions the making depends on must be confirmed by
          you.
        </p>
        {brief.measurements.map((m) => (
          <div key={m.id} className={`cv-measure ${m.critical && m.status !== 'user_confirmed' ? 'needs-confirm' : ''}`}>
            <input
              aria-label="What is measured"
              className="cv-input"
              value={m.label}
              onChange={(e) => setMeasurement(m.id, { label: e.target.value })}
            />
            <input
              aria-label={`${m.label} value`}
              className="cv-input cv-input-num"
              type="number"
              step="any"
              value={Number.isFinite(m.value) ? m.value : ''}
              onChange={(e) => setMeasurement(m.id, { value: Number(e.target.value), status: 'unconfirmed' })}
            />
            <select
              aria-label={`${m.label} unit`}
              className="cv-input"
              value={m.unit}
              onChange={(e) => setMeasurement(m.id, { unit: e.target.value as LengthUnit, status: 'unconfirmed' })}
            >
              {UNITS.map((u) => (
                <option key={u}>{u}</option>
              ))}
            </select>
            <select
              aria-label={`${m.label} certainty`}
              className="cv-input"
              value={m.status}
              onChange={(e) => setMeasurement(m.id, { status: e.target.value as MeasurementStatus })}
            >
              {(Object.keys(MEASUREMENT_STATUS_COPY) as MeasurementStatus[]).map((s) => (
                <option key={s} value={s}>
                  {MEASUREMENT_STATUS_COPY[s]}
                </option>
              ))}
            </select>
            <label className="cv-check">
              <input
                type="checkbox"
                checked={m.critical}
                onChange={(e) => setMeasurement(m.id, { critical: e.target.checked })}
              />
              Critical
            </label>
            <button
              type="button"
              className="cv-icon-btn"
              aria-label={`Remove ${m.label}`}
              onClick={() => edit({ ...brief, measurements: brief.measurements.filter((x) => x.id !== m.id) })}
            >
              <Trash2 size={13} />
            </button>
          </div>
        ))}
        <button
          type="button"
          className="cv-link"
          onClick={() =>
            edit({
              ...brief,
              measurements: [
                ...brief.measurements,
                {
                  id: `m${Date.now().toString(36)}${(measurementSeq += 1)}`,
                  label: 'New dimension',
                  value: 0,
                  unit: 'mm',
                  status: 'unconfirmed',
                  critical: false,
                  source: 'typed',
                },
              ],
            })
          }
        >
          <Plus size={13} /> Add a measurement
        </button>
      </section>

      {brief.assumptions.length > 0 && (
        <section className="cv-subsection" aria-labelledby="cv-assumptions">
          <h4 id="cv-assumptions">Assumptions</h4>
          {brief.assumptions.map((a) => (
            <div key={a.id} className={`cv-assumption is-${a.status}`}>
              <AutoGrowTextarea
                aria-label="Assumption"
                className="cv-input"
                value={a.text}
                onChange={(e) =>
                  edit({
                    ...brief,
                    assumptions: brief.assumptions.map((x) => (x.id === a.id ? { ...x, text: e.target.value, status: 'revised' } : x)),
                  })
                }
              />
              {a.status === 'open' ? (
                <button
                  type="button"
                  className="cv-chip-btn"
                  onClick={() =>
                    edit({
                      ...brief,
                      assumptions: brief.assumptions.map((x) => (x.id === a.id ? { ...x, status: 'confirmed' } : x)),
                    })
                  }
                >
                  <Check size={13} /> Confirm
                </button>
              ) : (
                <span className="cv-tag">{a.status === 'confirmed' ? 'Confirmed' : 'Revised'}</span>
              )}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

function BriefField({ k, item, onChange }: { k: BriefKey; item: BriefItem; onChange: (c: Partial<BriefItem>) => void }) {
  const copy = BRIEF_COPY[k];
  const id = `cv-brief-${k}`;
  return (
    <div className={`cv-field is-${item.status}`}>
      <div className="cv-field-head">
        <label htmlFor={id}>{copy.label}</label>
        <span className={`cv-tag cv-status-${item.status}`}>
          {item.status === 'confirmed' ? 'Confirmed' : item.status === 'assumed' ? 'Assumption' : 'Open'}
        </span>
        {item.status === 'assumed' && (
          <button type="button" className="cv-link" onClick={() => onChange({ status: 'confirmed', source: 'user' })}>
            Confirm
          </button>
        )}
      </div>
      {item.status === 'missing' && <p className="cv-muted cv-question">{copy.question}</p>}
      <AutoGrowTextarea
        id={id}
        className="cv-input"
        placeholder={copy.placeholder}
        value={item.value}
        onChange={(e) => onChange({ value: e.target.value })}
      />
    </div>
  );
}

function SaveStatus({
  state,
  onRetry,
  onLoadLatest,
  onKeepMine,
}: {
  state: SaveState;
  onRetry: () => void;
  onLoadLatest: () => void;
  onKeepMine: () => void;
}) {
  if (state.kind === 'conflict') {
    return (
      <div className="cv-banner is-warning" role="alert">
        This brief was changed elsewhere (the Engineering view or an agent) since you started editing.
        <span className="cv-banner-actions">
          <button type="button" className="cv-link" onClick={onLoadLatest}>
            Load the latest
          </button>
          <button type="button" className="cv-link" onClick={onKeepMine}>
            Keep my version
          </button>
        </span>
      </div>
    );
  }
  if (state.kind === 'failed') {
    return (
      <div className="cv-banner is-error" role="alert">
        Not saved: {state.reason}
        <button type="button" className="cv-link" onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  }
  return (
    <p className="cv-save" aria-live="polite">
      {state.kind === 'saving' ? 'Saving…' : state.kind === 'dirty' ? 'Unsaved changes' : 'All changes saved'}
    </p>
  );
}
