import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { act, render, screen, waitFor } from '../../../test/test-utils';
import { opEvent } from '../../../test/operation-fixtures';
import { makeApproval } from '../../../test/approval-fixtures';
import { useOperationsStore } from '../../../store/operations-store';
import type { OperationEvent } from '../../../types/operations';

const cancelOperation = vi.fn();
vi.mock('../../../api/endpoints/operations', () => ({
  getOperations: vi.fn(),
  operationsStreamUrl: vi.fn(),
  cancelOperation: (...args: unknown[]) => cancelOperation(...args),
}));

const getApproval = vi.fn();
vi.mock('../../../api/endpoints/approvals', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/endpoints/approvals')>();
  return { ...actual, getApproval: (...args: unknown[]) => getApproval(...args) };
});

import { ActivityOverlay } from '../ActivityOverlay';

function seed(events: OperationEvent[], health: 'live' | 'reconnecting' | 'offline' = 'live') {
  act(() => {
    const store = useOperationsStore.getState();
    store.applyEvents('p1', events);
    store.setHealth('p1', health);
  });
}

function setReducedMotion(reduce: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: reduce && query.includes('reduce'),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  });
}

beforeEach(() => {
  useOperationsStore.getState().reset();
  cancelOperation.mockReset();
  getApproval.mockReset();
  setReducedMotion(false);
});

afterEach(() => {
  // Leave jsdom as it was: no matchMedia.
  Reflect.deleteProperty(window, 'matchMedia');
});

