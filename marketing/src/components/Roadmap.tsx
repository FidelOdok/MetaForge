import { Eyebrow, Icon, Lede, Section, SectionHeading } from './primitives';
import { PHASES } from '../site';

const STATUS_STYLE = {
  current: {
    dot: 'bg-primary-container',
    label: 'In development',
    labelClass: 'text-primary-container',
    card: 'border-[rgba(230,126,34,0.35)]',
  },
  next: {
    dot: 'bg-tertiary-container',
    label: 'Next',
    labelClass: 'text-tertiary',
    card: 'border-[rgba(65,72,90,0.2)]',
  },
  later: {
    dot: 'bg-surface-highest',
    label: 'Planned',
    labelClass: 'text-on-surface-variant',
    card: 'border-[rgba(65,72,90,0.2)]',
  },
} as const;

export function Roadmap() {
  return (
    <Section id="roadmap">
      <Eyebrow>Roadmap</Eyebrow>
      <SectionHeading>Three phases, stated plainly</SectionHeading>
      <Lede>
        Twenty-five engineering disciplines, one specialist agent each, arriving in three steps. We
        would rather tell you what isn't built yet than have you discover it mid-project.
      </Lede>

      <div className="mt-12 grid gap-4 lg:grid-cols-3">
        {PHASES.map((phase) => {
          const s = STATUS_STYLE[phase.status];
          return (
            <article
              key={phase.id}
              className={`flex flex-col rounded-lg border bg-surface-container p-6 ${s.card}`}
            >
              <div className="flex items-center justify-between">
                <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-on-surface-variant">
                  {phase.label} · {phase.version}
                </span>
                <span className={`flex items-center gap-1.5 text-[11px] ${s.labelClass}`}>
                  <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
                  {s.label}
                </span>
              </div>

              <h3 className="mt-4 text-[15.5px] font-medium leading-snug text-on-surface">
                {phase.headline}
              </h3>

              <ul className="mt-5 space-y-2.5">
                {phase.points.map((point) => (
                  <li key={point} className="flex gap-2.5 text-[13px] leading-relaxed text-on-surface-variant">
                    <Icon name="chevron_right" size={16} className="mt-px shrink-0 opacity-50" />
                    <span>{point}</span>
                  </li>
                ))}
              </ul>
            </article>
          );
        })}
      </div>
    </Section>
  );
}
