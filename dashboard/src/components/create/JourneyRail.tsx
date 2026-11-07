import { Check } from 'lucide-react';
import { MILESTONES, type Milestone } from './copy';

interface JourneyRailProps {
  /** Where the creation stands: the current milestone; `MILESTONES.length` when all are done. */
  milestone: number;
  /** The stage on screen. */
  selected: number;
  onSelect: (index: number) => void;
  /** One short summary per milestone. */
  summaries?: Partial<Record<Milestone, string>>;
  /** Something is running now, so the current milestone animates. */
  active?: boolean;
}

/**
 * Imagine, Develop, Check, Make: the navigation of Create. Each stage owns
 * its content; the rail also says where the creation stands. Any stage can
 * be opened: work in a later stage can already be under way.
 */
export function JourneyRail({ milestone, selected, onSelect, summaries, active }: JourneyRailProps) {
  return (
    <div className="cv-journey" role="tablist" aria-label="Stages">
      {MILESTONES.map((label, i) => {
        const state = i < milestone ? 'done' : i === milestone ? 'current' : 'todo';
        const summary = summaries?.[label];
        return (
          <button
            key={label}
            type="button"
            role="tab"
            id={`cv-stage-tab-${i}`}
            aria-selected={i === selected}
            aria-controls="cv-stage-panel"
            className={`cv-journey-step is-${state}${i === selected ? ' is-selected' : ''}${state === 'current' && active ? ' is-active' : ''}`}
            data-state={state}
            title={summary ? `${label}: ${summary}` : label}
            onClick={() => onSelect(i)}
          >
            <span className="cv-journey-dot" aria-hidden="true">
              {state === 'done' ? <Check size={11} /> : i + 1}
            </span>
            <span className="cv-journey-text">
              <span className="cv-journey-label">{label}</span>
              {summary && <span className="cv-journey-summary">{summary}</span>}
            </span>
          </button>
        );
      })}
    </div>
  );
}
