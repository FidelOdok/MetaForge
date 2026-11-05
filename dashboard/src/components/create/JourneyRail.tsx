import { Check } from 'lucide-react';
import { MILESTONES, type Milestone } from './copy';

interface JourneyRailProps {
  /** The current milestone; `MILESTONES.length` when all are done. */
  milestone: number;
  /** One short summary per milestone. */
  summaries?: Partial<Record<Milestone, string>>;
  /** Something is running now, so the current milestone animates. */
  active?: boolean;
}

/**
 * Imagine, Develop, Check, Make (16.2 item 2): where this creation stands, as
 * summaries rather than gates. Work in a later milestone can already be under
 * way; nothing here blocks it.
 */
export function JourneyRail({ milestone, summaries, active }: JourneyRailProps) {
  return (
    <ol className="cv-journey" aria-label="Progress">
      {MILESTONES.map((label, i) => {
        const state = i < milestone ? 'done' : i === milestone ? 'current' : 'todo';
        const summary = summaries?.[label];
        return (
          <li
            key={label}
            className={`cv-journey-step is-${state} ${state === 'current' && active ? 'is-active' : ''}`}
            aria-current={state === 'current' ? 'step' : undefined}
            title={summary ? `${label}: ${summary}` : label}
          >
            <span className="cv-journey-dot" aria-hidden="true">
              {state === 'done' ? <Check size={11} /> : i + 1}
            </span>
            <span className="cv-journey-text">
              <span className="cv-journey-label">{label}</span>
              {summary && <span className="cv-journey-summary">{summary}</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
