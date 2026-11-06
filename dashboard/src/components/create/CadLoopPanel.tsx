import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  STOP_TEXT,
  cadLoopKeys,
  decideCadLoop,
  getCadLoops,
  loopImageUrl,
  startCadLoop,
  type LoopRun,
  type LoopStarted,
} from '../../api/endpoints/cadLoop';
import { refusalOf } from '../../api/endpoints/create';
import { spendConfirmationOf } from '../../api/endpoints/sketchGeneration';
import { twinKeys } from '../../hooks/use-twin';

/** The newest run of this parametric plan, if any. */
export function latestRunFor(runs: LoopRun[], sketchKey: string): LoopRun | null {
  return runs.find((r) => (r.context?.sketch_ref ?? '').split('@')[0] === sketchKey) ?? null;
}

/** The newest stored contact sheet of a run: what the loop last looked at. */
export function latestSheet(run: LoopRun | null): { iteration: number; file: string } | null {
  if (!run) return null;
  const wanted = run.result?.iteration;
  const its = [...run.iterations].reverse();
  const pick = (wanted ? its.find((i) => i.n === wanted && i.render?.sheet) : null) ?? its.find((i) => i.render?.sheet);
  return pick?.render?.sheet ? { iteration: pick.n, file: pick.render.sheet } : null;
}

interface Props {
  projectId: string;
  /** The component's parametric plan (PSK-...). */
  sketchKey: string;
  componentName?: string | null;
}

/**
 * Create: *Refine the model automatically*. The loop writes and revises the
 * model's CadQuery, checks it and looks at it; progress shows on the activity
 * overlay, with the latest rendered views here. At the end the person sees the
 * result and its open issues in plain words, and chooses *Use this version* or
 * *Keep the previous one*. A paid step asks before it spends past the hourly cap.
 */
