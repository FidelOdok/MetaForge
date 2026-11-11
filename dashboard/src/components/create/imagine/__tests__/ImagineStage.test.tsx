import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor, within } from '../../../../test/test-utils';
import type { HandoverChecklist, ImagineStudio, ValidationReport } from '../../../../types/imagine';

const getStudio = vi.fn();
const getHandover = vi.fn();
const getValidation = vi.fn();
const postHandover = vi.fn();
const postRework = vi.fn();
vi.mock('../../../../api/endpoints/imagine', async (orig) => ({
  ...(await orig<typeof import('../../../../api/endpoints/imagine')>()),
  getStudio: (...a: unknown[]) => getStudio(...a),
  getHandover: (...a: unknown[]) => getHandover(...a),
  getValidation: (...a: unknown[]) => getValidation(...a),
  postHandover: (...a: unknown[]) => postHandover(...a),
  postRework: (...a: unknown[]) => postRework(...a),
}));
vi.mock('../SketchWorkspace', () => ({ SketchWorkspace: () => <div data-testid="sketch-workspace" /> }));

import { ImagineStage } from '../ImagineStage';

const P = 'proj-imagine';

function studio(over: Partial<ImagineStudio> = {}): ImagineStudio {
  return {
    project_id: P,
    status: 'draft',
    revision: 2,
    config_ref: 'STUDIO-1@2',
    engine_url: '/v1/twin/projects/proj-imagine/imagine/engine',
    config: {},
    updated_at: '2026-11-07T10:00:00Z',
    handover: null,
    ...over,
  };
}

const CHECKLIST: HandoverChecklist = {
  ready: false,
  items: [
    { id: 'ledger', label: 'Intent ledger complete', status: 'done', detail: '' },
    { id: 'assumptions', label: 'Assumptions confirmed', status: 'assumed', detail: '2 accepted as assumptions' },
    { id: 'parts', label: 'One chosen part per function', status: 'missing', detail: 'Battery has no source' },
  ],
};

const REPORT: ValidationReport = {
  profile_id: 'imagine-v1',
  profile_version: '1.0.0',
  revision_number: 2,
  level: 'V1',
  gates: [],
  findings: [
    {
      id: 'f_assumptions',
      check_id: 'assumptions.settled',
      rule_version: 'imagine-v1@1.0.0',
      gate: 'handover',
      severity: 'blocking',
      status: 'fail',
      target: { kind: 'studio', id: 'assumptions', revision: 2 },
      message: '3 of 3 assumptions still to confirm or accept.',
      evidence: [],
      resolver: 'person',
      fix: null,
      accepted_by: [],
    },
  ],
  debt: [],
  verdict: 'needs_person',
  limitations: [],
  project_id: P,
  computed_at: '2026-11-09T12:00:00Z',
  stale: false,
};

function imagine(view: 'studio' | 'sketch' = 'studio') {
  const onView = vi.fn();
  const onAskAssistant = vi.fn();
  render(<ImagineStage projectId={P} nodes={[]} view={view} onView={onView} onAskAssistant={onAskAssistant} />);
  return { onView, onAskAssistant };
}

describe('studio frame follows the revision', () => {
  it('loads a new revision the agent saves behind the page on screen', async () => {
    getStudio.mockReset().mockResolvedValue(studio({ revision: 2 }));
    imagine();
    const frame = await screen.findByTestId('studio-frame');
    expect(frame.getAttribute('src')).toContain('rev=2');
    getStudio.mockResolvedValue(studio({ revision: 3 }));
    // No config to compare, so the new revision loads as a new page behind the current one.
    await waitFor(() => expect(screen.getByTestId('studio-frame-pending').getAttribute('src')).toContain('rev=3'), { timeout: 15000 });
    expect(screen.getByTestId('studio-frame').getAttribute('src')).toContain('rev=2');
  }, 20000);
});

beforeEach(() => {
  getStudio.mockReset();
  getHandover.mockReset().mockResolvedValue(CHECKLIST);
  getValidation.mockReset().mockResolvedValue(REPORT);
  postHandover.mockReset();
  postRework.mockReset();
});

