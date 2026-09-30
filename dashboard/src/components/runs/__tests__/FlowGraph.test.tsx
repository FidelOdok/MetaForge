import { describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../../test/test-utils';

import { FlowGraph } from '../FlowGraph';
import { PhaseActivity } from '../PhaseActivity';
import type { FlowPhaseState, FlowRunEvent } from '../../../types/run-flow';

// React Flow needs layout measurements jsdom does not provide, so it renders
// no nodes without a sized container. Stubbing ResizeObserver and giving the
// elements a size is what makes the assertions below about real output.
class StubResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver);
Object.defineProperties(HTMLElement.prototype, {
  offsetWidth: { get: () => 800, configurable: true },
  offsetHeight: { get: () => 400, configurable: true },
});

// React Flow keeps a node `visibility: hidden` until it has measured it, and
// jsdom measures nothing. Two consequences, both environment artefacts rather
// than app bugs:
//
//   1. node contents are absent from the accessibility tree, so plain role
//      queries find nothing;
//   2. accessible-*name* computation returns empty for a hidden element, so
//      even `{ hidden: true }` cannot match a button by its name.
//
// So these assertions read the `aria-label` attribute directly. That proves
// the buttons exist and carry the right labels. It cannot prove they are
// reachable by assistive technology in a real browser -- that needs a
// browser, which is Playwright's job (`dashboard-tester`), not jsdom's.
function gateButtonLabels(node: HTMLElement): string[] {
  return Array.from(node.querySelectorAll('button')).map(
    (b) => b.getAttribute('aria-label') ?? '',
  );
}

function gateButton(node: HTMLElement, label: string): HTMLButtonElement {
  const found = Array.from(node.querySelectorAll('button')).find(
    (b) => b.getAttribute('aria-label') === label,
  );
  if (!found) throw new Error(`no button labelled "${label}" in ${node.dataset.testid}`);
  return found as HTMLButtonElement;
}

function phase(over: Partial<FlowPhaseState> = {}): FlowPhaseState {
  return {
    id: 'requirements',
    title: 'Requirements',
    status: 'pending',
    summary: '',
    artifacts: [],
    gate: 'Requirements sign-off',
    disciplines: [],
    ...over,
  };
}

