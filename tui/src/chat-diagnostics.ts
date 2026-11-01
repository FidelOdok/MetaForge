/**
 * Turn-level SSE stats and the cause of an empty assistant turn.
 *
 * Before this, an empty turn produced one opaque string — "(no reply — the
 * agent didn't answer)" — regardless of why: dropped-but-nonzero deltas, a
 * stream error, no events at all, or a genuinely empty answer all looked
 * identical. `describeEmptyTurn` distinguishes them so the UI line and the log
 * carry the actual cause. Kept pure (no I/O) so it is unit-testable.
 */

export interface TurnStats {
  /** Total SSE events observed this turn. */
  events: number;
  /** `message.delta` events observed. */
  deltas: number;
  /** Characters accumulated into the answer. */
  chars: number;
  /** A stream `error` event (or transport failure) occurred. */
  errored: boolean;
  errorMsg?: string;
}

export function newTurnStats(): TurnStats {
  return { events: 0, deltas: 0, chars: 0, errored: false };
}

/**
 * Human-readable cause of an empty assistant turn, or `null` when the turn
 * produced text (the normal case).
 */
export function describeEmptyTurn(s: TurnStats): string | null {
  if (s.chars > 0) return null;
  if (s.errored) return `stream error: ${s.errorMsg ?? "unknown"}`;
  if (s.events === 0) return "no stream events received (the turn never reached this thread's stream)";
  if (s.deltas > 0) {
    // Deltas arrived but carried no text — the exact fingerprint of an SSE
    // payload/parse mismatch (e.g. reading data.delta instead of the unwrapped
    // envelope). This is the class of bug that hid for so long.
    return `${s.deltas} delta event(s) but 0 characters — likely an SSE payload/parse mismatch`;
  }
  return "the agent produced no output (exhausted, or an empty answer)";
}

/** Where the full raw error always still lands (see log.ts's LOG_PATH). */
export const SESSION_LOG_HINT = "see ~/.forge/logs/session.log";

const STATUS_REASONS: Record<number, string> = {
  400: "bad request",
  401: "unauthorized",
  402: "out of credits",
  403: "forbidden",
  404: "not found",
  408: "timed out",
  409: "conflict",
  429: "rate limited",
  500: "server error",
  502: "server error",
  503: "server error",
  504: "server error",
};

// The pipeline's AllProvidersFailedError.__str__ (orchestrator/harness/providers/
// pipeline.py): "all providers failed for role '<role>': <name>:<model> -> <err>[; ...]".
const ALL_PROVIDERS_FAILED = /^all providers failed for role '[^']+':\s*(.+)$/;
const ATTEMPT = /^([^:]+):(\S+)\s*->\s*(.+)$/;
const STATUS_CODE = /(?:error code|status code|status)[:=]?\s*(\d{3})\b/i;
const MAX_LEN = 140;

/**
 * A short, single-line summary of a raw provider/transport error for on-screen
 * display (FORGE-101). The raw string can run to a full JSON error body —
 * including the provider's key-management URL and account user_id — and stays
 * pinned above the input until something overwrites it, which reads as "your
 * current model is still failing" long after it recovered. The caller is
 * responsible for logging the untouched raw string to session.log; this only
 * shortens what reaches the terminal.
 */
export function summarizeProviderError(raw: string): string {
  const text = raw.replace(/\s+/g, " ").trim();
  const allFailed = ALL_PROVIDERS_FAILED.exec(text);
  if (allFailed) {
    // Multiple candidates are joined with "; " — the last one attempted is
    // what the user's current model actually hit.
    const attempts = allFailed[1].split("; ");
    const last = attempts[attempts.length - 1] ?? allFailed[1];
    const parsed = ATTEMPT.exec(last);
    if (parsed) {
      const [, name, model, detail] = parsed;
      const codeMatch = STATUS_CODE.exec(detail);
      const code = codeMatch ? Number(codeMatch[1]) : null;
      const reason = code !== null ? (STATUS_REASONS[code] ?? "failed") : detail.slice(0, 60).trim();
      const codeSuffix = code !== null ? ` (${code})` : "";
      return `provider ${name} · ${model}: ${reason}${codeSuffix}, ${SESSION_LOG_HINT}`;
    }
  }
  if (text.length <= MAX_LEN) return text;
  return `${text.slice(0, MAX_LEN).trimEnd()}…, ${SESSION_LOG_HINT}`;
}