describe('Imagine', () => {
  it('without a studio, shows one calm empty state that asks the assistant to start it', async () => {
    getStudio.mockResolvedValue(studio({ status: 'none', revision: null, engine_url: null }));
    const { onAskAssistant } = imagine();
    const empty = await screen.findByTestId('studio-empty');
    expect(empty).toHaveTextContent('Ask the assistant to start the studio for this product');
    expect(screen.queryByTestId('studio-frame')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Hand over to engineering' })).toBeNull();
    await userEvent.click(within(empty).getByRole('button', { name: 'Ask the assistant' }));
    expect(onAskAssistant).toHaveBeenCalled();
  });

  it('hosts the studio from the engine URL, sandboxed', async () => {
    getStudio.mockResolvedValue(studio());
    imagine();
    const frame = await screen.findByTestId('studio-frame');
    expect(frame).toHaveAttribute('src', '/api/v1/twin/projects/proj-imagine/imagine/engine?theme=light&rev=2');
    expect(frame).toHaveAttribute('sandbox', 'allow-scripts');
  });

  it('gives the studio the full stage width; the sketch keeps the column', async () => {
    getStudio.mockResolvedValue(studio());
    const first = render(<ImagineStage projectId={P} nodes={[]} view="studio" onView={vi.fn()} />);
    await screen.findByTestId('studio-frame');
    expect(screen.getByTestId('imagine-stage')).toHaveClass('cv-stage-col', 'is-wide');
    first.unmount();
    render(<ImagineStage projectId={P} nodes={[]} view="sketch" onView={vi.fn()} />);
    expect(screen.getByTestId('imagine-stage')).not.toHaveClass('is-wide');
  });

  it('keeps the column for the empty state', async () => {
    getStudio.mockResolvedValue(studio({ status: 'none', revision: null, engine_url: null }));
    imagine();
    await screen.findByTestId('studio-empty');
    expect(screen.getByTestId('imagine-stage')).not.toHaveClass('is-wide');
  });

  it('switches between Studio and Sketch', async () => {
    getStudio.mockResolvedValue(studio());
    const { onView } = imagine();
    await userEvent.click(screen.getByRole('tab', { name: 'Sketch' }));
    expect(onView).toHaveBeenCalledWith('sketch');
  });

  it('shows the sketch tools in the Sketch tab, with no studio frame', async () => {
    getStudio.mockResolvedValue(studio());
    imagine('sketch');
    expect(screen.getByTestId('sketch-workspace')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Sketch' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByTestId('studio-frame')).toBeNull();
  });

  it('lists the handover checklist with one primary action', async () => {
    getStudio.mockResolvedValue(studio());
    imagine();
    const list = await screen.findByRole('list', { name: 'Handover checklist' });
    expect(within(list).getAllByRole('listitem')).toHaveLength(3);
    expect(within(list).getByText(/Battery has no source/)).toBeInTheDocument();
    expect(screen.getAllByTestId('stage-primary')).toHaveLength(1);
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Hand over to engineering');
  });

  it('shows what is left and who clears it above the checklist, and the handover stays the person\'s', async () => {
    getStudio.mockResolvedValue(studio());
    imagine();
    expect(await screen.findByTestId('validation-verdict')).toHaveTextContent('Waiting on you');
    const person = screen.getByRole('list', { name: 'Waiting on you' });
    expect(within(person).getByText(/3 of 3 assumptions still to confirm or accept/)).toBeInTheDocument();
    // The old checklist is still there, one click away, and the single primary action is unchanged.
    expect(screen.getByRole('list', { name: 'Handover checklist' })).toBeInTheDocument();
    expect(screen.getAllByTestId('stage-primary')).toHaveLength(1);
    expect(getValidation).toHaveBeenCalledWith(P, expect.anything());
  });

  it('still lets the person try the handover when the report says blocked, since the gateway decides', async () => {
    getStudio.mockResolvedValue(studio());
    getValidation.mockResolvedValue({ ...REPORT, verdict: 'blocked_external' });
    imagine();
    expect(await screen.findByTestId('validation-verdict')).toHaveTextContent('Blocked outside the project');
    expect(screen.getByTestId('stage-primary')).toBeEnabled();
  });

  it('shows the missing items when the gateway refuses the handover (409)', async () => {
    getStudio.mockResolvedValue(studio());
    postHandover.mockRejectedValue({
      response: {
        status: 409,
        data: { detail: { ready: false, items: [CHECKLIST.items[0], CHECKLIST.items[2]] } },
      },
    });
    imagine();
    await userEvent.click(await screen.findByRole('button', { name: 'Hand over to engineering' }));
    const missing = await screen.findByTestId('handover-missing');
    expect(postHandover).toHaveBeenCalledWith(P, expect.stringMatching(/^handover-/));
    expect(within(missing).getByText('One chosen part per function')).toBeInTheDocument();
    // Only what is missing: a done item is not repeated.
    expect(within(missing).queryByText('Intent ledger complete')).toBeNull();
  });

  it('after handover, is read only and can be reopened with a reason after confirming', async () => {
    getStudio.mockResolvedValue(studio({ status: 'handed_over', revision: 3, handover: { revision: 3 } }));
    postRework.mockResolvedValue(studio({ revision: 4 }));
    imagine();
    expect(await screen.findByTestId('imagine-readonly')).toHaveTextContent('Handed over to engineering · revision 3 · read only');
    expect(screen.queryByRole('button', { name: 'Hand over to engineering' })).toBeNull();
    expect(screen.queryByTestId('stage-primary')).toBeNull();

    await userEvent.click(screen.getByRole('button', { name: 'Reopen Imagine' }));
    expect(postRework).not.toHaveBeenCalled();
    const confirm = screen.getByRole('button', { name: 'Confirm and reopen' });
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Why reopen Imagine/), 'The battery must be swappable');
    await userEvent.click(confirm);
    await waitFor(() => expect(postRework).toHaveBeenCalledWith(P, expect.stringMatching(/^rework-/), 'The battery must be swappable'));
  });

  it('after handover, offers to start developing (FORGE-824)', async () => {
    getStudio.mockResolvedValue(studio({ status: 'handed_over', revision: 3, handover: { revision: 3 } }));
    const onStartDeveloping = vi.fn();
    render(<ImagineStage projectId={P} nodes={[]} view="studio" onView={vi.fn()} onStartDeveloping={onStartDeveloping} />);
    const start = await screen.findByRole('button', { name: 'Start developing' });
    expect(screen.getByRole('button', { name: 'Reopen Imagine' })).toBeInTheDocument();
    await userEvent.click(start);
    expect(onStartDeveloping).toHaveBeenCalledTimes(1);
  });
});
