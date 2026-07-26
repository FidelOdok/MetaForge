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
