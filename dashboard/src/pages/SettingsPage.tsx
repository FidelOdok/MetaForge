import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '../components/ui/Button';
import { useToast } from '../components/ui/Toast';
import { useHarnessModels, useHarnessProviders } from '../hooks/use-harness';
import {
  removeProviderKey,
  saveProviderKey,
  selectProviderModel,
} from '../api/endpoints/harness-credentials';
import { probeGateway } from '../api/endpoints/health';
import {
  GatewayUrlError,
  apiBase,
  describeMixedContent,
  getGatewayBase,
  isGatewayUserConfigured,
  joinAddressPort,
  setGatewayBase,
  splitGatewayBase,
  subscribeGatewayBase,
} from '../lib/gatewayConfig';

const C = {
  onSurface: 'var(--mf-c-e2e2eb)',
  onSurfaceVariant: 'var(--mf-c-9a9aaa)',
  success: 'var(--mf-c-3dd68c)',
  warning: 'var(--mf-c-f59e0b)',
  error: 'var(--mf-c-ffb4ab)',
  tertiary: 'var(--mf-c-86cfff)',
  primary: '#ff5a0a',
  border: 'var(--mf-r-65-72-90-0p2)',
  glass: 'var(--mf-r-30-31-38-0p85)',
};

const GLASS: React.CSSProperties = {
  background: C.glass,
  backdropFilter: 'blur(16px)',
  WebkitBackdropFilter: 'blur(16px)',
  border: `1px solid ${C.border}`,
  borderRadius: 7,
};

const INPUT: React.CSSProperties = {
  background: 'var(--mf-c-111319)',
  border: `1px solid ${C.border}`,
  borderRadius: 7,
  color: C.onSurface,
  fontFamily: "'Roboto Mono', monospace",
  fontSize: 14,
  height: 42,
  padding: '0 10px',
  width: '100%',
};

const FIELD_LABEL: React.CSSProperties = {
  fontSize: 14,
  color: C.onSurfaceVariant,
  letterSpacing: '0.04em',
};

type TestState =
  | { kind: 'idle' }
  | { kind: 'testing' }
  | { kind: 'ok'; latencyMs: number; detail: string }
  | { kind: 'fail'; message: string };

type ProviderAction = 'save' | 'remove' | 'select';

/** True when credentials may be sent to this gateway (HTTPS or loopback). */
function isSecureDestination(base: string): boolean {
  if (!base) return true;
  try {
    const url = new URL(base);
    return url.protocol === 'https:' || ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  } catch {
    return false;
  }
}

// ── "Your models" panel ──────────────────────────────────────────────────────

