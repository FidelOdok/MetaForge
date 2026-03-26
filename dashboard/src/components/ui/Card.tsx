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
        background: glass ? 'rgba(30,31,38,0.85)' : '#1e1f26',
        border: '1px solid rgba(65,72,90,0.2)',
      }}
      {...props}
    />
  );
}
