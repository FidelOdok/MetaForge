import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '../../test/test-utils';

vi.mock('../../api/endpoints/harness', () => ({
  getHarnessProviders: vi.fn(),
  getHarnessModels: vi.fn(async () => ['gpt-5.5']),
  getHarnessTools: vi.fn(async () => []),
}));
vi.mock('../../api/endpoints/harness-credentials', () => ({
  saveProviderKey: vi.fn(async () => undefined),
  removeProviderKey: vi.fn(async () => undefined),
  selectProviderModel: vi.fn(async () => undefined),
}));

import { SettingsPage } from '../SettingsPage';
import { getHarnessProviders } from '../../api/endpoints/harness';
import { saveProviderKey, selectProviderModel } from '../../api/endpoints/harness-credentials';
import { GATEWAY_STORAGE_KEY, setGatewayBase } from '../../lib/gatewayConfig';

const mockProviders = vi.mocked(getHarnessProviders);

describe('SettingsPage', () => {
  beforeEach(() => {
    setGatewayBase(null);
    localStorage.clear();
    vi.clearAllMocks();
    mockProviders.mockResolvedValue({
      activeProvider: 'openai',
      activeModel: 'gpt-5.5',
      providers: [
        { id: 'openai', family: 'openai', configured: true, baseUrl: null },
        { id: 'anthropic', family: 'anthropic', configured: false, baseUrl: null },
      ],
    });
  });

  it('shows the same-origin proxy as the default gateway and omits the account card', async () => {
    render(<SettingsPage />);
    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();
    expect(screen.getByTestId('gateway-in-use')).toHaveTextContent('(same origin, via proxy)');
    expect(screen.getByTestId('gateway-source')).toHaveTextContent('same-origin proxy');
    expect(screen.getByText('Before you expose a gateway')).toBeInTheDocument();
    expect(screen.queryByText('Sign in')).not.toBeInTheDocument();
    expect(screen.queryByText('Sign out')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(await screen.findByText('gpt-5.5', { exact: false })).toBeInTheDocument();
  });

  it('saves a gateway override to the browser and can reset it', () => {
    render(<SettingsPage />);
    fireEvent.change(screen.getByPlaceholderText('https://gateway.tailnet.ts.net'), {
      target: { value: 'http://localhost' },
    });
    fireEvent.change(screen.getByPlaceholderText('8000'), { target: { value: '9000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(localStorage.getItem(GATEWAY_STORAGE_KEY)).toBe('http://localhost:9000');
    expect(screen.getByTestId('gateway-in-use')).toHaveTextContent('http://localhost:9000');
    expect(screen.getByTestId('gateway-source')).toHaveTextContent('saved in this browser');

    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(localStorage.getItem(GATEWAY_STORAGE_KEY)).toBeNull();
    expect(screen.getByTestId('gateway-source')).toHaveTextContent('same-origin proxy');
  });

  it('flags an invalid port', () => {
    render(<SettingsPage />);
    fireEvent.change(screen.getByPlaceholderText('https://gateway.tailnet.ts.net'), {
      target: { value: 'http://localhost' },
    });
    fireEvent.change(screen.getByPlaceholderText('8000'), { target: { value: '70000' } });
    expect(screen.getByText('"70000" is not a valid port (1-65535).')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('sends a provider key and selection to the gateway', async () => {
    render(<SettingsPage />);
    const select = await screen.findByRole('combobox');
    fireEvent.change(select, { target: { value: 'openai' } });

    fireEvent.change(screen.getByPlaceholderText('Paste your provider API key'), {
      target: { value: 'sk-test' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save key to gateway' }));
    await waitFor(() => expect(saveProviderKey).toHaveBeenCalledWith('openai', 'sk-test', ''));
    expect(await screen.findByText('Key saved to your gateway.')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('Model ID, or leave blank for provider default'), {
      target: { value: 'gpt-5.5' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Use this provider and model' }));
    await waitFor(() => expect(selectProviderModel).toHaveBeenCalledWith('openai', 'gpt-5.5', ''));
  });

  it('prompts for the gateway when providers cannot load', async () => {
    mockProviders.mockRejectedValue(new Error('down'));
    render(<SettingsPage />);
    expect(
      await screen.findByText(/Connect your gateway above to load its registered providers/),
    ).toBeInTheDocument();
  });
});
