import type { AnchorHTMLAttributes, ReactNode } from 'react';

/** Material Symbols glyph. The font is loaded from index.html. */
export function Icon({ name, size = 20, className = '' }: { name: string; size?: number; className?: string }) {
  return (
    <span
      className={`material-symbols-outlined ${className}`}
      style={{ fontSize: size, lineHeight: 1 }}
      aria-hidden="true"
    >
      {name}
    </span>
  );
}

/** Small uppercase label that sits above a section heading. */
export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.18em] text-primary-container">
      {children}
    </p>
  );
}

export function SectionHeading({ children }: { children: ReactNode }) {
  return (
    <h2 className="text-2xl font-semibold tracking-tight text-on-surface sm:text-3xl">{children}</h2>
  );
}

export function Lede({ children }: { children: ReactNode }) {
  return (
    <p className="mt-4 max-w-2xl text-[15px] leading-relaxed text-on-surface-variant">{children}</p>
  );
}

/** Page section with consistent rhythm and an anchor target for the nav. */
export function Section({
  id,
  children,
  className = '',
}: {
  id?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section id={id} className={`mx-auto w-full max-w-6xl px-6 py-20 sm:py-28 ${className}`}>
      {children}
    </section>
  );
}

type ButtonLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  variant?: 'primary' | 'secondary';
  children: ReactNode;
};

export function ButtonLink({ variant = 'primary', children, className = '', ...rest }: ButtonLinkProps) {
  const base =
    'inline-flex items-center justify-center gap-2 rounded-md px-5 py-2.5 text-sm font-medium transition-all focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-container';
  const look =
    variant === 'primary'
      ? 'bg-primary-container text-surface hover:opacity-90'
      : 'border border-[rgba(65,72,90,0.35)] text-on-surface hover:bg-surface-high';
  return (
    <a className={`${base} ${look} ${className}`} {...rest}>
      {children}
    </a>
  );
}
