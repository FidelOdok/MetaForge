interface EmptyStateProps {
  title: string;
  description?: string;
  icon?: React.ReactNode;
}

/** The Files page's empty state: a faint icon, a short line and, when needed, one more. */
export function EmptyState({ title, description, icon }: EmptyStateProps) {
  return (
    <div className="mf-empty">
      {icon && <div className="mf-empty-icon">{icon}</div>}
      <h3>{title}</h3>
      {description && <p>{description}</p>}
    </div>
  );
}
