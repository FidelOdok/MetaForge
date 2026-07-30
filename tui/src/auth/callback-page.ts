/**
 * The page shown in the browser after the ChatGPT/Codex OAuth redirect lands
 * on the local loopback server. Self-contained (no external requests — this is
 * served straight off a Node http server, not a bundled asset) and themed with
 * MetaForge's "Kinetic Console" tokens (dashboard/src/index.css) so the moment
 * a user completes reads as MetaForge, not a bare Node response.
 *
 * Kinetic Console is deliberately always-dark ("Always-dark engineering
 * dashboard" — dashboard/src/index.css), so this page stays single-theme too.
 */

const T = {
  surface: "#111319",
  surfaceLow: "#191b22",
  surfaceContainer: "#1e1f26",
  surfaceHigh: "#282a30",
  onSurface: "#e2e2eb",
  onSurfaceVariant: "#9a9aaa",
  primary: "#ffb783",
  primaryContainer: "#e67e22",
  success: "#3dd68c",
  error: "#ffb4ab",
};

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

/** Render the callback page. `ok` picks the success/failure treatment. */
export function renderCallbackPage(ok: boolean, message: string): string {
  const accent = ok ? T.success : T.error;
  const heading = ok ? "Authorization successful" : "Login failed";
  const icon = ok
    ? `<path d="M7 13.5l3.2 3.2L17.5 9" stroke="${accent}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>`
    : `<path d="M8 8l8 8M16 8l-8 8" stroke="${accent}" stroke-width="2.2" stroke-linecap="round" fill="none"/>`;

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>MetaForge · ${escapeHtml(heading)}</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; height: 100%;
    background: ${T.surface};
    color: ${T.onSurface};
    font-family: -apple-system, "Segoe UI", Inter, Roboto, Helvetica, Arial, sans-serif;
  }
  body {
    display: flex; align-items: center; justify-content: center;
    padding: 24px;
  }
  .wordmark {
    position: fixed; top: 28px; left: 50%; transform: translateX(-50%);
    font-family: ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, monospace;
    font-size: 13px; font-weight: 700; letter-spacing: 0.32em;
    color: ${T.primary}; text-transform: uppercase;
    opacity: 0.9;
  }
  .card {
    width: 100%; max-width: 380px;
    background: ${T.surfaceContainer};
    border: 1px solid ${T.surfaceHigh};
    border-radius: 14px;
    padding: 40px 32px 32px;
    text-align: center;
    box-shadow: 0 24px 60px -20px rgba(0,0,0,0.6);
    animation: rise 0.32s ease-out;
  }
  @media (prefers-reduced-motion: reduce) { .card { animation: none; } }
  @keyframes rise {
    from { opacity: 0; transform: translateY(6px) scale(0.98); }
    to   { opacity: 1; transform: translateY(0) scale(1); }
  }
  .badge {
    width: 56px; height: 56px; margin: 0 auto 20px;
    border-radius: 50%;
    background: ${T.surfaceLow};
    border: 1.5px solid ${accent};
    display: grid; place-items: center;
  }
  h1 {
    font-size: 18px; font-weight: 640; letter-spacing: -0.01em;
    margin: 0 0 8px;
  }
  p {
    font-size: 13.5px; line-height: 1.55; color: ${T.onSurfaceVariant};
    margin: 0;
  }
  .tag {
    display: inline-block; margin-top: 20px;
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    font-size: 11px; letter-spacing: 0.04em; color: ${T.onSurfaceVariant};
    background: ${T.surfaceLow}; border: 1px solid ${T.surfaceHigh};
    border-radius: 100px; padding: 4px 12px;
  }
</style>
</head>
<body>
  <div class="wordmark">Forge</div>
  <div class="card">
    <div class="badge">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none">${icon}</svg>
    </div>
    <h1>${escapeHtml(heading)}</h1>
    <p>${escapeHtml(message)}</p>
    <span class="tag">openai-codex</span>
  </div>
</body>
</html>`;
}
