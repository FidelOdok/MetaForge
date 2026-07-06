/**
 * Generate a UUID-ish id that works in non-secure contexts.
 *
 * `crypto.randomUUID()` is only defined in secure contexts (HTTPS or
 * localhost). Over plain HTTP — e.g. the LAN/Tailscale dev host
 * `http://fidel-dev:3000` — it is `undefined`, so a bare call crashes. Prefer
 * the native method when available and fall back to a Math.random v4 shape.
 */
export function generateId(): string {
  const c = typeof crypto !== 'undefined' ? crypto : undefined;
  if (c && typeof c.randomUUID === 'function') {
    return c.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    return (ch === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}
