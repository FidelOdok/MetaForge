import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '../components/ui/Button';
import { useToast } from '../components/ui/Toast';
import { probeGateway } from '../api/endpoints/health';
import {
  GatewayUrlError,
  describeMixedContent,
  getGatewayBase,
  isGatewayUserConfigured,
  joinAddressPort,
  setGatewayBase,
  splitGatewayBase,
  subscribeGatewayBase,
} from '../lib/gatewayConfig';

// ─── Kinetic Console design tokens ──────────────────────────────────────────
const KC = {
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  success: '#3dd68c',
  warning: '#f59e0b',
  error: '#ffb4ab',
  tertiary: '#86cfff',
  primary: '#e67e22',
  border: 'rgba(65,72,90,0.2)',
  glass: 'rgba(30,31,38,0.85)',
  surfaceHigh: '#282a30',
} as const;

const glassPanel: React.CSSProperties = {
  background: KC.glass,
  backdropFilter: 'blur(16px)',
  WebkitBackdropFilter: 'blur(16px)',
  border: `1px solid ${KC.border}`,
  borderRadius: 4,
};

const inputStyle: React.CSSProperties = {
  background: '#111319',
  border: `1px solid ${KC.border}`,
  borderRadius: 4,
  color: KC.onSurface,
  fontFamily: "'Roboto Mono', monospace",
  fontSize: 13,
  height: 34,
  padding: '0 10px',
  width: '100%',
};

type ProbeState =
  | { kind: 'idle' }
  | { kind: 'testing' }
  | { kind: 'ok'; latencyMs: number; detail: string }
  | { kind: 'fail'; message: string };

