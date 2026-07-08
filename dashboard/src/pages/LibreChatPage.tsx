import { useMemo, useState } from 'react';

// Kinetic Console tokens (mirrors the inline palette used across the shell).
const KC = {
  surface: '#111319',
  surfaceLow: '#191b22',
  border: 'rgba(65,72,90,0.2)',
  borderMid: 'rgba(65,72,90,0.3)',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  orange: '#e67e22',
} as const;

/**
 * LibreChat — MetaForge's flagship chat/agent surface (MET-552).
 *
 * LibreChat runs as its own app (Docker) and reaches MetaForge's tools over
 * MCP. We embed it here so it lives inside the dashboard shell. The URL is
 * configurable via `VITE_LIBRECHAT_URL`; the default assumes LibreChat is on
 * the same host at :3080. LibreChat may send framing headers that block the
 * iframe — the "Open in new tab" action is always available as a fallback.
 */
export function LibreChatPage() {
  const url = useMemo(() => {
    const configured = import.meta.env.VITE_LIBRECHAT_URL as string | undefined;
    if (configured && configured.trim()) return configured.trim();
    if (typeof window !== 'undefined') {
      return `${window.location.protocol}//${window.location.hostname}:3080`;
    }
    return '';
  }, []);

  // Bump to force the iframe to reload.
  const [reloadKey, setReloadKey] = useState(0);

  return (
    <div
      style={{
        position: 'relative',
        margin: -24, // escape AppLayout's p-6
        height: 'calc(100vh - 40px)', // 40px topbar
        display: 'flex',
        flexDirection: 'column',
        background: KC.surface,
        overflow: 'hidden',
      }}
    >
      {/* Header bar */}
      <div
        className="flex items-center gap-2 px-3 flex-shrink-0"
        style={{ height: 36, borderBottom: `1px solid ${KC.border}`, background: KC.surfaceLow }}
      >
        <span className="material-symbols-outlined" style={{ fontSize: 16, color: KC.orange }}>
          forum
        </span>
        <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurface }}>
          Assistant
        </span>
        <span className="font-mono truncate" style={{ fontSize: 11, color: KC.onSurfaceVariant, maxWidth: 360 }}>
          {url}
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          title="Reload"
          onClick={() => setReloadKey((k) => k + 1)}
          className="flex items-center justify-center rounded"
          style={{ width: 26, height: 26, background: 'transparent', border: 'none', color: KC.onSurfaceVariant, cursor: 'pointer' }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>refresh</span>
        </button>
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          title="Open in new tab"
          className="flex items-center gap-1 rounded px-2"
          style={{ height: 26, border: `1px solid ${KC.borderMid}`, color: KC.onSurfaceVariant, fontSize: 11, textDecoration: 'none' }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>open_in_new</span>
          Open
        </a>
      </div>

      {/* Embedded LibreChat */}
      {url ? (
        <iframe
          key={reloadKey}
          title="LibreChat"
          src={url}
          style={{ flex: 1, width: '100%', border: 'none', background: KC.surface }}
          allow="clipboard-write; clipboard-read"
        />
      ) : (
        <div className="flex flex-1 items-center justify-center">
          <p className="font-mono text-xs" style={{ color: KC.onSurfaceVariant }}>
            Set VITE_LIBRECHAT_URL to embed the assistant.
          </p>
        </div>
      )}
    </div>
  );
}
