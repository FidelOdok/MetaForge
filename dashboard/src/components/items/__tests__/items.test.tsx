import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '../../../test/test-utils';
import { CURRENT_VIEW, DIFF, HISTORY } from './fixtures';

vi.mock('../../../hooks/use-items', () => ({
  useItemHistory: vi.fn(),
  useItemDiff: vi.fn(),
  useBaselines: vi.fn(),
  useBaselineDiff: vi.fn(),
  useRunChanges: vi.fn(),
}));

// three.js is not exercised in jsdom; the overlay has its own props contract.
vi.mock('../RevisionOverlay', () => ({
  RevisionOverlay: ({ oldLabel, newLabel }: { oldLabel: string; newLabel: string }) => (
    <div data-testid="overlay-stub">{`${oldLabel} ghost under ${newLabel}`}</div>
  ),
}));

vi.mock('../../preview/ProjectRowPreview', () => ({
  ProjectRowPreview: ({ nodeId }: { nodeId: string }) => <div data-testid="row-preview-stub">{nodeId}</div>,
}));

import {
  useBaselineDiff,
  useBaselines,
  useItemDiff,
  useItemHistory,
  useRunChanges,
} from '../../../hooks/use-items';
import { CurrentItemsPanel } from '../CurrentItemsPanel';
import { ItemHistoryPanel } from '../ItemHistoryPanel';
import { RevisionCompare } from '../RevisionCompare';
import { RevisionPicker } from '../RevisionPicker';
import { BaselinesPanel } from '../BaselinesPanel';
import { RunChangesPanel } from '../RunChangesPanel';
import { RevisionBadge } from '../RevisionBadge';

type Q<T> = { data: T | undefined; isLoading: boolean; isError: boolean; isFetching?: boolean };
const q = <T,>(data: T | undefined): Q<T> => ({ data, isLoading: false, isError: false, isFetching: false });

beforeEach(() => {
  vi.mocked(useItemHistory).mockReturnValue(q(HISTORY) as never);
  vi.mocked(useItemDiff).mockReturnValue(q(DIFF) as never);
});

describe('CurrentItemsPanel', () => {
  it('lists one row per current item, grouped by type, with ref, gate and run', () => {
    render(<CurrentItemsPanel view={CURRENT_VIEW} projectId="proj-001" />);
    expect(screen.getByText('Parts')).toBeInTheDocument();
    expect(screen.getByText('Requirements')).toBeInTheDocument();
    const bracket = screen.getByTestId('item-row-CAD-BRACKET');
    expect(within(bracket).getByText('CAD-BRACKET@3')).toBeInTheDocument();
    expect(within(bracket).getByText(/gate G6/)).toBeInTheDocument();
    expect(within(bracket).getByText('evidence out of date')).toBeInTheDocument();
    // A draft-only item has no row until Working is on.
    expect(screen.queryByText('New part')).not.toBeInTheDocument();
    expect(screen.queryByText('DRAFT')).not.toBeInTheDocument();
    expect(screen.getByText('2 older revisions hidden')).toBeInTheDocument();
    expect(screen.getByText('Pinmap')).toBeInTheDocument();
  });

  it('shows open drafts labelled DRAFT when Working is on', () => {
    render(<CurrentItemsPanel view={CURRENT_VIEW} projectId="proj-001" />);
    fireEvent.click(screen.getByLabelText('Show working drafts'));
    expect(screen.getAllByText('DRAFT')).toHaveLength(2);
    expect(screen.getByText('CAD-BRACKET@4')).toBeInTheDocument();
    expect(screen.getByText('New part')).toBeInTheDocument();
  });

  it('keeps records listed and groups out-of-date results', () => {
    render(<CurrentItemsPanel view={CURRENT_VIEW} projectId="proj-001" />);
    expect(screen.getByText('Decisions')).toBeInTheDocument();
    const stale = screen.getByTestId('out-of-date');
    expect(within(stale).getByText('FEA v1')).toBeInTheDocument();
    expect(within(stale).getByText('CAD-BRACKET@1')).toBeInTheDocument();
  });

  it('opens an item history from its "3 revisions" link', () => {
    render(<CurrentItemsPanel view={CURRENT_VIEW} projectId="proj-001" />);
    fireEvent.click(screen.getByText('3 revisions'));
    expect(screen.getByTestId('item-history')).toBeInTheDocument();
    expect(vi.mocked(useItemHistory)).toHaveBeenCalledWith('CAD-BRACKET', 'proj-001');
  });
});

