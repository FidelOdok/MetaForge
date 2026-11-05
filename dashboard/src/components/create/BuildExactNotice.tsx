import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import type { BuildExactState } from '../../hooks/use-build-exact';

/**
 * The one line Build exact model needs on the stage (spec 16.2 item 5): a
 * single question with concrete choices when an input is missing, what must
 * be done first when something blocks the build, or that it started. It
 * never fills a value in on its own.
 */
export function BuildExactNotice({
  state,
  onAnswer,
  onFix,
  onDismiss,
}: {
  state: Exclude<BuildExactState, { phase: 'idle' }>;
  onAnswer: (value: number | string) => void;
  onFix: (fix: 'editable' | 'plane') => void;
  onDismiss: () => void;
}) {
  const [other, setOther] = useState('');

  if (state.phase === 'checking' || state.phase === 'starting') {
    return (
      <div className="cv-stage-notice" role="status" data-testid="build-exact-notice">
        <Loader2 size={14} className="cv-spin" aria-hidden="true" />
        <span>{state.phase === 'checking' ? 'Checking what the exact model needs…' : 'Starting the exact build…'}</span>
      </div>
    );
  }

  if (state.phase === 'asking') {
    const q = state.question;
    const submitOther = () => {
      const raw = other.trim();
      if (!raw) return;
      if (q.input.kind === 'number') {
        const v = Number(raw);
        if (!Number.isFinite(v) || v <= 0 || (q.input.max !== undefined && v > q.input.max)) return;
        onAnswer(v);
      } else {
        onAnswer(raw);
      }
      setOther('');
    };
    return (
      <div className="cv-stage-notice is-question" role="group" aria-label={q.prompt} data-testid="build-exact-question">
        <span className="cv-question-prompt">{q.prompt}</span>
        {q.choices.map((c) => (
          <button key={String(c.value)} type="button" className="cv-chip-btn" onClick={() => onAnswer(c.value)}>
            {c.label}
          </button>
        ))}
        <form
          className="cv-question-other"
          onSubmit={(e) => {
            e.preventDefault();
            submitOther();
          }}
        >
          <input
            aria-label={q.input.label}
            placeholder={q.input.label}
            type={q.input.kind === 'number' ? 'number' : 'text'}
            inputMode={q.input.kind === 'number' ? 'decimal' : undefined}
            min={q.input.min}
            max={q.input.max}
            step="any"
            value={other}
            onChange={(e) => setOther(e.target.value)}
          />
          <button type="submit" className="cv-chip-btn" disabled={!other.trim()}>
            Use this
          </button>
        </form>
        <button type="button" className="cv-chip-btn" onClick={onDismiss}>
          Not now
        </button>
      </div>
    );
  }

  if (state.phase === 'blocked') {
    return (
      <div className="cv-stage-notice is-spend" role="alert" data-testid="build-exact-blocked">
        <span>{state.message}</span>
        {state.fix && (
          <button type="button" className="cv-chip-btn" onClick={() => onFix(state.fix!)}>
            {state.fix === 'editable' ? 'Open the editable sketch' : 'Choose the sketch plane'}
          </button>
        )}
        <button type="button" className="cv-chip-btn" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    );
  }

  return (
    <div
      className={`cv-stage-notice ${state.phase === 'error' ? 'is-error' : ''}`}
      role={state.phase === 'error' ? 'alert' : 'status'}
      data-testid="build-exact-notice"
    >
      <span>{state.message}</span>
      <button type="button" className="cv-chip-btn" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  );
}