export function SettingsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const [saved, setSaved] = useState(getGatewayBase);
  const [address, setAddress] = useState(() => splitGatewayBase(getGatewayBase()).address);
  const [port, setPort] = useState(() => splitGatewayBase(getGatewayBase()).port);
  const [probe, setProbe] = useState<ProbeState>({ kind: 'idle' });

  // Another tab may change the gateway; keep this form honest about it.
  useEffect(
    () =>
      subscribeGatewayBase(() => {
        const base = getGatewayBase();
        setSaved(base);
        const parts = splitGatewayBase(base);
        setAddress(parts.address);
        setPort(parts.port);
      }),
    [],
  );

  /** The base the current form fields describe, or the validation error. */
  const draft = useMemo<{ base: string } | { error: string }>(() => {
    try {
      return { base: joinAddressPort(address, port) };
    } catch (err) {
      return { error: err instanceof GatewayUrlError ? err.message : String(err) };
    }
  }, [address, port]);

  const draftBase = 'base' in draft ? draft.base : null;
  const dirty = draftBase !== null && draftBase !== saved;
  const mixedContentWarning = draftBase ? describeMixedContent(draftBase) : null;

  const handleTest = useCallback(async () => {
    if (draftBase === null) return;
    setProbe({ kind: 'testing' });
    try {
      // An empty base means proxy mode, where /health is same-origin.
      const { latencyMs, status } = await probeGateway(draftBase);
      const detail = [status?.status, status?.version].filter(Boolean).join(' · ') || 'healthy';
      setProbe({ kind: 'ok', latencyMs, detail });
    } catch (err) {
      setProbe({ kind: 'fail', message: err instanceof Error ? err.message : String(err) });
    }
  }, [draftBase]);

  const handleSave = useCallback(() => {
    if (draftBase === null) return;
    try {
      setGatewayBase(draftBase);
      setSaved(getGatewayBase());
      // Everything already fetched came from the *old* gateway. Drop it all
      // rather than leave a page showing another gateway's data.
      queryClient.clear();
      toast.success(draftBase ? `Gateway set to ${draftBase}` : 'Gateway reset to this origin');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }, [draftBase, queryClient, toast]);

  const handleReset = useCallback(() => {
    setGatewayBase(null);
    const base = getGatewayBase();
    setSaved(base);
    const parts = splitGatewayBase(base);
    setAddress(parts.address);
    setPort(parts.port);
    setProbe({ kind: 'idle' });
    queryClient.clear();
    toast.info('Gateway override cleared');
  }, [queryClient, toast]);

  const effective = saved || `${window.location.origin} (same origin, via proxy)`;

  return (
    <div style={{ maxWidth: 760 }}>
      <header className="mb-6">
        <h1 style={{ fontSize: 20, fontWeight: 600, color: KC.onSurface }}>Settings</h1>
        <p style={{ fontSize: 13, color: KC.onSurfaceVariant, marginTop: 4 }}>
          Configuration stored in this browser only.
        </p>
      </header>

      <section style={{ ...glassPanel, padding: 20 }}>
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined" style={{ fontSize: 18, color: KC.primary }}>
            lan
          </span>
          <h2 style={{ fontSize: 14, fontWeight: 600, color: KC.onSurface }}>Gateway</h2>
        </div>
        <p style={{ fontSize: 12.5, color: KC.onSurfaceVariant, marginTop: 6, lineHeight: 1.6 }}>
          Where this dashboard sends its API calls. Leave both fields empty to use the current
          origin, which is what the bundled Docker image and the Vite dev server expect — they
          proxy to the gateway themselves. Set an address when the dashboard is hosted separately
          from the gateway, as it is on Vercel.
        </p>

        <div className="mt-5 grid gap-4" style={{ gridTemplateColumns: '1fr 120px' }}>
          <label>
            <span style={{ fontSize: 11, color: KC.onSurfaceVariant, letterSpacing: '0.04em' }}>
              ADDRESS
            </span>
            <input
              value={address}
              onChange={(e) => {
                setAddress(e.target.value);
                setProbe({ kind: 'idle' });
              }}
              placeholder="https://gateway.tailnet.ts.net"
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
              style={{ ...inputStyle, marginTop: 6 }}
            />
          </label>
          <label>
            <span style={{ fontSize: 11, color: KC.onSurfaceVariant, letterSpacing: '0.04em' }}>
              PORT
            </span>
            <input
              value={port}
              onChange={(e) => {
                setPort(e.target.value);
                setProbe({ kind: 'idle' });
              }}
              placeholder="8000"
              inputMode="numeric"
              style={{ ...inputStyle, marginTop: 6 }}
            />
          </label>
        </div>

        {'error' in draft && (
          <p style={{ marginTop: 10, fontSize: 12.5, color: KC.error }}>{draft.error}</p>
        )}

        {mixedContentWarning && (
          <p
            style={{
              marginTop: 10,
              fontSize: 12.5,
              color: KC.warning,
              lineHeight: 1.6,
              display: 'flex',
              gap: 6,
            }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
              warning
            </span>
            <span>{mixedContentWarning}</span>
          </p>
        )}

        <div className="mt-5 flex items-center gap-2">
          <Button
            variant="secondary"
            onClick={handleTest}
            disabled={draftBase === null || probe.kind === 'testing'}
          >
            {probe.kind === 'testing' ? 'Testing…' : 'Test connection'}
          </Button>
          <Button onClick={handleSave} disabled={!dirty}>
            Save
          </Button>
          {isGatewayUserConfigured() && (
            <Button variant="ghost" onClick={handleReset}>
              Reset
            </Button>
          )}
        </div>

        {probe.kind === 'ok' && (
          <p style={{ marginTop: 12, fontSize: 12.5, color: KC.success, fontFamily: "'Roboto Mono', monospace" }}>
            ✓ {probe.detail} — {probe.latencyMs} ms
          </p>
        )}
        {probe.kind === 'fail' && (
          <p style={{ marginTop: 12, fontSize: 12.5, color: KC.error, lineHeight: 1.6 }}>
            ✗ {probe.message}
          </p>
        )}

        <dl
          className="mt-5 grid gap-2"
          style={{
            gridTemplateColumns: 'max-content 1fr',
            fontSize: 12,
            borderTop: `1px solid ${KC.border}`,
            paddingTop: 16,
          }}
        >
          <dt style={{ color: KC.onSurfaceVariant }}>In use</dt>
          <dd style={{ color: KC.tertiary, fontFamily: "'Roboto Mono', monospace" }}>{effective}</dd>
          <dt style={{ color: KC.onSurfaceVariant }}>Source</dt>
          <dd style={{ color: KC.onSurface }}>
            {isGatewayUserConfigured()
              ? 'saved in this browser'
              : saved
                ? 'VITE_GATEWAY_URL build default'
                : 'same-origin proxy'}
          </dd>
        </dl>
      </section>

      <section style={{ ...glassPanel, padding: 20, marginTop: 16 }}>
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined" style={{ fontSize: 18, color: KC.warning }}>
            shield
          </span>
          <h2 style={{ fontSize: 14, fontWeight: 600, color: KC.onSurface }}>Before you expose a gateway</h2>
        </div>
        <p style={{ fontSize: 12.5, color: KC.onSurfaceVariant, marginTop: 8, lineHeight: 1.7 }}>
          The MetaForge gateway ships <strong style={{ color: KC.onSurface }}>no authentication</strong> on
          its data routes. Anything that can reach it can read and change your digital twin. Put it
          behind a private network (Tailscale) or an authenticating proxy (Cloudflare Access) rather
          than on the public internet — and note that doing so also gives you the HTTPS endpoint
          browsers require when the dashboard itself is served over HTTPS.
        </p>
      </section>
    </div>
  );
}
