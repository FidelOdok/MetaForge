import { ButtonLink, Icon } from './primitives';
import { DASHBOARD_URL, DOCS_URL } from '../site';

/** The one-line pitch rendered as a terminal transcript, not a screenshot. */
function TerminalCard() {
  const lines: Array<{ text: string; tone: 'cmd' | 'muted' | 'ok' | 'warn' }> = [
    { text: '$ forge chat --project drone-fc', tone: 'cmd' },
    { text: '› Size the standoffs for a 36 g board at 12 g vertical load.', tone: 'muted' },
    { text: '  → freecad.create_parametric   standoff_m3 × 4', tone: 'muted' },
    { text: '  → calculix.run_fea            max von Mises 41.2 MPa', tone: 'muted' },
    { text: '  ✓ margin 4.1× against PA12 yield', tone: 'ok' },
    { text: '  ⧗ gate: mechanical review — awaiting your approval', tone: 'warn' },
  ];
  const tones = {
    cmd: 'text-on-surface',
    muted: 'text-on-surface-variant',
    ok: 'text-success',
    warn: 'text-warning',
  } as const;

  return (
    <div className="glass overflow-hidden rounded-lg">
      <div className="flex items-center gap-2 border-b border-[rgba(65,72,90,0.2)] px-4 py-2.5">
        <span className="h-2.5 w-2.5 rounded-full bg-[#33343b]" />
        <span className="h-2.5 w-2.5 rounded-full bg-[#33343b]" />
        <span className="h-2.5 w-2.5 rounded-full bg-[#33343b]" />
        <span className="ml-2 font-mono text-[11px] text-on-surface-variant">forge</span>
      </div>
      <pre className="overflow-x-auto px-4 py-4 font-mono text-[12.5px] leading-[1.9]">
        {lines.map((line) => (
          <div key={line.text} className={tones[line.tone]}>
            {line.text}
          </div>
        ))}
      </pre>
    </div>
  );
}

export function Hero() {
  return (
    <div id="top" className="relative overflow-hidden pt-16">
      <div className="blueprint-grid pointer-events-none absolute inset-0 -z-10" aria-hidden="true" />

      <div className="mx-auto w-full max-w-6xl px-6 pb-20 pt-20 sm:pb-28 sm:pt-28">
        <div className="grid items-center gap-14 lg:grid-cols-[1.05fr_1fr]">
          <div>
            <p className="mb-5 inline-flex items-center gap-2 rounded-full border border-[rgba(65,72,90,0.3)] px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] text-on-surface-variant">
              <span className="h-1.5 w-1.5 rounded-full bg-primary-container" />
              Phase 1 · in development
            </p>

            <h1 className="text-[2.4rem] font-semibold leading-[1.1] tracking-tight text-on-surface sm:text-[3.25rem]">
              Hardware design that
              <br />
              <span className="text-primary-container">survives review.</span>
            </h1>

            <p className="mt-6 max-w-xl text-[16.5px] leading-relaxed text-on-surface-variant">
              MetaForge is a local-first control plane that turns human intent into manufacturable
              deliverables. Specialist agents drive the real tools — KiCad, FreeCAD, CalculiX — and
              everything they produce lands in your repository as a file you can diff.
            </p>

            <div className="mt-9 flex flex-wrap items-center gap-3">
              <ButtonLink href={DASHBOARD_URL}>
                Open the dashboard
                <Icon name="arrow_forward" size={17} />
              </ButtonLink>
              <ButtonLink href={DOCS_URL} target="_blank" rel="noreferrer" variant="secondary">
                Read the docs
              </ButtonLink>
            </div>

            <p className="mt-9 border-l-2 border-primary-container pl-4 text-[13.5px] leading-relaxed text-on-surface-variant">
              <strong className="font-medium text-on-surface">The prime rule.</strong> If it can't be
              versioned, reviewed and built, MetaForge doesn't output it.
            </p>
          </div>

          <TerminalCard />
        </div>
      </div>
    </div>
  );
}
