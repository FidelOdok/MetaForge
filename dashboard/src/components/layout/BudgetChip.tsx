import { Gauge } from 'lucide-react';
import { useActiveProject } from '../../hooks/use-active-project';
import { useBudget } from '../../hooks/use-budget';

const WARN_FRACTION = 0.8;

function usd(value: number): string {
  return `$${value.toFixed(2)}`;
}

/**
 * The AI budget of the account that owns the active project (FORGE-873), as
 * "AI $1.25 / $5.00". Shown only where a gateway enforces one: a local
 * gateway has none, and an unreadable budget is not shown as zero.
 */
export function BudgetChip() {
  const { activeProjectId } = useActiveProject();
  const budget = useBudget(activeProjectId);
  const b = budget.data;
  if (!b?.enabled || !b.available || b.monthly_usd === undefined || b.spent_usd === undefined) {
    return null;
  }
  const fraction = b.monthly_usd > 0 ? b.spent_usd / b.monthly_usd : 1;
  const state = b.exhausted ? 'exhausted' : fraction >= WARN_FRACTION ? 'warning' : 'ok';
  const period = b.period ?? 'this month';
  const title = b.exhausted
    ? `This account has used its AI budget for ${period}. Ask the account owner to raise it.`
    : `AI budget for ${period}: ${usd(b.remaining_usd ?? 0)} left` +
      (b.default_budget ? ' (default amount)' : '');
  return (
    <span className={`gateway-chip budget-chip budget-${state}`} title={title} role="status">
      <Gauge size={14} aria-hidden />
      AI {usd(b.spent_usd)} / {usd(b.monthly_usd)}
    </span>
  );
}