describe('ActivityOverlay', () => {
  it('renders nothing when the project has no operations', () => {
    seed([]);
    const { container } = render(<ActivityOverlay projectId="p1" />);
    expect(container.querySelector('[data-testid="activity-overlay"]')).toBeNull();
  });

  it('expands and minimises without losing state or cancelling', async () => {
    const user = userEvent.setup();
    seed([
      opEvent({ sequence: 1, stage_id: 'shape', stage_title: 'Drawing the profile' }),
      opEvent({ sequence: 2, type: 'milestone', message: 'Profile drawn' }),
    ]);
    render(<ActivityOverlay projectId="p1" />);

    expect(screen.getByTestId('activity-status')).toHaveTextContent('Working');
    expect(screen.queryByText('Illustrative activity')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Expand activity' }));
    expect(screen.getByText('Illustrative activity')).toBeInTheDocument();
    expect(screen.getByText('Profile drawn')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Minimise activity' }));
    expect(screen.queryByText('Illustrative activity')).toBeNull();
    expect(screen.getByTestId('activity-status')).toHaveTextContent('Working');
    expect(useOperationsStore.getState().projects.p1?.ops.byId['run:a']?.state).toBe('running');
    expect(useOperationsStore.getState().projects.p1?.size).toBe('compact');
    expect(cancelOperation).not.toHaveBeenCalled();
  });

  it('offers the review action from action_refs, reachable by keyboard, using the existing approval card', async () => {
    const user = userEvent.setup();
    getApproval.mockResolvedValue(makeApproval({ id: 'gate:run_a', title: 'Mounting option' }));
    seed([
      opEvent({
        sequence: 1,
        type: 'review_required',
        state: 'waiting_for_user',
        requires_action: true,
        action_refs: [{ kind: 'approval', id: 'gate:run_a', label: 'Choose a mounting option' }],
      }),
    ]);
    render(<ActivityOverlay projectId="p1" />);

    expect(screen.getByTestId('activity-status')).toHaveTextContent('Waiting for a decision');
    const review = screen.getByRole('button', { name: 'Choose a mounting option' });
    // The overlay adds no cancel or retry unless the operation lists them.
    expect(screen.queryByRole('button', { name: /cancel/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull();

    // Tab until the review button has focus.
    for (let i = 0; i < 5 && document.activeElement !== review; i += 1) await user.tab();
    expect(review).toHaveFocus();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(getApproval).toHaveBeenCalledWith('gate:run_a'));
    expect(await screen.findByText('Mounting option')).toBeInTheDocument();
  });

  it('shows a failure as Needs attention, with no percentage', async () => {
    const user = userEvent.setup();
    seed([
      opEvent({ sequence: 1, progress: { done: 3, total: 8, unit: 'checks' } }),
      opEvent({
        sequence: 2,
        type: 'failed',
        state: 'failed',
        severity: 'error',
        message: 'The wall is too thin',
        action_refs: [{ kind: 'retry', id: 'retry', label: 'Retry' }],
      }),
    ]);
    render(<ActivityOverlay projectId="p1" />);
    await user.click(screen.getByRole('button', { name: 'Expand activity' }));
    expect(screen.getByTestId('activity-status')).toHaveTextContent('Needs attention');
    expect(screen.queryByText(/%/)).toBeNull();
    expect(screen.getByRole('link', { name: 'Retry' })).toBeInTheDocument();
    const scene = document.querySelector('svg.act-scene');
    expect(scene?.getAttribute('data-state')).toBe('failed');
    expect(scene?.getAttribute('data-animated')).toBe('false');
  });

  it('shows a measured percentage only when the backend gives a total', () => {
    seed([opEvent({ sequence: 1, progress: { done: 3, total: 8, unit: 'checks' } })]);
    render(<ActivityOverlay projectId="p1" />);
    expect(screen.getByText('3 of 8 checks complete (38%)')).toBeInTheDocument();
    expect(screen.getByTestId('activity-status')).toHaveTextContent('Checking');
  });

  it('calls cancel only when listed, and never on minimise', async () => {
    const user = userEvent.setup();
    cancelOperation.mockResolvedValue({ operation_id: 'run:a', state: 'cancelled' });
    seed([opEvent({ sequence: 1, action_refs: [{ kind: 'cancel', id: 'cancel', label: 'Cancel' }] })]);
    render(<ActivityOverlay projectId="p1" />);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(cancelOperation).toHaveBeenCalledWith('p1', 'run:a'));
  });

  it('gives a static scene under reduced motion', async () => {
    const user = userEvent.setup();
    setReducedMotion(true);
    seed([opEvent({ sequence: 1 })]);
    render(<ActivityOverlay projectId="p1" />);
    await user.click(screen.getByRole('button', { name: 'Expand activity' }));
    const scene = document.querySelector('svg.act-scene');
    expect(scene?.getAttribute('data-state')).toBe('running');
    expect(scene?.getAttribute('data-animated')).toBe('false');
    expect(screen.queryByRole('button', { name: /pause animation/i })).toBeNull();
    expect(screen.getByTestId('activity-status')).toHaveTextContent('Working');
  });

  it('animates while running and lets the person pause it', async () => {
    const user = userEvent.setup();
    seed([opEvent({ sequence: 1 })]);
    render(<ActivityOverlay projectId="p1" />);
    await user.click(screen.getByRole('button', { name: 'Expand activity' }));
    expect(document.querySelector('svg.act-scene')?.getAttribute('data-animated')).toBe('true');
    await user.click(screen.getByRole('button', { name: 'Pause animation' }));
    expect(document.querySelector('svg.act-scene')?.getAttribute('data-animated')).toBe('false');
    expect(screen.getByRole('button', { name: 'Play animation' })).toBeInTheDocument();
  });

  it('says the connection is interrupted and stops implying progress', async () => {
    const user = userEvent.setup();
    seed([opEvent({ sequence: 1 })], 'reconnecting');
    render(<ActivityOverlay projectId="p1" />);
    expect(screen.getByText('Connection interrupted, reconnecting')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Expand activity' }));
    expect(document.querySelector('svg.act-scene')?.getAttribute('data-animated')).toBe('false');
  });

  it('replaces the illustrative label when a real artifact arrives', async () => {
    const user = userEvent.setup();
    seed([opEvent({ sequence: 1 })]);
    render(<ActivityOverlay projectId="p1" />);
    await user.click(screen.getByRole('button', { name: 'Expand activity' }));
    expect(screen.getByText('Illustrative activity')).toBeInTheDocument();

    seed([
      opEvent({
        sequence: 2,
        type: 'preview_ready',
        artifact_refs: [
          { kind: 'image', id: 'img-1', label: 'Enclosure concept', url: 'https://example.test/c.png', authority: 'preview' },
        ],
      }),
    ]);
    expect(screen.queryByText('Illustrative activity')).toBeNull();
    expect(screen.getByText('Preview: Enclosure concept')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Enclosure concept' })).toBeInTheDocument();
  });

  it('keeps an illustrative artifact labelled as illustrative', async () => {
    const user = userEvent.setup();
    seed([
      opEvent({
        sequence: 1,
        artifact_refs: [{ kind: 'image', id: 'i', label: 'Mood', url: 'https://example.test/m.png', authority: 'illustrative' }],
      }),
    ]);
    render(<ActivityOverlay projectId="p1" />);
    await user.click(screen.getByRole('button', { name: 'Expand activity' }));
    expect(screen.getByText('Illustrative activity')).toBeInTheDocument();
  });

  it('shows one primary operation and a queue counter for the rest', () => {
    seed([
      opEvent({ operation_id: 'run:a', sequence: 1, title: 'Shaping your enclosure' }),
      opEvent({ operation_id: 'job:b', sequence: 1, kind: 'job', domain: 'electronics', title: 'Checking the board' }),
      opEvent({ operation_id: 'job:c', sequence: 1, kind: 'job', domain: 'firmware', title: 'Building firmware' }),
    ]);
    render(<ActivityOverlay projectId="p1" />);
    expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(1);
    expect(screen.getByLabelText('2 more operations in progress')).toHaveTextContent('+2');
  });

  it('has a polite live region and never shows an ETA', () => {
    seed([opEvent({ sequence: 1 })]);
    const { container } = render(<ActivityOverlay projectId="p1" />);
    const live = container.querySelector('[aria-live="polite"]');
    expect(live?.textContent).toContain('Shaping your enclosure: Working');
    expect(container.textContent).not.toMatch(/remaining|ETA|left/i);
  });

  it('shows lanes, stage ids, revisions and raw events in Engineering detail', async () => {
    const user = userEvent.setup();
    seed([
      opEvent({ sequence: 1, stage_id: 'cad.profile', stage_title: 'Profile', domain: 'mechanical', target_revision: '9' }),
      opEvent({ sequence: 2, stage_id: 'eda.place', stage_title: 'Placement', domain: 'electronics', message: 'Placing U1' }),
    ]);
    render(<ActivityOverlay projectId="p1" detail />);
    await user.click(screen.getByRole('button', { name: 'Expand activity' }));
    expect(screen.getByText('CAD')).toBeInTheDocument();
    expect(screen.getByText('EDA')).toBeInTheDocument();
    expect(screen.getAllByText('cad.profile').length).toBeGreaterThan(0);
    await user.click(screen.getByText('Technical detail'));
    expect(screen.getByText(/none to 9/)).toBeInTheDocument();
  });

  it('lets a finished operation be closed, and it stays closed', async () => {
    const user = userEvent.setup();
    seed([opEvent({ sequence: 1, type: 'completed', state: 'completed', message: 'Shape ready' })]);
    const { container } = render(<ActivityOverlay projectId="p1" />);
    expect(screen.getByTestId('activity-status')).toHaveTextContent('Ready');
    await user.click(screen.getByRole('button', { name: 'Close this activity' }));
    expect(container.querySelector('[data-testid="activity-overlay"]')).toBeNull();
    expect(useOperationsStore.getState().projects.p1?.dismissed).toEqual(['run:a']);
  });
});
