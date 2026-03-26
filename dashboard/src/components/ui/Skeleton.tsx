import { clsx } from 'clsx';

interface SkeletonProps {
  className?: string;
}

export function Skeleton({ className }: SkeletonProps) {
  return (
    <div
      className={clsx('animate-pulse rounded', className)}
      style={{ background: '#282a30' }}
    />
  );
}

export function SkeletonCard() {
  return (
    <div
      className="rounded-lg p-5"
      style={{ background: '#1e1f26', border: '1px solid rgba(65,72,90,0.2)' }}
    >
      <div className="mb-3 flex items-start justify-between">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-4 w-16 rounded-full" />
      </div>
      <Skeleton className="mb-2 h-3 w-full" />
      <Skeleton className="mb-4 h-3 w-3/4" />
      <div className="flex items-center justify-between">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-3 w-16" />
      </div>
    </div>
  );
}

interface SkeletonTableProps {
  rows?: number;
  cols?: number;
}

export function SkeletonTable({ rows = 5, cols = 4 }: SkeletonTableProps) {
  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ background: '#1e1f26', border: '1px solid rgba(65,72,90,0.2)' }}
    >
      <div
        className="flex gap-3 px-3 py-2"
        style={{ borderBottom: '1px solid rgba(65,72,90,0.2)', background: '#191b22' }}
      >
        {Array.from({ length: cols }).map((_, i) => (
          <Skeleton key={i} className="h-3 flex-1" />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, rowIdx) => (
        <div
          key={rowIdx}
          className="flex gap-3 px-3 py-2.5"
          style={{ borderBottom: rowIdx < rows - 1 ? '1px solid rgba(65,72,90,0.1)' : 'none' }}
        >
          {Array.from({ length: cols }).map((_, colIdx) => (
            <Skeleton
              key={colIdx}
              className={clsx('h-3 flex-1', colIdx === 0 && 'w-24 flex-none')}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

interface SkeletonListProps {
  rows?: number;
}

export function SkeletonList({ rows = 5 }: SkeletonListProps) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-4 rounded-lg p-5"
          style={{ background: '#1e1f26', border: '1px solid rgba(65,72,90,0.2)' }}
        >
          <Skeleton className="h-9 w-9 shrink-0 rounded-full" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-3 w-32" />
          </div>
          <Skeleton className="h-4 w-16 rounded-full" />
        </div>
      ))}
    </div>
  );
}
