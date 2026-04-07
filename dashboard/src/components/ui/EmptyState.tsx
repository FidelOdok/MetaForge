interface EmptyStateProps {
  title: string;
  description?: string;
  icon?: React.ReactNode;
}

export function EmptyState({ title, description, icon }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      {icon && (
        <div className="mb-4 text-on-surface-variant opacity-40">
          {icon}
        </div>
      )}
      <h3 className="text-sm font-medium text-on-surface">
        {title}
      </h3>
      {description && (
        <p className="mt-1 font-mono text-xs text-on-surface-variant">{description}</p>
      )}
    </div>
  );
}
