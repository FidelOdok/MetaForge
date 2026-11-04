import { describe, it, expect, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { act, render, screen, within } from '../../test/test-utils';
import { opEvent } from '../../test/operation-fixtures';
import { useOperationsStore } from '../../store/operations-store';
import { CreatePage } from '../CreatePage';
import { useProject, useProjects } from '../../hooks/use-projects';
import { useCreation } from '../../hooks/use-creation';
import { useTwinNodes, useNodeVersionHistory } from '../../hooks/use-twin';
import { useApprovals } from '../../hooks/use-approvals';
import { useHarnessTools } from '../../hooks/use-harness';
import { useProjectStore } from '../../store/project-store';
import { useCreateStore } from '../../store/create-store';
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
vi.mock('../../hooks/use-harness', () => ({ useHarnessTools: vi.fn() }));
vi.mock('../../hooks/use-twin-chat', () => ({
  useTwinChatThreads: vi.fn(() => ({ data: [] })),
  useTwinChatThread: vi.fn(() => ({ data: undefined })),
}));
// The stage renders WebGL previews; its behaviour is covered on its own.
vi.mock('../../components/create/ProductStage', async (orig) => ({
  ...(await orig<typeof import('../../components/create/ProductStage')>()),
  ProductStage: () => <section aria-label="Product" />,
}));

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
    resetSampleCreation();
    useCreateStore.setState({ sessions: {} });
    mockWorkspace(routeSampleCreate('get', `/create/${P}`, {}, P, 'sample-pcb') as CreationView);
  });

  it('starts with one question: what do you want to make', () => {
    renderAt('/create');
    expect(screen.getByRole('heading', { name: 'What do you want to make?' })).toBeInTheDocument();
  });

  it('shows the product, where it stands, and the journey for a creation', () => {
    renderAt(`/create/${P}`);
    expect(screen.getByRole('heading', { name: 'Drone FC' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Build completed' })).toBeInTheDocument();
    const progress = screen.getByRole('list', { name: 'Progress' });
    expect(within(progress).getByText('Inspect').closest('li')).toHaveAttribute('aria-current', 'step');
    expect(useProjectStore.getState().activeProjectId).toBe(P);
  });

  it('keeps the open panel in the address, so back and forward work', async () => {
    renderAt(`/create/${P}`);
    await userEvent.click(screen.getByRole('tab', { name: 'Inspect' }));
    expect(window.location.search).toContain('panel=inspect');
    expect(screen.getByText('Boots and shows up over USB')).toBeInTheDocument();
  });

  it('opens the Decisions panel when the overlay asks for a review', async () => {
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
    expect(window.location.search).toContain('panel=decisions');
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
});