describe('ItemHistoryPanel', () => {
  it('renders the timeline and compares the previous with the current revision', () => {
    render(<ItemHistoryPanel itemKey="CAD-BRACKET" projectId="proj-001" />);
    const r3 = screen.getByTestId('revision-3');
    expect(within(r3).getByText('CURRENT')).toBeInTheDocument();
    expect(within(r3).getByText('gate G6')).toBeInTheDocument();
    expect(within(r3).getByText(/Approved at gate 'G6'/)).toBeInTheDocument();
    expect(within(r3).getByText('in 1 baseline')).toBeInTheDocument();
    expect(vi.mocked(useItemDiff)).toHaveBeenLastCalledWith('CAD-BRACKET', 2, 3, 'proj-001');
    expect(screen.getByTestId('revision-compare')).toBeInTheDocument();
  });

  it('compares the two revisions the user ticks', () => {
    render(<ItemHistoryPanel itemKey="CAD-BRACKET" projectId="proj-001" />);
    fireEvent.click(screen.getByLabelText('Compare CAD-BRACKET@1'));
    expect(vi.mocked(useItemDiff)).toHaveBeenLastCalledWith('CAD-BRACKET', 1, 3, 'proj-001');
  });
});

describe('RevisionCompare', () => {
  it('shows the overlay, numeric deltas, parameters, requirement values and pinned dependents', async () => {
    render(<RevisionCompare itemKey="CAD-BRACKET" itemType="cad_model" a={2} b={3} />);
    expect(await screen.findByTestId('overlay-stub')).toHaveTextContent('CAD-BRACKET@2 ghost under CAD-BRACKET@3');
    const geo = screen.getByTestId('geometry-deltas');
    expect(within(geo).getByText('+500')).toBeInTheDocument();
    expect(within(geo).getByText('z 1')).toBeInTheDocument();
    expect(within(screen.getByTestId('parameter-changes')).getByText('thickness_mm')).toBeInTheDocument();
    const req = screen.getByTestId('requirement-changes');
    expect(within(req).getByText('>= 2 kg')).toBeInTheDocument();
    expect(within(screen.getByTestId('pinned-dependents')).getByText('FEA v2')).toBeInTheDocument();
  });

  it('skips the 3D overlay for a non-geometry item', () => {
    render(<RevisionCompare itemKey="CS-PAYLOAD" itemType="constraint_set" a={1} b={2} />);
    expect(screen.queryByTestId('overlay-stub')).not.toBeInTheDocument();
  });
});

