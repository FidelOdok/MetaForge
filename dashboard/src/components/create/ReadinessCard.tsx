import { ArrowRight, CircleAlert } from 'lucide-react';
import type { Readiness } from '../../api/endpoints/create';
import { STAGE_COPY } from './copy';

interface ReadinessCardProps {
  readiness: Readiness;
  onAction?: (blockerId: string) => void;
}

/** Where the creation stands, what is in the way, and the one thing to do next. */
export function ReadinessCard({ readiness, onAction }: ReadinessCardProps) {
  const copy = STAGE_COPY[readiness.stage];
  return (
    <section className="cv-card cv-readiness" aria-labelledby="cv-readiness-title">
      <p className="cv-eyebrow">Where it stands</p>
      <h3 id="cv-readiness-title">{copy.label}</h3>
      <p className="cv-muted">{copy.meaning}</p>
      <p className="cv-next">
        <ArrowRight size={14} aria-hidden="true" /> {readiness.next_action}
      </p>
      {readiness.blockers.length > 0 && (
        <ul className="cv-blockers">
          {readiness.blockers.map((b) => (
            <li key={b.id}>
              <CircleAlert size={13} aria-hidden="true" />
              <span>{b.message}</span>
              {onAction ? (
                <button type="button" className="cv-link" onClick={() => onAction(b.id)}>
                  {b.action}
                </button>
              ) : (
                <span className="cv-muted">{b.action}</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {readiness.stage === 'checked_design' && (
        <p className="cv-fineprint">
          A finished design is not proof it is safe for its use or right for every process. The checks and your
          inspection decide that.
        </p>
      )}
    </section>
  );
}
