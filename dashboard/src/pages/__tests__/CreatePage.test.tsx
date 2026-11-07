import { describe, it, expect, vi, beforeEach } from 'vitest';
import { waitFor } from '@testing-library/react';
import * as chat from '../../api/endpoints/twin-chat';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { act, render, screen, within } from '../../test/test-utils';
import { opEvent } from '../../test/operation-fixtures';
import { useOperationsStore } from '../../store/operations-store';
import { CreatePage, START_DEVELOP_TEXT, START_IMAGINE_TEXT } from '../CreatePage';
import { useProject, useProjects } from '../../hooks/use-projects';
import { useCreation } from '../../hooks/use-creation';
import { useTwinNodes, useNodeVersionHistory } from '../../hooks/use-twin';
import { useApprovals } from '../../hooks/use-approvals';
import { useHarnessTools } from '../../hooks/use-harness';
import { useProjectStore } from '../../store/project-store';
import { useCreateStore } from '../../store/create-store';
import { PANELS_KEY, useCreateLayoutStore } from '../../store/create-layout-store';
import { ApprovalNotifier } from '../../components/approvals/ApprovalNotifier';
import { resetSampleCreation, routeSampleCreate } from '../../lib/sample-create';
import type { CreationView } from '../../api/endpoints/create';

