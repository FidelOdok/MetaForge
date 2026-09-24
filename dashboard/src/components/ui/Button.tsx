import { clsx } from 'clsx';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

const VARIANT_STYLES: Record<Variant, string> = {
  primary:   'text-surface hover:opacity-90',
  secondary: 'text-on-surface-variant hover:bg-surface-high hover:text-on-surface',
  ghost:     'text-on-surface-variant hover:bg-surface-high hover:text-on-surface',
  danger:    'text-error hover:opacity-90',
};

const VARIANT_INLINE: Record<Variant, React.CSSProperties> = {
  primary:   { background: '#ff5a0a', border: 'none' },
  secondary: { background: 'transparent', border: '1px solid var(--mf-r-65-72-90-0p3)' },
  ghost:     { background: 'transparent', border: 'none' },
  danger:    { background: 'rgba(255,180,171,0.1)', border: '1px solid rgba(255,180,171,0.2)' },
};

const SIZE_STYLES: Record<Size, string> = {
  sm: 'min-h-9 px-2.5 text-sm',
  md: 'min-h-10 px-3 text-sm',
  lg: 'min-h-11 px-4 text-base',
};

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export function Button({ className, variant = 'primary', size = 'md', style, ...props }: ButtonProps) {
  return (
    <button
      className={clsx(
        'inline-flex items-center justify-center rounded font-medium tracking-wide transition-all',
        'focus-visible:outline-2 focus-visible:outline-primary disabled:pointer-events-none disabled:opacity-40',
        VARIANT_STYLES[variant],
        SIZE_STYLES[size],
        className
      )}
      style={{ ...VARIANT_INLINE[variant], ...style }}
      {...props}
    />
  );
}
