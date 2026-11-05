import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '../../../test/test-utils';
import * as harness from '../../../api/endpoints/harness';
import { useModelChoice } from '../../../store/model-choice';
import { ModelPicker } from '../ModelPicker';

const PROVIDERS = {
  providers: [
    { id: 'openrouter', configured: true },
    { id: 'anthropic', configured: false },
  ],
  activeProvider: 'openrouter',
  activeModel: 'openai/gpt-4o',
} as never;

describe('ModelPicker', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useModelChoice.getState().reset();
    vi.spyOn(harness, 'getHarnessProviders').mockResolvedValue(PROVIDERS);
    vi.spyOn(harness, 'getHarnessModels').mockResolvedValue(['openai/gpt-4o', 'anthropic/claude-sonnet-5.5']);
  });

  it('starts on the workspace default and keeps a chosen model', async () => {
    render(<ModelPicker />);
    expect(await screen.findByText(/Using the default set for this workspace/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Use the default' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'anthropic/claude-sonnet-5.5' } });
    expect(useModelChoice.getState().model).toBe('anthropic/claude-sonnet-5.5');
    expect(screen.getByText(/Used for your messages in both chats/)).toBeInTheDocument();
  });

  it('goes back to the default', async () => {
    useModelChoice.getState().choose({ provider: 'openrouter', model: 'x/y' });
    render(<ModelPicker />);
    fireEvent.click(await screen.findByRole('button', { name: 'Use the default' }));
    expect(useModelChoice.getState()).toMatchObject({ provider: '', model: '' });
  });

  it('drops a saved provider that is no longer connected', async () => {
    useModelChoice.getState().choose({ provider: 'anthropic', model: 'claude' });
    render(<ModelPicker />);
    await waitFor(() => expect(useModelChoice.getState()).toMatchObject({ provider: '', model: '' }));
  });
});
