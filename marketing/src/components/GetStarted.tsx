import { ButtonLink, Eyebrow, Icon, Lede, Section, SectionHeading } from './primitives';
import { DASHBOARD_URL, DOCS_URL, GITHUB_URL } from '../site';

const STEPS = [
  {
    n: '01',
    title: 'Run the gateway',
    body: 'Bring up the stack with Docker Compose on your own machine. Your design files never leave it.',
    code: 'docker compose up gateway',
  },
  {
    n: '02',
    title: 'Point the dashboard at it',
    body: 'Open the hosted dashboard and set your gateway address under Settings. Nothing is stored server-side — the address lives in your browser.',
    code: 'Settings → Gateway → localhost:8000',
  },
  {
    n: '03',
    title: 'Describe what you want built',
    body: 'Work through the CLI or the dashboard. Agents propose; you approve at each gate; the results land in your repo.',
    code: 'forge chat --project my-board',
  },
] as const;

export function GetStarted() {
  return (
    <Section id="get-started">
      <Eyebrow>Get started</Eyebrow>
      <SectionHeading>Three steps, all on your hardware</SectionHeading>
      <Lede>
        The dashboard can be hosted anywhere because it is only a client. The gateway, the agents and
        the tool containers stay with you.
      </Lede>

      <div className="mt-12 grid gap-4 lg:grid-cols-3">
        {STEPS.map((step) => (
          <article key={step.n} className="rounded-lg border border-[rgba(65,72,90,0.2)] bg-surface-container p-6">
            <span className="font-mono text-[11px] tracking-[0.14em] text-primary-container">{step.n}</span>
            <h3 className="mt-3 text-[15px] font-medium text-on-surface">{step.title}</h3>
            <p className="mt-2.5 text-[13.5px] leading-relaxed text-on-surface-variant">{step.body}</p>
            <code className="mt-4 block overflow-x-auto rounded border border-[rgba(65,72,90,0.25)] bg-surface-lowest px-3 py-2 font-mono text-[12px] text-tertiary">
              {step.code}
            </code>
          </article>
        ))}
      </div>

      <div className="mt-6 flex items-start gap-3 rounded-lg border border-[rgba(245,158,11,0.25)] bg-[rgba(245,158,11,0.05)] p-5">
        <Icon name="shield" size={19} className="mt-px shrink-0 text-warning" />
        <p className="text-[13px] leading-relaxed text-on-surface-variant">
          <strong className="font-medium text-on-surface">One caution.</strong> The gateway currently
          ships without authentication on its data routes, so keep it on a private network — Tailscale
          or an authenticating proxy such as Cloudflare Access — rather than exposing it to the open
          internet. That also gives you the HTTPS endpoint a browser needs when the dashboard is
          served over HTTPS.
        </p>
      </div>

      <div className="mt-12 flex flex-wrap items-center gap-3">
        <ButtonLink href={DASHBOARD_URL}>
          Open the dashboard
          <Icon name="arrow_forward" size={17} />
        </ButtonLink>
        <ButtonLink href={DOCS_URL} target="_blank" rel="noreferrer" variant="secondary">
          Getting started guide
        </ButtonLink>
        <ButtonLink href={GITHUB_URL} target="_blank" rel="noreferrer" variant="secondary">
          Source on GitHub
        </ButtonLink>
      </div>
    </Section>
  );
}
