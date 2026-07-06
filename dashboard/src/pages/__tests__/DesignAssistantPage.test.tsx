import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '../../test/test-utils';

vi.mock('../../hooks/use-assistant', () => ({
  useSubmitRequest: () => ({ mutate: vi.fn(), isPending: false, isError: false, reset: vi.fn() }),
  useRunStatus: () => ({ data: undefined }),
  useProposals: vi.fn(),
  useDecideProposal: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../hooks/use-projects', () => ({
  useProjects: () => ({ data: [] }),
}));

// AssistantChat (Chat tab, default) — keep it network-free.
vi.mock('../../hooks/use-chat', () => ({
  useChatThreads: () => ({ data: { data: [] } }),
  useChatThread: () => ({ data: undefined }),
  useCreateChatThread: () => ({ mutate: vi.fn(), isPending: false }),
  useSendChatMessage: () => ({ mutate: vi.fn() }),
}));
vi.mock('../../hooks/use-chat-stream', () => ({ useChatStream: () => undefined }));

import { DesignAssistantPage } from '../DesignAssistantPage';

/** Switch to the Actions tab so the structured-actions form renders. */
function showActions() {
  fireEvent.click(screen.getByRole('button', { name: 'actions' }));
}

describe('DesignAssistantPage', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', class {
      addEventListener() {}
      removeEventListener() {}
      close() {}
    });
  });

  it('renders the page heading', () => {
    render(<DesignAssistantPage />);
    expect(screen.getByText('Design Assistant')).toBeInTheDocument();
  });

  it('defaults to the Chat view with a New chat action', () => {
    render(<DesignAssistantPage />);
    expect(screen.getByRole('button', { name: /New chat/i })).toBeInTheDocument();
  });

  it('renders project selector in the Actions tab', () => {
    render(<DesignAssistantPage />);
    showActions();
    expect(screen.getByText('Select a project...')).toBeInTheDocument();
  });

  it('renders action selector with options in the Actions tab', () => {
    render(<DesignAssistantPage />);
    showActions();
    expect(screen.getByText('Validate Stress')).toBeInTheDocument();
    expect(screen.getByText('Generate CAD')).toBeInTheDocument();
  });

  it('shows target work product field for actions that need it', () => {
    render(<DesignAssistantPage />);
    showActions();
    expect(screen.getByText('Target work product')).toBeInTheDocument();
  });

  it('hides target work product when action does not need it', () => {
    render(<DesignAssistantPage />);
    showActions();
    const actionSelect = document.getElementById('action-select') as HTMLSelectElement;
    fireEvent.change(actionSelect, { target: { value: 'generate_cad' } });
    expect(screen.queryByText('Target work product')).not.toBeInTheDocument();
  });

  it('renders submit button in the Actions tab', () => {
    render(<DesignAssistantPage />);
    showActions();
    expect(screen.getByRole('button', { name: 'Submit request' })).toBeInTheDocument();
  });

  it('shows description prompt label when action does not need target', () => {
    render(<DesignAssistantPage />);
    showActions();
    const actionSelect = document.getElementById('action-select') as HTMLSelectElement;
    fireEvent.change(actionSelect, { target: { value: 'generate_cad' } });
    expect(screen.getByText('Description / prompt')).toBeInTheDocument();
  });
});
