import { Badge } from '../ui/Badge';

const STATUS_MAP: Record<string, { variant: 'success' | 'warning' | 'error' | 'info' | 'default'; label: string }> = {
  completed: { variant: 'success', label: 'Completed' },
  running: { variant: 'info', label: 'Running' },
  pending: { variant: 'default', label: 'Pending' },
  failed: { variant: 'error', label: 'Failed' },
  active: { variant: 'success', label: 'Active' },
  archived: { variant: 'default', label: 'Archived' },
  draft: { variant: 'warning', label: 'Draft' },
  valid: { variant: 'success', label: 'Valid' },
  warning: { variant: 'warning', label: 'Warning' },
  error: { variant: 'error', label: 'Error' },
  unknown: { variant: 'default', label: 'Unknown' },
  approved: { variant: 'success', label: 'Approved' },
  rejected: { variant: 'error', label: 'Rejected' },
  available: { variant: 'success', label: 'Available' },
  low_stock: { variant: 'warning', label: 'Low Stock' },
  out_of_stock: { variant: 'error', label: 'Out of Stock' },
  alternate_needed: { variant: 'info', label: 'Alternate Needed' },
  ready: { variant: 'info', label: 'Ready' },
  waiting: { variant: 'default', label: 'Waiting' },
  skipped: { variant: 'default', label: 'Skipped' },
  cancelled: { variant: 'default', label: 'Cancelled' },
};

interface StatusBadgeProps {
  status: string;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const config = STATUS_MAP[status] ?? { variant: 'default' as const, label: status };
  return (
    <Badge variant={config.variant} className={className}>
      {config.label}
    </Badge>
  );
}
