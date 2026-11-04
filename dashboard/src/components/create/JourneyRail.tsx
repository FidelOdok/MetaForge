import { Check } from 'lucide-react';
import { JOURNEY } from './copy';

interface JourneyRailProps {
  step: number;
  /** Something is running now, so the current step animates. */
  active?: boolean;
}

/** Describe → … → Improve or accept, with where this creation is now. */
export function JourneyRail({ step, active }: JourneyRailProps) {
  return (
    <ol className="cv-journey" aria-label="Progress">
      {JOURNEY.map((label, i) => {
        const state = i < step ? 'done' : i === step ? 'current' : 'todo';
        return (
          <li
            key={label}
            className={`cv-journey-step is-${state} ${state === 'current' && active ? 'is-active' : ''}`}
            aria-current={state === 'current' ? 'step' : undefined}
          >
            <span className="cv-journey-dot" aria-hidden="true">
              {state === 'done' ? <Check size={11} /> : i + 1}
            </span>
            <span className="cv-journey-label">{label}</span>
          </li>
        );
      })}
    </ol>
  );
}
