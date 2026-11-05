import { forwardRef } from 'react';
import { clsx } from 'clsx';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

// Background and border come from the mf-btn-* classes in styles/page.css, so a
// link styled with buttonClasses() looks exactly like a button.
const VARIANT_STYLES: Record<Variant, string> = {
  primary:   'mf-btn-primary text-surface hover:opacity-90',
  secondary: 'mf-btn-secondary text-on-surface-variant hover:bg-surface-high hover:text-on-surface',
  ghost:     'mf-btn-ghost text-on-surface-variant hover:bg-surface-high hover:text-on-surface',
  danger:    'mf-btn-danger text-error hover:opacity-90',
};

const SIZE_STYLES: Record<Size, string> = {
  sm: 'min-h-9 px-2.5 text-sm',
  md: 'min-h-10 px-3 text-sm',
  lg: 'min-h-11 px-4 text-base',
};

/** The Button look as a className, for a <Link> or <a> that acts as a button. */
export function buttonClasses(variant: Variant = 'primary', size: Size = 'md', className?: string): string {
  return clsx(
    'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded font-medium tracking-wide transition-all',
    'focus-visible:outline-2 focus-visible:outline-primary disabled:pointer-events-none disabled:opacity-40',
    VARIANT_STYLES[variant],
    SIZE_STYLES[size],
    className,
  );
}

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = 'primary', size = 'md', ...props },
  ref,
) {
  return <button ref={ref} className={buttonClasses(variant, size, className)} {...props} />;
});
