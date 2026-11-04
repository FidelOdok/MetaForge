import { describe, it, expect, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../../test/test-utils';
import { AutoGrowTextarea } from '../AutoGrowTextarea';
import { ReadinessCard } from '../ReadinessCard';
import { JourneyRail } from '../JourneyRail';
import { ViewSwitch } from '../../layout/ViewSwitch';
import { useProjectStore } from '../../../store/project-store';

describe('AutoGrowTextarea', () => {
  it('sizes itself to its content with no inner scrollbar', () => {
    const spy = vi.spyOn(HTMLTextAreaElement.prototype, 'scrollHeight', 'get').mockReturnValue(132);
    render(<AutoGrowTextarea aria-label="draft" value={'one\ntwo\nthree'} onChange={() => undefined} />);
    const box = screen.getByLabelText('draft') as HTMLTextAreaElement;
    expect(box.style.height).toBe('132px');
    expect(box.style.overflowY).toBe('hidden');
    spy.mockRestore();
  });
});

describe('ReadinessCard', () => {
  it('names the state, the next action and each blocker with its action', async () => {
    const onAction = vi.fn();
    render(
      <ReadinessCard
        readiness={{
          stage: 'checked_design',
          stage_index: 2,
          blockers: [{ id: 'route', message: 'No way of making it chosen', action: 'Choose home, workshop or a manufacturer' }],
          next_action: 'Choose home, workshop or a manufacturer.',
          has_concept: true,
          has_engineering_model: true,
        }}
        onAction={onAction}
      />,
    );
    expect(screen.getByRole('heading', { name: 'Checked design' })).toBeInTheDocument();
    expect(screen.getByText('No way of making it chosen')).toBeInTheDocument();
    expect(screen.getByText(/not proof it is safe/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Choose home, workshop or a manufacturer' }));
    expect(onAction).toHaveBeenCalledWith('route');
  });
});

describe('JourneyRail', () => {
  it('marks the current step', () => {
    render(<JourneyRail step={3} />);
    expect(screen.getByText('Check').closest('li')).toHaveAttribute('aria-current', 'step');
  });
});

describe('ViewSwitch', () => {
  beforeEach(() => useProjectStore.setState({ activeProjectId: 'proj-1', hasSelected: true }));

  it('points both views at the active project', () => {
    window.history.pushState({}, '', '/create/proj-1');
    render(<ViewSwitch />);
    expect(screen.getByRole('link', { name: 'Create' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'Engineering' })).toHaveAttribute('href', '/projects/proj-1');
  });

  it('returns to the same creation from Engineering', () => {
    window.history.pushState({}, '', '/bom');
    render(<ViewSwitch />);
    expect(screen.getByRole('link', { name: 'Create' })).toHaveAttribute('href', '/create/proj-1');
  });
});