describe('FlowGraph', () => {
  it('draws a node per phase, carrying its status', async () => {
    render(
      <FlowGraph
        phases={[
          phase({ id: 'requirements', status: 'passed' }),
          phase({ id: 'design', title: 'Design', status: 'running' }),
        ]}
        selectedPhaseId={null}
        onSelectPhase={vi.fn()}
        canApprove={false}
      />,
    );
    expect(await screen.findByTestId('phase-node-requirements')).toHaveAttribute(
      'data-status',
      'passed',
    );
    expect(screen.getByTestId('phase-node-design')).toHaveAttribute('data-status', 'running');
  });

  it('draws an unreadable phase as unknown, not as pending', async () => {
    // The distinction that matters. An engine that cannot be queried and a
    // flow that has not started look identical if "no data" renders as "not
    // yet" -- and one of those is an outage nobody is being told about.
    render(
      <FlowGraph
        phases={[phase({ status: 'unknown' })]}
        selectedPhaseId={null}
        onSelectPhase={vi.fn()}
        canApprove={false}
      />,
    );
    const node = await screen.findByTestId('phase-node-requirements');
    expect(node).toHaveAttribute('data-status', 'unknown');
    expect(node).toHaveTextContent('UNKNOWN');
    expect(node).not.toHaveTextContent('PENDING');
  });

  it('offers approval on the gate card only while a gate is open', async () => {
    render(
      <FlowGraph
        phases={[phase({ status: 'awaiting_gate' })]}
        selectedPhaseId={null}
        onSelectPhase={vi.fn()}
        canApprove
        onApprove={vi.fn()}
      />,
    );
    // Scoped to the node: React Flow mounts nodes asynchronously, so waiting
    // for the node and then querying inside it is both more reliable and a
    // tighter assertion -- the button has to be on *that* gate card.
    const node = await screen.findByTestId('phase-node-requirements');
    expect(gateButtonLabels(node)).toEqual([
      'Approve Requirements sign-off',
      'Reject Requirements sign-off',
    ]);
  });

  it('does not offer approval when the parent says it cannot be approved', async () => {
    // The component holds no opinion of its own. A second answer to "can this
    // be approved" means the disagreeing copy shows a button that does
    // nothing.
    render(
      <FlowGraph
        phases={[phase({ status: 'awaiting_gate' })]}
        selectedPhaseId={null}
        onSelectPhase={vi.fn()}
        canApprove={false}
        onApprove={vi.fn()}
      />,
    );
    const node = await screen.findByTestId('phase-node-requirements');
    // The control for the test above: it must fail if the button never
    // renders at all, so assert the gate card is present and the button is
    // not -- rather than asserting absence in an empty document.
    expect(node).toHaveTextContent('Requirements sign-off');
    expect(gateButtonLabels(node)).toEqual([]);
  });

  it('marks the gate controls nodrag/nopan so a press does not pan the canvas', async () => {
    // React Flow's own mechanism for interactive controls inside a node.
    render(
      <FlowGraph
        phases={[phase({ status: 'awaiting_gate' })]}
        selectedPhaseId={null}
        onSelectPhase={vi.fn()}
        canApprove
        onApprove={vi.fn()}
      />,
    );
    const node = await screen.findByTestId('phase-node-requirements');
    const approve = gateButton(node, 'Approve Requirements sign-off');
    expect(approve.className).toContain('nodrag');
    expect(approve.className).toContain('nopan');
    // The wrapper too: a press that lands between the buttons is still a
    // press on the pane.
    expect(approve.parentElement?.className).toContain('nopan');
  });

  it('approving from the graph does not also select the phase', async () => {
    const onApprove = vi.fn();
    const onSelectPhase = vi.fn();
    render(
      <FlowGraph
        phases={[phase({ status: 'awaiting_gate' })]}
        selectedPhaseId={null}
        onSelectPhase={onSelectPhase}
        canApprove
        onApprove={onApprove}
      />,
    );
    const node = await screen.findByTestId('phase-node-requirements');
    await userEvent.click(gateButton(node, 'Approve Requirements sign-off'));
    expect(onApprove).toHaveBeenCalledWith('approve');
    expect(onSelectPhase).not.toHaveBeenCalled();
  });
});

describe('PhaseActivity', () => {
  const events: FlowRunEvent[] = [
    { event: 'phase_started', phase: 'requirements', detail: '', at: '2026-09-30T10:00:00Z' },
    { event: 'phase_finished', phase: 'design', detail: 'completed', at: '2026-09-30T10:05:00Z' },
  ];

  it("shows only the selected phase's events", () => {
    render(
      <PhaseActivity phase={phase({ status: 'running' })} events={events} live />,
    );
    const timeline = screen.getByRole('list', { name: 'Phase timeline' });
    expect(timeline).toHaveTextContent('phase_started');
    expect(timeline).not.toHaveTextContent('phase_finished');
  });

  it('says a phase committed nothing rather than showing a tidy empty list', () => {
    // no-data and pass must not render the same (FORGE-361).
    render(<PhaseActivity phase={phase()} events={[]} live />);
    expect(screen.getByTestId('phase-activity')).toHaveTextContent(
      'Nothing committed to the twin by this phase',
    );
  });

  it('says the status is unknown rather than idle when the engine is unreadable', () => {
    render(<PhaseActivity phase={phase({ status: 'unknown' })} events={[]} live={false} />);
    expect(screen.getByRole('status')).toHaveTextContent('unknown — not');
  });

  it('names what it does not yet show instead of rendering empty headings', () => {
    // An empty "Tool calls" section reads as "this phase made none". Naming
    // the gap is the difference between missing data and absent data.
    render(<PhaseActivity phase={phase()} events={[]} live />);
    expect(screen.getByTestId('coverage-note')).toHaveTextContent('session capture');
  });
});
