import { describe, it, expect, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../../test/test-utils';
import { AutoGrowTextarea } from '../AutoGrowTextarea';
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

describe('JourneyRail', () => {
  it('is the navigation of Create: four stages, where it stands, and the one on screen', async () => {
    const onSelect = vi.fn();
    render(
      <JourneyRail milestone={2} selected={0} onSelect={onSelect} summaries={{ Imagine: 'Concept captured', Check: '2 to resolve' }} />,
    );
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.querySelector('.cv-journey-label')?.textContent)).toEqual(['Imagine', 'Develop', 'Check', 'Make']);
    expect(screen.getByRole('tab', { name: /Imagine/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: /Imagine/ })).toHaveClass('is-done');
    expect(screen.getByRole('tab', { name: /Check/ })).toHaveAttribute('data-state', 'current');
    expect(screen.getByText('2 to resolve')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: /Make/ }));
    expect(onSelect).toHaveBeenCalledWith(3);
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
