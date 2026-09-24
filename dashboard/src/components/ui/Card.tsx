import { clsx } from 'clsx';

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Glass variant adds backdrop-filter blur */
  glass?: boolean;
}

export function Card({ className, glass, ...props }: CardProps) {
  return (
    <div
      className={clsx(
        'rounded-lg p-5',
        glass
          ? 'glass'
          : '',
        className
      )}
      style={{
        background: glass ? 'var(--mf-r-30-31-38-0p85)' : 'var(--mf-c-1e1f26)',
        border: '1px solid var(--mf-r-65-72-90-0p2)',
      }}
      {...props}
    />
  );
}