function ProvidersPanel() {
  const queryClient = useQueryClient();
  const [gateway, setGateway] = useState(getGatewayBase);
  const [providerId, setProviderId] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [adminToken, setAdminToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState('');
  const [confirmRemove, setConfirmRemove] = useState(false);

  useEffect(
    () =>
      subscribeGatewayBase(() => {
        setGateway(getGatewayBase());
        setApiKey('');
        setProviderId('');
        setModel('');
        setNotice('');
        setFailure('');
      }),
    [],
  );

  const providers = useHarnessProviders();
  const selected = providers.data?.providers.find((p) => p.id === providerId);
  const models = useHarnessModels(selected?.configured ? selected.id : null);
  const secure = isSecureDestination(gateway);

  async function run(action: ProviderAction) {
    if (!selected || busy || !secure) return;
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      if (action === 'save') await saveProviderKey(providerId, apiKey.trim(), adminToken);
      if (action === 'remove') await removeProviderKey(providerId, adminToken);
      if (action === 'select') await selectProviderModel(providerId, model, adminToken);
      setNotice(
        action === 'save'
          ? 'Key saved to your gateway.'
          : action === 'remove'
            ? 'Stored key removed. Environment-provided credentials may still be available.'
            : 'Active provider and model updated.',
      );
      await queryClient.invalidateQueries({ queryKey: ['harness'] });
    } catch {
      setFailure(
        'The gateway could not complete this request. Check its connection and provider configuration, then try again.',
      );
    } finally {
      setBusy(false);
      setApiKey('');
      setConfirmRemove(false);
    }
  }

  return (
    <section className="account-panel" aria-labelledby="providers-heading">
      <p className="account-eyebrow">YOUR MODELS</p>
      <h2 id="providers-heading">Bring your own API key</h2>
      <p>
        Connect a provider for your engineering agents. Credentials are sent to the gateway below
        and are never saved in this browser.
      </p>
      <div className="account-note">
        <strong>Gateway destination</strong>
        <code>{gateway ? apiBase() : `${window.location.origin}${apiBase()}`}</code>
        <span>
          Credentials and the active model apply to this gateway, including other people using it.
        </span>
      </div>

      {providers.isPending && <p role="status">Loading providers…</p>}
      {providers.isError && (
        <p role="alert">
          Connect your gateway above to load its registered providers.{' '}
          <button type="button" className="text-action" onClick={() => providers.refetch()}>
            Retry
          </button>
        </p>
      )}

      {providers.data && (
        <>
          <p>
            Active: <strong>{providers.data.activeProvider || 'No provider selected'}</strong>
            {providers.data.activeModel ? ` · ${providers.data.activeModel}` : ''}
          </p>
          {!providers.data.providers.length && <p>No providers are registered on this gateway.</p>}
          <label className="account-field">
            Provider
            <select
              value={providerId}
              disabled={busy}
              onChange={(e) => {
                setProviderId(e.target.value);
                setApiKey('');
                setModel('');
                setNotice('');
                setFailure('');
                setConfirmRemove(false);
              }}
            >
              <option value="">Select a provider</option>
              {providers.data.providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id} · {p.configured ? 'Configured' : 'Not configured'}
                </option>
              ))}
            </select>
          </label>

          {selected && (
            <>
              <label className="account-field">
                Admin token (optional)
                <input
                  type="password"
                  value={adminToken}
                  onChange={(e) => setAdminToken(e.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={busy}
                  placeholder="Only if the gateway sets METAFORGE_HARNESS_ADMIN_TOKEN"
                  aria-describedby="admin-help"
                />
              </label>
              <p id="admin-help">
                Sent as the X-MetaForge-Admin header with each change below. It is not stored.
              </p>

              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void run('save');
                }}
              >
                <label className="account-field">
                  {selected.configured ? 'Replace API key' : 'API key'}
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    disabled={busy}
                    required
                    placeholder="Paste your provider API key"
                    aria-describedby="key-help"
                  />
                </label>
                <p id="key-help">
                  Use an API key issued by {selected.id}. OAuth-only providers must be connected
                  through the gateway.
                </p>
                <button
                  className="action-primary"
                  disabled={busy || !apiKey.trim() || !secure}
                  type="submit"
                >
                  {busy ? 'Saving changes…' : 'Save key to gateway'}
                </button>
              </form>

              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void run('select');
                }}
              >
                <label className="account-field">
                  Model
                  <input
                    list="provider-models"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    disabled={busy || !selected.configured}
                    placeholder="Model ID, or leave blank for provider default"
                  />
                  <datalist id="provider-models">
                    {models.data?.map((m) => <option key={m} value={m} />)}
                  </datalist>
                </label>
                {models.isError && (
                  <p>Model discovery is unavailable. You can enter a model ID manually.</p>
                )}
                <button
                  className="action-secondary"
                  type="submit"
                  disabled={busy || !selected.configured || !secure}
                >
                  Use this provider and model
                </button>
              </form>

              {selected.configured && (
                <div className="account-remove">
                  {confirmRemove ? (
                    <>
                      <p>Remove the stored key for {providerId} from this gateway?</p>
                      <button
                        type="button"
                        className="action-secondary"
                        disabled={busy || !secure}
                        onClick={() => void run('remove')}
                      >
                        Confirm removal
                      </button>{' '}
                      <button
                        type="button"
                        className="text-action"
                        disabled={busy}
                        onClick={() => setConfirmRemove(false)}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="text-action"
                      onClick={() => setConfirmRemove(true)}
                      disabled={busy}
                    >
                      Remove stored key
                    </button>
                  )}
                </div>
              )}
            </>
          )}
        </>
      )}

      {!secure && <p role="alert">Use an HTTPS gateway address before sending credentials.</p>}
      {notice && <p role="status">{notice}</p>}
      {failure && <p role="alert">{failure}</p>}
    </section>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function SettingsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [inUse, setInUse] = useState(getGatewayBase);
  const [address, setAddress] = useState(() => splitGatewayBase(getGatewayBase()).address);
  const [port, setPort] = useState(() => splitGatewayBase(getGatewayBase()).port);
  const [test, setTest] = useState<TestState>({ kind: 'idle' });
  const [overridden, setOverridden] = useState(isGatewayUserConfigured);

  useEffect(
    () =>
      subscribeGatewayBase(() => {
        const base = getGatewayBase();
        setInUse(base);
        setOverridden(isGatewayUserConfigured());
        const parts = splitGatewayBase(base);
        setAddress(parts.address);
        setPort(parts.port);
      }),
    [],
  );

  const parsed = useMemo<{ base: string } | { error: string }>(() => {
    try {
      return { base: joinAddressPort(address, port) };
    } catch (err) {
      return { error: err instanceof GatewayUrlError ? err.message : String(err) };
    }
  }, [address, port]);

  const candidate = 'base' in parsed ? parsed.base : null;
  const dirty = candidate !== null && candidate !== inUse;
  const warning = candidate ? describeMixedContent(candidate) : null;

  const handleTest = useCallback(async () => {
    if (candidate === null) return;
    setTest({ kind: 'testing' });
    try {
      const { latencyMs, status } = await probeGateway(candidate);
      const detail = [status?.status, status?.version].filter(Boolean).join(' · ') || 'healthy';
      setTest({ kind: 'ok', latencyMs, detail });
    } catch (err) {
      setTest({ kind: 'fail', message: err instanceof Error ? err.message : String(err) });
    }
  }, [candidate]);

  const handleSave = useCallback(() => {
    if (candidate === null) return;
    try {
      setGatewayBase(candidate);
      setInUse(getGatewayBase());
      setOverridden(isGatewayUserConfigured());
      queryClient.clear();
      toast.success(candidate ? `Gateway set to ${candidate}` : 'Gateway reset to this origin');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }, [candidate, queryClient, toast]);

  const handleReset = useCallback(() => {
    setGatewayBase(null);
    const base = getGatewayBase();
    setInUse(base);
    setOverridden(false);
    const parts = splitGatewayBase(base);
    setAddress(parts.address);
    setPort(parts.port);
    setTest({ kind: 'idle' });
    queryClient.clear();
    toast.info('Gateway override cleared');
  }, [queryClient, toast]);

  const inUseLabel = inUse || `${window.location.origin} (same origin, via proxy)`;

  return (
    <div style={{ maxWidth: 880 }}>
      <header className="mb-6">
        <h1 style={{ fontSize: 30, fontWeight: 600, color: C.onSurface }}>Settings</h1>
        <p style={{ fontSize: 14, color: C.onSurfaceVariant, marginTop: 4 }}>
          Manage your gateway and AI providers.
        </p>
      </header>

      <section style={{ ...GLASS, padding: 20 }} aria-labelledby="gateway-heading">
        <div className="flex items-center gap-2">
          <span
            className="material-symbols-outlined"
            aria-hidden="true"
            style={{ fontSize: 18, color: C.primary }}
          >
            lan
          </span>
          <h2 id="gateway-heading" style={{ fontSize: 14, fontWeight: 600, color: C.onSurface }}>
            Gateway
          </h2>
        </div>
        <p style={{ fontSize: 14, color: C.onSurfaceVariant, marginTop: 6, lineHeight: 1.6 }}>
          Connect this workspace to your MetaForge gateway. Enter its reachable HTTPS address, test
          the connection, then save. Leave both fields empty only when running the dashboard with
          the bundled local server or Docker proxy.
        </p>

        <div className="gateway-fields mt-5 grid gap-4">
          <label>
            <span style={FIELD_LABEL}>ADDRESS</span>
            <input
              value={address}
              onChange={(e) => {
                setAddress(e.target.value);
                setTest({ kind: 'idle' });
              }}
              placeholder="https://gateway.tailnet.ts.net"
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
              style={{ ...INPUT, marginTop: 6 }}
            />
          </label>
          <label>
            <span style={FIELD_LABEL}>PORT</span>
            <input
              value={port}
              onChange={(e) => {
                setPort(e.target.value);
                setTest({ kind: 'idle' });
              }}
              placeholder="8000"
              inputMode="numeric"
              style={{ ...INPUT, marginTop: 6 }}
            />
          </label>
        </div>

        {'error' in parsed && (
          <p style={{ marginTop: 10, fontSize: 14, color: C.error }}>{parsed.error}</p>
        )}
        {warning && (
          <p
            style={{
              marginTop: 10,
              fontSize: 14,
              color: C.warning,
              lineHeight: 1.6,
              display: 'flex',
              gap: 6,
            }}
          >
            <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 16 }}>
              warning
            </span>
            <span>{warning}</span>
          </p>
        )}

        <div className="mt-5 flex items-center gap-2">
          <Button
            variant="secondary"
            onClick={() => void handleTest()}
            disabled={candidate === null || test.kind === 'testing'}
          >
            {test.kind === 'testing' ? 'Testing…' : 'Test connection'}
          </Button>
          <Button onClick={handleSave} disabled={!dirty}>
            Save
          </Button>
          {overridden && (
            <Button variant="ghost" onClick={handleReset}>
              Reset
            </Button>
          )}
        </div>

        {test.kind === 'ok' && (
          <p
            role="status"
            style={{
              marginTop: 12,
              fontSize: 14,
              color: C.success,
              fontFamily: "'Roboto Mono', monospace",
            }}
          >
            ✓ {test.detail} · {test.latencyMs} ms
          </p>
        )}
        {test.kind === 'fail' && (
          <p role="alert" style={{ marginTop: 12, fontSize: 14, color: C.error, lineHeight: 1.6 }}>
            ✗ {test.message}
          </p>
        )}

        <dl
          className="gateway-details mt-5 grid gap-2"
          style={{
            gridTemplateColumns: 'max-content 1fr',
            fontSize: 14,
            borderTop: `1px solid ${C.border}`,
            paddingTop: 16,
          }}
        >
          <dt style={{ color: C.onSurfaceVariant }}>In use</dt>
          <dd
            data-testid="gateway-in-use"
            style={{
              color: C.tertiary,
              fontFamily: "'Roboto Mono', monospace",
              overflowWrap: 'anywhere',
            }}
          >
            {inUseLabel}
          </dd>
          <dt style={{ color: C.onSurfaceVariant }}>Source</dt>
          <dd data-testid="gateway-source" style={{ color: C.onSurface }}>
            {overridden
              ? 'saved in this browser'
              : inUse
                ? 'VITE_GATEWAY_URL build default'
                : 'same-origin proxy'}
          </dd>
        </dl>
      </section>

      <ProvidersPanel />

      <section style={{ ...GLASS, padding: 20, marginTop: 16 }} aria-labelledby="expose-heading">
        <div className="flex items-center gap-2">
          <span
            className="material-symbols-outlined"
            aria-hidden="true"
            style={{ fontSize: 18, color: C.warning }}
          >
            shield
          </span>
          <h2 id="expose-heading" style={{ fontSize: 14, fontWeight: 600, color: C.onSurface }}>
            Before you expose a gateway
          </h2>
        </div>
        <p style={{ fontSize: 14, color: C.onSurfaceVariant, marginTop: 8, lineHeight: 1.7 }}>
          The MetaForge gateway ships <strong style={{ color: C.onSurface }}>no authentication</strong>{' '}
          on its data routes. Anything that can reach it can read and change your digital twin. Put
          it behind a private network (Tailscale) or an authenticating proxy (Cloudflare Access)
          rather than on the public internet, and note that doing so also gives you the HTTPS
          endpoint browsers require when the dashboard itself is served over HTTPS.
        </p>
      </section>
    </div>
  );
}
