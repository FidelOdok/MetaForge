import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { act, render, screen, waitFor } from '../../../test/test-utils';
import { opEvent, opSnapshot, snapshotResponse } from '../../../test/operation-fixtures';
import { makeApproval } from '../../../test/approval-fixtures';
import { useOperationsStore } from '../../../store/operations-store';
import type { OperationEvent, OperationSnapshot } from '../../../types/operations';
import { elapsedText, formatElapsed } from '../labels';

const cancelOperation = vi.fn();
const retryOperation = vi.fn();
vi.mock('../../../api/endpoints/operations', () => ({
  getOperations: vi.fn(),
  operationsStreamUrl: vi.fn(),
  cancelOperation: (...args: unknown[]) => cancelOperation(...args),
  retryOperation: (...args: unknown[]) => retryOperation(...args),
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
  retryOperation.mockReset();
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
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
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

  it('retries through the gateway and shows a refusal beside the actions', async () => {
    const user = userEvent.setup();
    retryOperation.mockRejectedValueOnce({ response: { status: 409, data: { detail: 'only a failed design-flow run can be retried from here' } } });
    seed([
      opEvent({ sequence: 1 }),
      opEvent({
        sequence: 2,
        type: 'failed',
        state: 'failed',
        severity: 'error',
        action_refs: [{ kind: 'retry', id: 'run:a', label: 'Retry' }],
      }),
    ]);
    render(<ActivityOverlay projectId="p1" />);
    await user.click(screen.getByRole('button', { name: 'Expand activity' }));
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(retryOperation).toHaveBeenCalledWith('p1', 'run:a'));
    expect(await screen.findByText(/only a failed design-flow run/)).toBeInTheDocument();
  });

  it('hands cancel, retry and approval to the host when it handles them', async () => {
    const user = userEvent.setup();
    const onAction = vi.fn().mockResolvedValue(undefined);
    const onApproval = vi.fn();
    seed([
      opEvent({
        sequence: 1,
        type: 'review_required',
        state: 'waiting_for_user',
        requires_action: true,
        action_refs: [
          { kind: 'approval', id: 'gate:run_a', label: 'Review and decide' },
          { kind: 'cancel', id: 'run:a', label: 'Cancel' },
        ],
      }),
    ]);
    render(<ActivityOverlay projectId="p1" onAction={onAction} onApproval={onApproval} />);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(onAction).toHaveBeenCalledTimes(1));
    expect(onAction.mock.calls[0]![1]).toMatchObject({ kind: 'cancel' });
    expect(cancelOperation).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Review and decide' }));
    expect(onApproval).toHaveBeenCalledTimes(1);
    expect(getApproval).not.toHaveBeenCalled();
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

  // ASVG-015: a depicted element opens what it shows, by mouse or keyboard.
  it('opens the related artifact when the drawing is activated', async () => {
    const user = userEvent.setup();
    window.history.pushState({}, '', '/create/p1');
    seed([
      opEvent({ sequence: 1, operation_id: 'job:sketch-s1', kind: 'job', run_id: null, domain: 'sketch', title: 'Saving sketch', type: 'accepted' }),
      opEvent({
        sequence: 2,
        operation_id: 'job:sketch-s1',
        kind: 'job',
        run_id: null,
        domain: 'sketch',
        title: 'Sketch saved',
        type: 'completed',
        state: 'completed',
        artifact_refs: [{ kind: 'work_product', id: 'node-7', label: 'Sketch SKT-SKETCH@2', url: null, authority: 'illustrative' }],
      }),
    ]);
    render(<ActivityOverlay projectId="p1" />);
    expect(screen.getByRole('heading', { name: 'Sketch saved' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Expand activity' }));
    const inspect = screen.getByRole('button', { name: 'Inspect Sketch SKT-SKETCH@2' });
    inspect.focus();
    await user.keyboard('{Enter}');
    expect(window.location.pathname).toBe('/twin');
    expect(window.location.search).toBe('?node=node-7');
  });

  it('surfaces a sketch that lost its surface as a review with a link to choose where it goes', async () => {
    seed([
      opEvent({
        sequence: 1,
        operation_id: 'job:sketch-plane-remap-PLN-RIB-CAD-BASE-r3',
        kind: 'job',
        run_id: null,
        domain: 'sketch',
        title: 'The surface this sketch was on changed; choose where it goes',
        type: 'review_required',
        state: 'waiting_for_user',
        requires_action: true,
        action_refs: [{ kind: 'open', id: '/twin?node=node-sk&tab=model', label: 'Choose where the sketch goes' }],
      }),
    ]);
    render(<ActivityOverlay projectId="p1" />);
    expect(screen.getByRole('heading', { name: 'The surface this sketch was on changed; choose where it goes' })).toBeInTheDocument();
    const link = await screen.findByRole('link', { name: 'Choose where the sketch goes' });
    expect(link.getAttribute('href')).toBe('/twin?node=node-sk&tab=model');
  });

  it('shows the stage detail when a stage is activated and nothing was produced yet', async () => {
    const user = userEvent.setup();
    seed([
      opEvent({ sequence: 1, operation_id: 'job:j1', kind: 'job', run_id: null, stage_id: 'cad.profile', stage_title: 'Drawing the profile', domain: 'mechanical' }),
      opEvent({ sequence: 2, operation_id: 'job:j1', kind: 'job', run_id: null, stage_id: 'eda.place', stage_title: 'Placing parts', domain: 'electronics' }),
    ]);
    render(<ActivityOverlay projectId="p1" />);
    await user.click(screen.getByRole('button', { name: 'Expand activity' }));
    // The drawing has nothing to open, so it opens the current stage's detail.
    await user.click(screen.getByRole('button', { name: 'Show the details of this step' }));
    const detail = screen.getByTestId('activity-stage-detail');
    expect(detail).toHaveTextContent('Placing parts');
    expect(detail).toHaveTextContent('Nothing produced yet.');
    // A lane's stage opens its own detail, and toggles closed again.
    const lane = screen.getByRole('button', { name: 'Drawing the profile' });
    await user.click(lane);
    expect(screen.getByTestId('activity-stage-detail')).toHaveTextContent('Drawing the profile');
    expect(lane).toHaveAttribute('aria-expanded', 'true');
    await user.click(lane);
    expect(screen.queryByTestId('activity-stage-detail')).toBeNull();
  });

  describe('elapsed time (ASVG-016)', () => {
    // The shape the twin viewer gets on a fresh load: an operation that opened
    // at a decision and never ran, from a gateway that only started the clock
    // on `running`.
    function decisionSnapshot(over: Partial<OperationSnapshot> = {}): OperationSnapshot {
      return opSnapshot({
        operation_id: 'sketch:s1',
        kind: 'job',
        domain: 'sketch',
        title: 'Editable sketch',
        state: 'waiting_for_user',
        message: 'Interpretation proposed',
        requires_action: true,
        started_at: null,
        ...over,
      });
    }

    function seedSnapshot(snapshot: OperationSnapshot) {
      act(() => {
        const store = useOperationsStore.getState();
        store.mergeSnapshot('p1', snapshotResponse([snapshot]));
        store.setHealth('p1', 'live');
      });
    }

    it.each([
      ['missing', null],
      ['unreadable', 'not a time'],
      ['empty', ''],
    ])('never renders NaN when started_at is %s', (_label, startedAt) => {
      seedSnapshot(decisionSnapshot({ started_at: startedAt }));
      render(<ActivityOverlay projectId="p1" detail />);
      const overlay = screen.getByTestId('activity-overlay');
      expect(overlay).toHaveTextContent('Editable sketch');
      expect(overlay.textContent).not.toMatch(/NaN/);
      // No start time: no figure rather than a made-up one.
      expect(screen.queryByTestId('activity-elapsed')).toBeNull();
    });

    it('keeps the start time the client already has when a snapshot lacks it', () => {
      seed([
        opEvent({ operation_id: 'sketch:s1', sequence: 1, state: 'waiting_for_user', occurred_at: '2026-11-04T10:00:00Z' }),
      ]);
      seedSnapshot(decisionSnapshot({ last_sequence: 2, updated_at: '2026-11-04T10:00:29Z' }));
      expect(useOperationsStore.getState().projects.p1?.ops.byId['sketch:s1']?.started_at).toBe('2026-11-04T10:00:00Z');
    });

    it('measures a finished operation from its start to its last update', () => {
      seedSnapshot(
        decisionSnapshot({
          state: 'completed',
          requires_action: false,
          started_at: '2026-11-04T10:00:00Z',
          updated_at: '2026-11-04T10:00:29Z',
        }),
      );
      render(<ActivityOverlay projectId="p1" detail />);
      expect(screen.getByTestId('activity-elapsed')).toHaveTextContent('29s');
    });

    it('reads an unreadable end or duration as nothing, never NaN', () => {
      expect(formatElapsed(Number.NaN)).toBe('0s');
      const now = Date.parse('2026-11-04T10:01:05Z');
      expect(elapsedText({ state: 'running', started_at: null, updated_at: 'x' }, now)).toBeNull();
      expect(elapsedText({ state: 'running', started_at: '2026-11-04T10:00:00Z', updated_at: 'x' }, now)).toBe('1m 05s');
      expect(elapsedText({ state: 'completed', started_at: '2026-11-04T10:00:00Z', updated_at: 'x' }, now)).toBe('1m 05s');
    });

    it('starts the client clock when the operation leaves the queue', () => {
      seed([opEvent({ sequence: 1, type: 'queued', state: 'queued', occurred_at: '2026-11-04T09:59:00Z' })]);
      expect(useOperationsStore.getState().projects.p1?.ops.byId['run:a']?.started_at).toBeNull();
      seed([opEvent({ sequence: 2, occurred_at: '2026-11-04T10:00:00Z' })]);
      expect(useOperationsStore.getState().projects.p1?.ops.byId['run:a']?.started_at).toBe('2026-11-04T10:00:00Z');
    });
  });

  describe('keyboard reach (ASVG-012)', () => {
    it('puts every control in the tab order, header first', async () => {
      const user = userEvent.setup();
      seed([opEvent({ sequence: 1, action_refs: [{ kind: 'cancel', id: 'cancel', label: 'Cancel' }] })]);
      render(<ActivityOverlay projectId="p1" />);

      await user.tab();
      expect(screen.getByRole('button', { name: 'Expand activity' })).toHaveFocus();
      await user.tab();
      expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();

      await user.click(screen.getByRole('button', { name: 'Expand activity' }));
      const overlay = screen.getByTestId('activity-overlay');
      for (const control of [
        screen.getByRole('button', { name: 'Minimise activity' }),
        screen.getByRole('button', { name: 'Pause animation' }),
        screen.getByTestId('activity-inspect'),
        screen.getByRole('button', { name: 'Cancel' }),
      ]) {
        expect(overlay).toContainElement(control);
        expect(control).not.toHaveAttribute('tabindex', '-1');
        expect(control).toBeEnabled();
        act(() => control.focus());
        expect(control).toHaveFocus();
      }
    });

    it('minimises on Escape and returns focus to the toggle', async () => {
      const user = userEvent.setup();
      seed([opEvent({ sequence: 1 })]);
      render(<ActivityOverlay projectId="p1" />);
      await user.click(screen.getByRole('button', { name: 'Expand activity' }));
      act(() => screen.getByTestId('activity-inspect').focus());
      await user.keyboard('{Escape}');
      expect(useOperationsStore.getState().projects.p1?.size).toBe('compact');
      expect(screen.getByRole('button', { name: 'Expand activity' })).toHaveFocus();
    });
  });
});
