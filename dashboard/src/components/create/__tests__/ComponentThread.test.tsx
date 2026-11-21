import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor, within } from '../../../test/test-utils';
import type { ComponentThread, ComponentThreads } from '../../../api/endpoints/components';

const getComponentThreads = vi.fn();
const adoptComponent = vi.fn();
vi.mock('../../../api/endpoints/components', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/components')>()),
  getComponentThreads: (...a: unknown[]) => getComponentThreads(...a),
  adoptComponent: (...a: unknown[]) => adoptComponent(...a),
}));

import { ComponentThreadPanel } from '../ComponentThread';

const P = 'proj-thread';

const DRIVER: ComponentThread = {
  key: 'part:al8862-warm',
  name: 'Driver, warm channel',
  role: 'fn:e_ldrv',
  relation: 'refined',
  level: 'concept',
  imagine: { status: 'chosen', part_id: 'al8862-warm', mpn: 'AL8862', library: null, revision: 5, handed_over: false },
  develop: {
    status: 'selected',
    linked_by: 'mpn',
    selections: [
      {
        node_id: 'bom-1',
        mpn: 'AL8862SP-13',
        manufacturer: 'Diodes Incorporated',
        role: 'led_driver',
        selection: 'selected',
        library: { id: 'co_1', name: 'diodes_al8862sp_13', shared: false },
      },
    ],
    boards: [{ board: 'Main board', ref: 'U3' }],
  },
  check: { status: 'violates', violates: ['lux_range'], margins: 1, unchecked: 0 },
  make: { status: 'in_bom', lines: [{ record: 'b', kind: 'bom', part: 'AL8862SP-13', quantity: 2, refs: ['U3', 'U4'] }] },
  adopt: {
    kind: 'part',
    node_id: 'bom-1',
    mpn: 'AL8862SP-13',
    manufacturer: 'Diodes Incorporated',
    library: 'diodes_al8862sp_13',
    needs_rework: false,
  },
};

const LED: ComponentThread = {
  key: 'part:status-led',
  name: 'Status indicator',
  role: 'fn:e_led',
  relation: 'imagine_only',
  level: 'concept',
  imagine: { status: 'chosen', part_id: 'status-led', mpn: '<order code not yet chosen>', revision: 5 },
  develop: { status: 'absent', linked_by: null, selections: [], boards: [] },
  check: { status: 'absent', violates: [], margins: 0, unchecked: 0 },
  make: { status: 'absent', lines: [] },
  adopt: null,
};

function threads(...list: ComponentThread[]): ComponentThreads {
  return {
    project_id: P,
    studio: { status: 'draft', revision: 5 },
    threads: list,
    summary: { same: 0, refined: 1, replaced: 0, develop_only: 0, imagine_only: 1 },
    warnings: [],
  };
}

beforeEach(() => {
  getComponentThreads.mockReset().mockResolvedValue(threads(DRIVER, LED));
  adoptComponent.mockReset().mockResolvedValue({});
});

describe('Component thread', () => {
  it('shows one component across all four stages and marks the stage on screen', async () => {
    render(<ComponentThreadPanel projectId={P} stage="develop" open />);
    const row = await screen.findByTestId('thread-part:al8862-warm');
    const cells = within(row).getAllByRole('cell');
    expect(cells.map((c) => c.textContent)).toEqual([
      'AL8862',
      'AL8862SP-13 · your library diodes_al8862sp_13 · Main board U3',
      'Misses lux_range',
      'In the BOM (U3, U4)',
    ]);
    expect(cells[1]).toHaveClass('is-current');
    expect(screen.getByRole('columnheader', { name: 'Develop' })).toHaveAttribute('aria-current', 'step');
    expect(within(row).getByTestId('thread-relation')).toHaveTextContent('Refined');
    expect(screen.getByTestId('component-thread')).toHaveTextContent('2 components · 1 ahead of Imagine');
  });

  it('says what a stage does not have yet', async () => {
    render(<ComponentThreadPanel projectId={P} stage="imagine" open />);
    const row = await screen.findByTestId('thread-part:status-led');
    expect(within(row).getAllByRole('cell').map((c) => c.textContent)).toEqual([
      'no order code yet',
      'Nothing chosen yet',
      'Nothing to check yet',
      'Not in a BOM or order',
    ]);
    expect(within(row).queryByRole('button')).toBeNull();
  });

  it('takes Develop’s part into the studio at the revision it read', async () => {
    render(<ComponentThreadPanel projectId={P} stage="imagine" open />);
    await userEvent.click(await screen.findByRole('button', { name: 'Use in Imagine' }));
    await waitFor(() => expect(adoptComponent).toHaveBeenCalledWith(P, 5, 'part:al8862-warm'));
  });

  it('asks for Imagine to be reopened when it is handed over', async () => {
    getComponentThreads.mockResolvedValue(
      threads({ ...DRIVER, adopt: { ...DRIVER.adopt!, needs_rework: true } }),
    );
    render(<ComponentThreadPanel projectId={P} stage="develop" open />);
    expect(await screen.findByText('Reopen Imagine to take this in')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Use in Imagine' })).toBeNull();
  });

  it('marks a board part as the board’s own and flags a part missing from its BOM', async () => {
    const reg: ComponentThread = {
      ...DRIVER,
      key: 'selection:reg',
      name: 'TI TPS62160DGKR',
      relation: 'develop_only',
      level: 'board',
      imagine: { status: 'absent' },
      check: { status: 'unchecked', violates: [], margins: 0, unchecked: 2 },
      make: { status: 'not_in_bom', lines: [] },
      adopt: null,
    };
    getComponentThreads.mockResolvedValue(threads(reg));
    render(<ComponentThreadPanel projectId={P} stage="make" open />);
    const row = await screen.findByTestId('thread-selection:reg');
    expect(within(row).getByTestId('thread-relation')).toHaveTextContent('Board part');
    expect(within(row).getAllByRole('cell')[3]).toHaveTextContent("Missing from its board's BOM");
    expect(within(row).getAllByRole('cell')[3]).toHaveClass('is-bad');
    expect(within(row).queryByRole('button')).toBeNull();
  });

  it('renders nothing for a project with no components', async () => {
    getComponentThreads.mockResolvedValue(threads());
    render(<ComponentThreadPanel projectId={P} stage="make" />);
    await waitFor(() => expect(getComponentThreads).toHaveBeenCalled());
    expect(screen.queryByTestId('component-thread')).toBeNull();
  });
});
