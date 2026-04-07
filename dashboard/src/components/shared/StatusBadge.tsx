import { Badge } from '../ui/Badge';

const STATUS_MAP: Record<string, { variant: 'success' | 'warning' | 'error' | 'info' | 'default'; label: string }> = {
  completed:        { variant: 'success', label: 'Completed' },
  running:          { variant: 'info',    label: 'Running'   },
  pending:          { variant: 'default', label: 'Pending'   },
  failed:           { variant: 'error',   label: 'Failed'    },
  active:           { variant: 'success', label: 'Active'    },
  archived:         { variant: 'default', label: 'Archived'  },
  draft:            { variant: 'warning', label: 'Draft'     },
  valid:            { variant: 'success', label: 'Valid'     },
  warning:          { variant: 'warning', label: 'Warning'   },
  error:            { variant: 'error',   label: 'Error'     },
  unknown:          { variant: 'default', label: 'Unknown'   },
  approved:         { variant: 'success', label: 'Approved'  },
  rejected:         { variant: 'error',   label: 'Rejected'  },
  available:        { variant: 'success', label: 'Available' },
  low_stock:        { variant: 'warning', label: 'Low Stock' },
  out_of_stock:     { variant: 'error',   label: 'Out of Stock' },
  alternate_needed: { variant: 'info',    label: 'Alternate Needed' },
  ready:            { variant: 'info',    label: 'Ready'     },
  waiting:          { variant: 'default', label: 'Waiting'   },
  skipped:          { variant: 'default', label: 'Skipped'   },
  cancelled:        { variant: 'default', label: 'Cancelled' },
};

// Status dot colors for use in tables/lists
const DOT_COLORS: Record<string, string> = {
  running:   '#e67e22',
  active:    '#e67e22',
  completed: '#3dd68c',
  approved:  '#3dd68c',
  valid:     '#3dd68c',
  available: '#3dd68c',
  failed:    '#ffb4ab',
  error:     '#ffb4ab',
  rejected:  '#ffb4ab',
  out_of_stock: '#ffb4ab',
  warning:   '#f59e0b',
  low_stock: '#f59e0b',
  draft:     '#f59e0b',
};

interface StatusBadgeProps {
  status: string;
  className?: string;
  /** Show as a dot + text instead of a pill badge */
  dot?: boolean;
}

export function StatusBadge({ status, className, dot }: StatusBadgeProps) {
  const config = STATUS_MAP[status] ?? { variant: 'default' as const, label: status };

  if (dot) {
    const color = DOT_COLORS[status] ?? '#9a9aaa';
    return (
      <span className={`inline-flex items-center gap-1.5 font-mono text-xs text-on-surface-variant ${className ?? ''}`}>
        <span
          className={status === 'running' || status === 'active' ? 'live-dot' : ''}
          style={{
            display: 'inline-block',
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: color,
            flexShrink: 0,
          }}
        />
        {config.label}
      </span>
    );
  }

  return (
    <Badge variant={config.variant} className={className}>
      {config.label}
    </Badge>
  );
}
