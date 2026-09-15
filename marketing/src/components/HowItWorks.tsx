import { Eyebrow, Icon, Lede, Section, SectionHeading } from './primitives';
import { PIPELINE } from '../site';

export function HowItWorks() {
  return (
    <Section id="how-it-works">
      <Eyebrow>How it works</Eyebrow>
      <SectionHeading>One path from intent to artefact</SectionHeading>
      <Lede>
        Each layer has one job and a boundary it does not cross. Agents never call a tool directly —
        every invocation goes through the MCP protocol layer, so what ran and with which version is a
        matter of record rather than a matter of trust.
      </Lede>

      <ol className="mt-12 space-y-px">
        {PIPELINE.map((step, i) => (
          <li
            key={step.name}
            className="group relative grid grid-cols-[auto_1fr] items-start gap-5 rounded-md px-4 py-5 transition-colors hover:bg-surface-container/60 sm:grid-cols-[auto_240px_1fr] sm:items-center"
          >
            {/* Connector spine: drawn on every row but the last. */}
            {i < PIPELINE.length - 1 && (
              <span
                className="absolute left-[2.15rem] top-[3.4rem] h-[calc(100%-2.4rem)] w-px bg-[rgba(65,72,90,0.3)] sm:top-[3.1rem] sm:h-[calc(100%-2.2rem)]"
                aria-hidden="true"
              />
            )}

            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-[rgba(65,72,90,0.3)] bg-surface-container text-on-surface-variant transition-colors group-hover:border-primary-container group-hover:text-primary-container">
              <Icon name={step.icon} size={18} />
            </span>

            <div className="flex items-baseline gap-3">
              <span className="font-mono text-[11px] text-on-surface-variant/70">
                {String(i + 1).padStart(2, '0')}
              </span>
              <h3 className="text-[15px] font-medium text-on-surface">{step.name}</h3>
            </div>

            <p className="col-start-2 text-[13.5px] leading-relaxed text-on-surface-variant sm:col-start-3">
              {step.detail}
            </p>
          </li>
        ))}
      </ol>
    </Section>
  );
}