describe('RevisionPicker', () => {
  it('selects another revision node', () => {
    const onSelect = vi.fn();
    render(<RevisionPicker itemKey="CAD-BRACKET" nodeId="n-b3" onSelect={onSelect} />);
    const picker = screen.getByTestId('revision-picker') as HTMLSelectElement;
    expect(picker.value).toBe('n-b3');
    expect(within(picker).getByText('@3 current')).toBeInTheDocument();
    fireEvent.change(picker, { target: { value: 'n-b1' } });
    expect(onSelect).toHaveBeenCalledWith('n-b1');
  });

  it('renders nothing for a node that is not an item revision', () => {
    vi.mocked(useItemHistory).mockReturnValue(q(undefined) as never);
    const { container } = render(<RevisionPicker itemKey={undefined} nodeId="x" onSelect={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('BaselinesPanel', () => {
  it('lists baselines and diffs one against the current items', () => {
    vi.mocked(useBaselines).mockReturnValue(
      q([
        {
          id: 'bl-2', name: 'G6 approved', project_id: 'proj-001', created_at: new Date().toISOString(),
          approved_by: ['alice'], reason: '', gate_id: 'G6', run_id: 'run-abcdef123456', source: 'gate',
          item_count: 4, member_count: 0,
        },
      ]) as never,
    );
    vi.mocked(useBaselineDiff).mockReturnValue(
      q({
        a: {} as never,
        b: null,
        b_is_current: true,
        items: [
          { key: 'CAD-BRACKET', item_type: 'cad_model', name: 'Bracket', status: 'changed', from_ref: 'CAD-BRACKET@2', to_ref: 'CAD-BRACKET@3' },
          { key: 'CS-PAYLOAD', item_type: 'constraint_set', name: 'Payload', status: 'unchanged', from_ref: 'CS-PAYLOAD@1', to_ref: 'CS-PAYLOAD@1' },
        ],
        counts: { unchanged: 1, changed: 1, added: 0, removed: 0 },
      }) as never,
    );
    render(<BaselinesPanel projectId="proj-001" />);
    expect(screen.getAllByText('G6 approved').length).toBeGreaterThan(0);
    expect(screen.getByText('4 items')).toBeInTheDocument();
    const diff = screen.getByTestId('baseline-diff');
    expect(within(diff).getByText('CAD-BRACKET@2 to CAD-BRACKET@3')).toBeInTheDocument();
    expect(within(diff).queryByText('CS-PAYLOAD')).not.toBeInTheDocument();
    expect(vi.mocked(useBaselineDiff)).toHaveBeenLastCalledWith('bl-2', 'current');
  });

  it('explains that gate approvals record baselines when there are none', () => {
    vi.mocked(useBaselines).mockReturnValue(q([]) as never);
    vi.mocked(useBaselineDiff).mockReturnValue(q(undefined) as never);
    render(<BaselinesPanel projectId="proj-001" />);
    expect(screen.getByText(/A gate approval records one automatically/)).toBeInTheDocument();
  });
});

describe('RunChangesPanel', () => {
  it('lists the revisions a run produced and what its gate did', () => {
    vi.mocked(useRunChanges).mockReturnValue(
      q({
        run_id: 'run-1',
        revisions: [
          { key: 'CAD-BRACKET', item_type: 'cad_model', name: 'Bracket', revision: 3, ref: 'CAD-BRACKET@3', node_id: 'n-b3', status: 'approved', change_reason: null, created_at: null, is_current: true, baselined_by_gate: 'G6' },
          { key: 'CAD-LEG', item_type: 'cad_model', name: 'Leg', revision: 2, ref: 'CAD-LEG@2', node_id: 'n-l2', status: 'draft', change_reason: null, created_at: null, is_current: false, baselined_by_gate: null },
        ],
        baselines: [],
      }) as never,
    );
    render(<RunChangesPanel runId="run-1" />);
    expect(screen.getByText('This run changed')).toBeInTheDocument();
    expect(screen.getByText('gate G6')).toBeInTheDocument();
    expect(screen.getByText('waiting for gate')).toBeInTheDocument();
    expect(screen.getByText('current')).toBeInTheDocument();
  });
});

describe('RevisionBadge', () => {
  it('shows the revision and opens the item history', () => {
    render(
      <RevisionBadge
        entry={{ key: 'CAD-BRACKET', item_type: 'cad_model', revision: 2, ref: 'CAD-BRACKET@2', status: 'committed', current: false, revision_count: 3, via: 'revision' }}
      />,
    );
    const badge = screen.getByTestId('revision-badge');
    expect(badge).toHaveTextContent('@2 (old)');
    fireEvent.click(badge);
    expect(screen.getByRole('dialog', { name: 'CAD-BRACKET history' })).toBeInTheDocument();
  });
});