vi.mock('../../hooks/use-projects', () => ({
  useProject: vi.fn(),
  useProjects: vi.fn(),
  useCreateProject: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useUpdateProject: vi.fn(() => ({ mutate: vi.fn() })),
  useDeleteProject: vi.fn(() => ({ mutateAsync: vi.fn() })),
}));
vi.mock('../../hooks/use-creation', () => ({
  useCreation: vi.fn(),
  useCreationWrite: vi.fn(() => ({ mutate: vi.fn(), isPending: false, error: null })),
}));
vi.mock('../../hooks/use-twin', () => ({ useTwinNodes: vi.fn(), useNodeVersionHistory: vi.fn() }));
vi.mock('../../hooks/use-approvals', () => ({
  useApprovals: vi.fn(),
  useDecideApproval: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));
vi.mock('../../hooks/use-harness', () => ({
  useHarnessTools: vi.fn(),
  useHarnessProviders: () => ({ data: { activeProvider: 'openrouter', activeModel: 'openai/gpt-4o', providers: [] } }),
  useHarnessModels: () => ({ data: [] }),
}));
vi.mock('../../hooks/use-twin-chat', () => ({
  useTwinChatThreads: vi.fn(() => ({ data: [] })),
  useTwinChatThread: vi.fn(() => ({ data: undefined, refetch: vi.fn() })),
}));
// Each stage is covered on its own; here they only say which one is on screen.
vi.mock('../../components/create/imagine/ImagineStage', () => ({
  ImagineStage: ({
    view,
    onView,
    onAskAssistant,
    onStartDeveloping,
  }: {
    view: string;
    onView: (v: 'studio' | 'sketch') => void;
    onAskAssistant?: () => void;
    onStartDeveloping?: () => void;
  }) => (
    <section aria-label="Imagine stage" data-view={view}>
      <button type="button" onClick={() => onView('sketch')}>
        Sketch
      </button>
      <button type="button" onClick={onAskAssistant}>
        Ask the assistant
      </button>
      <button type="button" onClick={onStartDeveloping}>
        Start developing
      </button>
    </section>
  ),
}));
vi.mock('../../components/create/DevelopStage', () => ({ DevelopStage: () => <section aria-label="Develop stage" /> }));
vi.mock('../../components/create/CheckStage', () => ({ CheckStage: () => <section aria-label="Check stage" /> }));
vi.mock('../../components/create/MakeStage', () => ({ MakeStage: () => <section aria-label="Make stage" /> }));

const P = 'sample-drone-fc';

function mockWorkspace(view: CreationView) {
  vi.mocked(useProject).mockReturnValue({ data: { id: P, name: 'Drone FC', work_products: [] } } as never);
  vi.mocked(useProjects).mockReturnValue({ data: [], isLoading: false } as never);
  vi.mocked(useCreation).mockReturnValue({ data: view } as never);
  vi.mocked(useTwinNodes).mockReturnValue({ data: [] } as never);
  vi.mocked(useNodeVersionHistory).mockReturnValue({ data: undefined } as never);
  vi.mocked(useApprovals).mockReturnValue({ data: { items: [], unscopedCount: 0 } } as never);
  vi.mocked(useHarnessTools).mockReturnValue({ data: [{ id: 'twin.propose_change' }] } as never);
}

function renderAt(path: string) {
  window.history.pushState({}, '', path);
  return render(
    <Routes>
      <Route path="/create" element={<CreatePage />} />
      <Route path="/create/:projectId" element={<CreatePage />} />
    </Routes>,
  );
}

describe('CreatePage', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useCreateLayoutStore.getState().reset();
    resetSampleCreation();
    useCreateStore.setState({ sessions: {} });
    mockWorkspace(routeSampleCreate('get', `/create/${P}`, {}, P, 'sample-pcb') as CreationView);
  });

  it('starts with one question: what do you want to make', () => {
    renderAt('/create');
    expect(screen.getByRole('heading', { name: 'What do you want to make?' })).toBeInTheDocument();
  });

  it('opens a creation on the stage where it stands, with the four stages as navigation', () => {
    renderAt(`/create/${P}`);
    expect(screen.getByRole('heading', { name: 'Drone FC' })).toBeInTheDocument();
    const rail = screen.getByRole('tablist', { name: 'Stages' });
    const tabs = within(rail).getAllByRole('tab');
    expect(tabs.map((t) => t.querySelector('.cv-journey-label')?.textContent)).toEqual(['Imagine', 'Develop', 'Check', 'Make']);
    // The sample build is completed: Make is where it stands, and on screen.
    expect(within(rail).getByRole('tab', { name: /Make/ })).toHaveAttribute('aria-selected', 'true');
    expect(within(rail).getByRole('tab', { name: /Make/ })).toHaveAttribute('data-state', 'current');
    expect(screen.getByRole('region', { name: 'Make stage' })).toBeInTheDocument();
    expect(useProjectStore.getState().activeProjectId).toBe(P);
  });

  it('switches stage from the rail and keeps it in the address, so back and forward work', async () => {
    renderAt(`/create/${P}`);
    await userEvent.click(screen.getByRole('tab', { name: /Imagine/ }));
    expect(window.location.search).toContain('stage=imagine');
    expect(screen.getByRole('region', { name: 'Imagine stage' })).toHaveAttribute('data-view', 'studio');
    expect(screen.queryByRole('region', { name: 'Make stage' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Sketch' }));
    expect(window.location.search).toContain('imagine=sketch');
    expect(screen.getByRole('region', { name: 'Imagine stage' })).toHaveAttribute('data-view', 'sketch');
    await userEvent.click(screen.getByRole('tab', { name: /Develop/ }));
    expect(screen.getByRole('region', { name: 'Develop stage' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: /Check/ }));
    expect(screen.getByRole('region', { name: 'Check stage' })).toBeInTheDocument();
  });

  it('has removed the side panels, the presentation mode and the duplicate controls', () => {
    renderAt(`/create/${P}`);
    for (const name of ['Brief', 'Inspect', 'History']) expect(screen.queryByRole('tab', { name })).toBeNull();
    expect(screen.queryByRole('complementary', { name: 'Details' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Present/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Engineering/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Collapse the details' })).toBeNull();
    expect(screen.queryByRole('group', { name: 'Stage mode' })).toBeNull();
    expect(screen.queryByTestId('fit-together')).toBeNull();
    expect(screen.queryByTestId('readiness-status')).toBeNull();
    expect(screen.queryByText(/Manufacturing guidance/)).toBeNull();
  });

  it('opens the decisions when the activity asks for a review', async () => {
    useOperationsStore.getState().reset();
    act(() =>
      useOperationsStore.getState().applyEvents(P, [
        opEvent({
          project_id: P,
          sequence: 1,
          type: 'review_required',
          state: 'waiting_for_user',
          requires_action: true,
          action_refs: [{ kind: 'approval', id: 'gate:run_a', label: 'Review and decide' }],
        }),
      ]),
    );
    renderAt(`/create/${P}`);
    await userEvent.click(screen.getByRole('button', { name: 'Review and decide' }));
    expect(screen.getByRole('dialog', { name: 'Decisions' })).toBeInTheDocument();
  });

  it('keeps the activity and the decisions in one compact header spot, never over the stage', () => {
    useOperationsStore.getState().reset();
    act(() => useOperationsStore.getState().applyEvents(P, [opEvent({ project_id: P, sequence: 1 })]));
    renderAt(`/create/${P}`);
    const overlay = screen.getByTestId('activity-overlay');
    const status = screen.getByTestId('create-status');
    expect(status.closest('header')).not.toBeNull();
    expect(status).toContainElement(overlay);
    expect(status).toContainElement(screen.getByRole('button', { name: /Decisions/ }));
    expect(overlay).toHaveClass('act-dock-create', 'is-compact');
    expect(screen.getByRole('tabpanel').contains(overlay)).toBe(false);
  });

  it('acknowledges a sent instruction once its operation is on the stream', () => {
    useOperationsStore.getState().reset();
    useCreateStore.setState({
      sessions: {
        [P]: {
          draft: '',
          intent: 'ask',
          attachments: [],
          threadId: null,
          selectedNodeId: null,
          lastOperationId: null,
          queue: [
            { id: 'ins-1', text: 'Make it wider', intent: 'ask', references: [], state: 'sending', createdAt: '2026-11-04T10:00:00Z', operationId: 'chat:ins-1' },
          ],
        },
      },
    });
    renderAt(`/create/${P}`);
    expect(screen.getByText('Sending…')).toBeInTheDocument();
    act(() =>
      useOperationsStore
        .getState()
        .applyEvents(P, [opEvent({ project_id: P, operation_id: 'chat:ins-1', kind: 'chat', run_id: null, type: 'accepted', state: 'queued' })]),
    );
    expect(screen.getByText('Received · working on it')).toBeInTheDocument();
  });

  it('keeps an unsent draft when the view is left and opened again', async () => {
    const first = renderAt(`/create/${P}`);
    await userEvent.type(screen.getByLabelText('Describe your product or ask for a change'), 'Make the holes M3');
    first.unmount();
    renderAt(`/create/${P}`);
    expect(screen.getByLabelText('Describe your product or ask for a change')).toHaveValue('Make the holes M3');
  });

  it('collapses the conversation to a slim rail, and remembers it', async () => {
    const first = renderAt(`/create/${P}`);
    await userEvent.click(screen.getByRole('button', { name: 'Collapse the conversation' }));
    expect(screen.getByTestId('chat-rail')).toBeInTheDocument();
    expect(screen.queryByLabelText('Describe your product or ask for a change')).toBeNull();
    // The stage stays.
    expect(screen.getByRole('tabpanel')).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem(PANELS_KEY) ?? '{}')).toEqual({ chat: true });
    first.unmount();

    // A new page load reads the preference back.
    useCreateLayoutStore.getState().reset();
    renderAt(`/create/${P}`);
    expect(screen.getByTestId('chat-rail')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Show the conversation' }));
    expect(screen.getByLabelText('Describe your product or ask for a change')).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem(PANELS_KEY) ?? '{}')).toEqual({ chat: false });
  });

  it('sends the Imagine request at once as a design turn with the Imagine tools (FORGE-824)', async () => {
    const send = vi.spyOn(chat, 'sendTwinChatMessage').mockResolvedValue({} as never);
    vi.spyOn(chat, 'createTwinChatThread').mockResolvedValue({ id: 't1' } as never);
    useCreateStore.getState().setDraft(P, 'my own unsent words');
    renderAt(`/create/${P}?stage=imagine`);
    await userEvent.click(screen.getByRole('button', { name: 'Ask the assistant' }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const body = send.mock.calls[0]![1];
    expect(body.content).toContain(START_IMAGINE_TEXT);
    expect(body.content).toContain('Use only the explicitly selected tools');
    expect(body.create_stage).toBe('imagine');
    expect(body.tools).toBeUndefined();
    // The person's draft is left alone.
    expect(useCreateStore.getState().session(P).draft).toBe('my own unsent words');
    send.mockRestore();
  });

  it('starts developing after handover: moves to Develop and sends a Develop design turn', async () => {
    const send = vi.spyOn(chat, 'sendTwinChatMessage').mockResolvedValue({} as never);
    vi.spyOn(chat, 'createTwinChatThread').mockResolvedValue({ id: 't1' } as never);
    renderAt(`/create/${P}?stage=imagine`);
    await userEvent.click(screen.getByRole('button', { name: 'Start developing' }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(window.location.search).toContain('stage=develop');
    const body = send.mock.calls[0]![1];
    expect(body.content).toContain(START_DEVELOP_TEXT);
    expect(body.create_stage).toBe('develop');
    expect(body.tools).toBeUndefined();
    send.mockRestore();
  });

  it('shows waiting approvals as a badge on Decisions, never as a popup over the stage', () => {
    vi.mocked(useApprovals).mockReturnValue({
      data: {
        items: [{ id: 'a1', kind: 'sketch', title: 'Parametric sketch', status: 'pending' }],
        unscopedCount: 0,
      },
    } as never);
    window.history.pushState({}, '', `/create/${P}`);
    render(
      <>
        <ApprovalNotifier />
        <Routes>
          <Route path="/create/:projectId" element={<CreatePage />} />
        </Routes>
      </>,
    );
    const badge = screen.getByTestId('decisions-badge');
    expect(badge).toHaveTextContent('1');
    expect(screen.getByRole('button', { name: 'Decisions, 1 waiting' })).toContainElement(badge);
    expect(screen.getByRole('tabpanel').contains(badge)).toBe(false);
    expect(screen.getAllByTestId('decisions-badge')).toHaveLength(1);
    expect(screen.queryByRole('status', { name: 'Approvals waiting' })).toBeNull();
  });
});