export function CadLoopPanel({ projectId, sketchKey, componentName }: Props) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [spend, setSpend] = useState<{ message: string; retry: () => Promise<LoopStarted> } | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});

  const state = useQuery({
    queryKey: cadLoopKeys.project(projectId),
    queryFn: ({ signal }) => getCadLoops(projectId, null, signal),
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && (d.active_jobs.length > 0 || d.runs.some((r) => r.status === 'running')) ? 4_000 : false;
    },
  });
  const data = state.data;
  if (!data) return null;
  const run = latestRunFor(data.runs, sketchKey);
  const running = data.active_jobs.length > 0 || run?.status === 'running';
  const sheet = latestSheet(run);
  const undecided = run?.status === 'finished' && run.result?.status === 'proposed' && !run.decision;
  const questions = run?.status === 'finished' && run.stop_reason === 'needs_input' ? run.questions ?? [] : [];

  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: cadLoopKeys.project(projectId) });
    await qc.invalidateQueries({ queryKey: twinKeys.all });
  };

  const paid = async (first: () => Promise<LoopStarted>, retry: () => Promise<LoopStarted>) => {
    setBusy(true);
    setError(null);
    try {
      await first();
      setSpend(null);
      await refresh();
    } catch (err) {
      const message = spendConfirmationOf(err);
      if (message) setSpend({ message, retry });
      else setError(refusalOf(err));
    } finally {
      setBusy(false);
    }
  };

  const start = (extra: Record<string, number | string> | null) => {
    const body = (confirm: boolean) => ({
      sketch_key: sketchKey,
      ...(extra ? { answers: extra } : {}),
      confirm_spend: confirm,
    });
    void paid(
      () => startCadLoop(projectId, body(false)),
      () => startCadLoop(projectId, body(true)),
    );
  };

  const decide = async (decision: 'accept' | 'reject') => {
    if (!run) return;
    setBusy(true);
    setError(null);
    try {
      await decideCadLoop(projectId, run.node_id, decision);
      await refresh();
    } catch (err) {
      setError(refusalOf(err));
    } finally {
      setBusy(false);
    }
  };

  const numeric = (v: string): number | string => (v.trim() !== '' && !Number.isNaN(Number(v)) ? Number(v) : v.trim());

  return (
    <section className="cv-parts" aria-label="Refine the model" data-testid="cad-loop">
      {!running && !undecided && questions.length === 0 && (
        <button
          type="button"
          className="cv-chip-btn"
          disabled={busy}
          title="The model is rewritten, checked and reviewed a few times. Paid model calls; the result is a proposal."
          onClick={() => start(null)}
        >
          Refine the model automatically
        </button>
      )}

      {running && (
        <p className="cv-parts-line" role="status" data-testid="cad-loop-running">
          Refining {componentName ?? 'the model'}
          {run?.iterations.length ? `: iteration ${run.iterations.length}` : ''}. The current model stays until you choose.
        </p>
      )}

      {run && sheet && (running || undecided) && (
        <figure className="cv-loop-views">
          <img
            src={loopImageUrl(run.node_id, sheet.file)}
            alt={`Rendered views, iteration ${sheet.iteration}`}
            data-testid="cad-loop-sheet"
            style={{ maxWidth: '100%' }}
          />
          <figcaption className="cv-parts-line">Iteration {sheet.iteration} · a picture of the proposal, not a check</figcaption>
        </figure>
      )}

      {run?.status === 'finished' && run.stop_reason && !run.decision && (
        <p className="cv-parts-line" data-testid="cad-loop-stop">
          {STOP_TEXT[run.stop_reason]}
          {run.stop_reason === 'failed' && run.stop_detail ? `: ${run.stop_detail}` : '.'}
        </p>
      )}

      {undecided && run?.result && (
        <div data-testid="cad-loop-result">
          {run.result.open_issues.length > 0 ? (
            <>
              <p className="cv-parts-line">Still open:</p>
              <ul className="cv-parts-proposals" aria-label="Open issues">
                {run.result.open_issues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            </>
          ) : (
            <p className="cv-parts-line">Nothing is open on this version.</p>
          )}
          <button type="button" className="cv-chip-btn" disabled={busy} onClick={() => void decide('accept')}>
            Use this version
          </button>
          <button type="button" className="cv-chip-btn" disabled={busy} onClick={() => void decide('reject')}>
            Keep the previous one
          </button>
        </div>
      )}

      {questions.length > 0 && (
        <form
          className="cv-parts-mapping"
          data-testid="cad-loop-questions"
          onSubmit={(e) => {
            e.preventDefault();
            const extra: Record<string, number | string> = {};
            for (const q of questions) {
              const v = answers[q.name];
              if (v && v.trim()) extra[q.name] = numeric(v);
            }
            start(extra);
          }}
        >
          <p className="cv-parts-line">It needs a number from you; nothing was guessed.</p>
          {questions.map((q) => (
            <label key={q.name}>
              <span>{q.question}</span>
              <input
                aria-label={q.question}
                value={answers[q.name] ?? ''}
                onChange={(e) => setAnswers((a) => ({ ...a, [q.name]: e.target.value }))}
              />
            </label>
          ))}
          <button
            type="submit"
            className="cv-chip-btn"
            disabled={busy || questions.some((q) => !(answers[q.name] ?? '').trim())}
          >
            Answer and continue
          </button>
        </form>
      )}

      {spend && (
        <div className="cv-stage-notice is-spend" role="alert" data-testid="cad-loop-spend">
          <span>{spend.message}</span>
          <button type="button" className="cv-chip-btn" onClick={() => void paid(spend.retry, spend.retry)}>
            Run it anyway
          </button>
          <button type="button" className="cv-chip-btn" onClick={() => setSpend(null)}>
            Not now
          </button>
        </div>
      )}
      {error && (
        <div className="cv-stage-notice is-error" role="alert">
          <span>{error}</span>
        </div>
      )}
    </section>
  );
}
