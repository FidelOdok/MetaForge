import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const mockUseBudget = vi.fn();
vi.mock('../../../hooks/use-budget', () => ({ useBudget: (id: string) => mockUseBudget(id) }));
vi.mock('../../../hooks/use-active-project', () => ({
  useActiveProject: () => ({ activeProjectId: 'p-1', projects: [], setActiveProjectId: vi.fn() }),
}));

import { BudgetChip } from '../BudgetChip';

const month = { enabled: true, available: true, period: '2026-11', monthly_usd: 5 };

describe('BudgetChip', () => {
  beforeEach(() => mockUseBudget.mockReset());

  it('asks for the active project', () => {
    mockUseBudget.mockReturnValue({ data: undefined });
    render(<BudgetChip />);
    expect(mockUseBudget).toHaveBeenCalledWith('p-1');
  });

  it.each([
    [undefined],
    [{ enabled: false }],
    // Unreadable is not shown as zero.
    [{ enabled: true, available: false }],
  ])('shows nothing without a budget to report (%j)', (data) => {
    mockUseBudget.mockReturnValue({ data });
    const { container } = render(<BudgetChip />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows spend against the budget', () => {
    mockUseBudget.mockReturnValue({
      data: { ...month, spent_usd: 1.25, remaining_usd: 3.75, exhausted: false },
    });
    render(<BudgetChip />);
    const chip = screen.getByRole('status');
    expect(chip).toHaveTextContent('AI $1.25 / $5.00');
    expect(chip).toHaveClass('budget-ok');
    expect(chip).toHaveAttribute('title', 'AI budget for 2026-11: $3.75 left');
  });

  it('warns from 80 percent', () => {
    mockUseBudget.mockReturnValue({
      data: { ...month, spent_usd: 4.1, remaining_usd: 0.9, exhausted: false, default_budget: true },
    });
    render(<BudgetChip />);
    const chip = screen.getByRole('status');
    expect(chip).toHaveClass('budget-warning');
    expect(chip.getAttribute('title')).toContain('(default amount)');
  });

  it('says what to do when the budget is used up', () => {
    mockUseBudget.mockReturnValue({
      data: { ...month, spent_usd: 5, remaining_usd: 0, exhausted: true },
    });
    render(<BudgetChip />);
    const chip = screen.getByRole('status');
    expect(chip).toHaveClass('budget-exhausted');
    expect(chip.getAttribute('title')).toContain('Ask the account owner to raise it');
  });
});
