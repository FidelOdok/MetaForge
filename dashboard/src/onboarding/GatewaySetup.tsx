import { Button } from '../components/ui/Button';
import { useGatewayForm } from '../hooks/use-gateway-form';

/**
 * First run: tell the user what this app is and get it pointed at a gateway.
 *
 * The hosted dashboard is a viewer, not a product you sign into — MetaForge is
 * local-first, so the engine runs on the visitor's own machine and this page
 * is useless until it knows where. Until now nothing said so. A new visitor to
 * app.metaforge.uk got the full workspace chrome wrapped around empty pages,
 * with the only route out being a gear icon nothing pointed at.
 *
 * So this screen has to do two jobs, and the explanation is the harder one:
 * someone who arrived expecting a SaaS sign-up needs to understand why they
 * are being asked for an address before they will type one.
 */
export function GatewaySetup() {
  const form = useGatewayForm();
  const testing = form.test.kind === 'testing';

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    // Save reloads rather than routing onward: every cached query, and the
    // health poll that decides whether this screen shows at all, was answered
    // by a gateway that is no longer the one in use.
    if (form.save() !== null) window.location.reload();
  }

  const inputStyle: React.CSSProperties = {
    background: 'var(--mf-c-111319)',
    border: '1px solid var(--mf-r-65-72-90-0p2)',
    borderRadius: 7,
    color: 'var(--mf-c-e2e2eb)',
    fontFamily: "'Roboto Mono', monospace",
    fontSize: 14,
    height: 42,
    padding: '0 10px',
    width: '100%',
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface px-6 py-12">
      <div className="w-full max-w-lg">
        <div className="mb-8 flex items-center gap-2.5">
          <img
            src="/logo/metaforge-mark-dark.svg"
            alt=""
            aria-hidden="true"
            width={28}
            height={28}
            className="h-7 w-auto"
          />
          <span className="text-[15px] font-semibold tracking-tight text-on-surface">
            MetaForge
          </span>
        </div>

        <h1 className="text-xl font-semibold tracking-tight text-on-surface">
          Connect your gateway
        </h1>
        <p className="mt-2 text-[13.5px] leading-relaxed text-on-surface-variant">
          MetaForge is local-first: the agents, the digital twin and the tool containers all run on
          your machine, and your design files never leave it. This dashboard is a viewer — it needs
          the address of the gateway you are running.
        </p>

        <form onSubmit={onSubmit} className="mt-7 flex flex-col gap-3">
          <div className="flex gap-3">
            <label className="flex flex-1 flex-col gap-1.5">
              <span className="text-xs text-on-surface-variant">Address</span>
              <input
                value={form.address}
                onChange={(e) => form.setAddress(e.target.value)}
                placeholder="http://localhost"
                autoFocus
                spellCheck={false}
                style={inputStyle}
              />
            </label>
            <label className="flex w-28 flex-col gap-1.5">
              <span className="text-xs text-on-surface-variant">Port</span>
              <input
                value={form.port}
                onChange={(e) => form.setPort(e.target.value)}
                placeholder="8000"
                inputMode="numeric"
                spellCheck={false}
                style={inputStyle}
              />
            </label>
          </div>

          {form.error && (
            <p role="alert" className="text-[13px] text-error">
              {form.error}
            </p>
          )}
          {form.mixedContentWarning && (
            <p className="text-[13px] leading-relaxed text-warning">{form.mixedContentWarning}</p>
          )}
          {form.test.kind === 'fail' && (
            <p role="alert" className="text-[13px] leading-relaxed text-error">
              {form.test.message}
            </p>
          )}
          {/* Narrowed inline rather than via a boolean: TypeScript cannot
              carry the discriminant through `const probedOk = …`. */}
          {form.test.kind === 'ok' && (
            <p role="status" className="text-[13px] text-success">
              Reached it in {form.test.latencyMs} ms — {form.test.detail}
            </p>
          )}

          <div className="mt-1 flex items-center gap-3">
            <Button
              type="button"
              variant="secondary"
              size="lg"
              onClick={form.runTest}
              disabled={form.candidate === null || testing}
            >
              {testing ? 'Testing…' : 'Test connection'}
            </Button>
            <Button type="submit" size="lg" disabled={form.candidate === null}>
              {/* Saving without probing is allowed: a gateway behind a tunnel
                  can refuse a cross-origin probe yet still work in the app. */}
              Connect
            </Button>
          </div>
        </form>

        <div
          className="mt-8 rounded border p-4"
          style={{ borderColor: 'var(--mf-r-65-72-90-0p2)' }}
        >
          <h2 className="text-[13px] font-semibold text-on-surface">
            Haven&rsquo;t got one running?
          </h2>
          <p className="mt-1.5 text-[13px] leading-relaxed text-on-surface-variant">
            Start the stack on your own machine, then come back and enter its address:
          </p>
          <pre
            className="mt-2.5 overflow-x-auto rounded p-2.5 text-[12.5px]"
            style={{
              background: 'var(--mf-c-111319)',
              border: '1px solid var(--mf-r-65-72-90-0p2)',
              fontFamily: "'Roboto Mono', monospace",
              color: 'var(--mf-c-e2e2eb)',
            }}
          >
            docker compose up gateway
          </pre>
          <p className="mt-2.5 text-[13px] leading-relaxed text-on-surface-variant">
            It listens on <code className="text-on-surface">http://localhost:8000</code> by
            default.{' '}
            <a
              className="text-tertiary underline underline-offset-2"
              href="https://fidelodok.github.io/MetaForge/getting-started/"
              target="_blank"
              rel="noreferrer"
            >
              Getting started guide
            </a>
          </p>
        </div>

        <p className="mt-5 text-[12.5px] leading-relaxed text-on-surface-variant">
          The address is stored in this browser only. You can change it later under Settings.
        </p>
      </div>
    </div>
  );
}
