import { clsx } from 'clsx';

type Variant = 'default' | 'success' | 'warning' | 'error' | 'info';

const VARIANT_STYLES: Record<Variant, string> = {
  default: 'text-on-surface-variant',
  success: 'text-success',
  warning: 'text-warning',
  error:   'text-error',
  info:    'text-tertiary',
};

const VARIANT_INLINE: Record<Variant, React.CSSProperties> = {
  default: { background: 'rgba(40,42,48,0.8)',    border: '1px solid rgba(65,72,90,0.3)' },
  success: { background: 'rgba(61,214,140,0.1)',  border: '1px solid rgba(61,214,140,0.25)' },
  warning: { background: 'rgba(245,158,11,0.1)',  border: '1px solid rgba(245,158,11,0.25)' },
  error:   { background: 'rgba(255,180,171,0.1)', border: '1px solid rgba(255,180,171,0.25)' },
  info:    { background: 'rgba(134,207,255,0.1)', border: '1px solid rgba(134,207,255,0.25)' },
};

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: Variant;
}

export function Badge({ className, variant = 'default', style, ...props }: BadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-full px-2 py-0.5 font-mono text-[10px] tracking-wide',
        VARIANT_STYLES[variant],
        className
      )}
      style={{ ...VARIANT_INLINE[variant], ...style }}
      {...props}
    />
  );
}
