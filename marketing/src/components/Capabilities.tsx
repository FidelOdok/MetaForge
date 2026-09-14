import { Eyebrow, Icon, Lede, Section, SectionHeading } from './primitives';
import { CAPABILITIES, PRINCIPLES } from '../site';

export function Capabilities() {
  return (
    <Section id="capabilities">
      <Eyebrow>Capabilities</Eyebrow>
      <SectionHeading>What it does today</SectionHeading>
      <Lede>
        Not a roadmap in disguise. This is the Phase 1 surface — the work the agents can actually
        carry out against real tools right now.
      </Lede>

      <div className="mt-12 grid gap-px overflow-hidden rounded-lg border border-[rgba(65,72,90,0.2)] bg-[rgba(65,72,90,0.2)] sm:grid-cols-2 lg:grid-cols-3">
        {CAPABILITIES.map((cap) => (
          <article key={cap.title} className="flex flex-col bg-surface-container p-6">
            <div className="flex items-center gap-2.5">
              <Icon name={cap.icon} size={19} className="text-primary-container" />
              <h3 className="text-[15px] font-medium text-on-surface">{cap.title}</h3>
            </div>
            <p className="mt-3 flex-1 text-[13.5px] leading-relaxed text-on-surface-variant">
              {cap.body}
            </p>
            {'tag' in cap && cap.tag && (
              <p className="mt-4 inline-flex w-fit rounded border border-[rgba(65,72,90,0.35)] px-2 py-0.5 font-mono text-[10.5px] uppercase tracking-[0.1em] text-tertiary">
                {cap.tag}
              </p>
            )}
          </article>
        ))}
      </div>

      <div className="mt-24">
        <Eyebrow>Principles</Eyebrow>
        <SectionHeading>Why it's built this way</SectionHeading>

        <div className="mt-10 grid gap-10 sm:grid-cols-2">
          {PRINCIPLES.map((p) => (
            <div key={p.title} className="flex gap-4">
              <Icon name={p.icon} size={20} className="mt-0.5 shrink-0 text-tertiary-container" />
              <div>
                <h3 className="text-[15px] font-medium text-on-surface">{p.title}</h3>
                <p className="mt-2 text-[13.5px] leading-relaxed text-on-surface-variant">{p.body}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </Section>
  );
}
