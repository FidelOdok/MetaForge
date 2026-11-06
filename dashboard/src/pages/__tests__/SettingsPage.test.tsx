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
import { getHarnessProviders, getHarnessTools } from '../../api/endpoints/harness';
import { saveProviderKey, selectProviderModel } from '../../api/endpoints/harness-credentials';
import { GATEWAY_STORAGE_KEY, setGatewayBase } from '../../lib/gatewayConfig';

const mockProviders = vi.mocked(getHarnessProviders);
const mockTools = vi.mocked(getHarnessTools);

describe('SettingsPage', () => {
  beforeEach(() => {
    setGatewayBase(null);
    localStorage.clear();
    vi.clearAllMocks();
    mockTools.mockResolvedValue([]);
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

  const gatewayError = (status: number, detail: string) =>
    Object.assign(new Error(String(status)), { isAxiosError: true, response: { status, data: { detail } } });

  async function saveKey(adminToken = '') {
    render(<SettingsPage />);
    fireEvent.change(await screen.findByRole('combobox'), { target: { value: 'openai' } });
    if (adminToken) {
      fireEvent.change(screen.getByLabelText('Admin token'), { target: { value: adminToken } });
    }
    fireEvent.change(screen.getByPlaceholderText('Paste your provider API key'), {
      target: { value: 'sk-test' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save key to gateway' }));
  }

  it('asks for the admin token when the gateway requires one, and keeps the key for a retry', async () => {
    vi.mocked(saveProviderKey).mockRejectedValueOnce(
      gatewayError(401, 'invalid or missing X-MetaForge-Admin token'),
    );
    await saveKey();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This gateway needs its admin token for this change. Enter it in Admin token, then try again.',
    );
    expect(screen.getByPlaceholderText('Paste your provider API key')).toHaveValue('sk-test');
  });

  it('says the admin token was wrong when one was sent', async () => {
    vi.mocked(saveProviderKey).mockRejectedValueOnce(
      gatewayError(401, 'invalid or missing X-MetaForge-Admin token'),
    );
    await saveKey('wrong');
    expect(await screen.findByRole('alert')).toHaveTextContent('The gateway rejected the admin token.');
  });

  it('explains a gateway with no admin token configured', async () => {
    vi.mocked(saveProviderKey).mockRejectedValueOnce(
      gatewayError(401, 'credential writes need METAFORGE_HARNESS_ADMIN_TOKEN set on the gateway'),
    );
    await saveKey();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This gateway only accepts key changes from its own machine.',
    );
  });

  it("shows the gateway's reason for a refused change", async () => {
    vi.mocked(saveProviderKey).mockRejectedValueOnce(gatewayError(400, "unknown provider 'nope'"));
    await saveKey();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "The gateway refused this change: unknown provider 'nope'",
    );
  });

  it('prompts for the gateway when providers cannot load', async () => {
    mockProviders.mockRejectedValue(new Error('down'));
    render(<SettingsPage />);
    expect(
      await screen.findByText(/Connect your gateway above to load its registered providers/),
    ).toBeInTheDocument();
  });

  it('shows a cloud adapter as not configured when the gateway has none of its tools', async () => {
    render(<SettingsPage />);
    const card = await screen.findByTestId('cloud-adapter-meshy');
    expect(card).toHaveTextContent('Meshy AI');
    expect(card).toHaveTextContent('Not configured');
    expect(card).toHaveTextContent('MESHY_API_KEY');
    const zoo = screen.getByTestId('cloud-adapter-zoo');
    expect(zoo).toHaveTextContent('Not configured');
    expect(zoo).toHaveTextContent('ZOO_API_TOKEN');
    const tripo = screen.getByTestId('cloud-adapter-tripo');
    expect(tripo).toHaveTextContent('Not configured');
    expect(tripo).toHaveTextContent('TRIPO_API_KEY');
    const slant = screen.getByTestId('cloud-adapter-slant3d');
    expect(slant).toHaveTextContent('Not configured');
    expect(slant).toHaveTextContent('SLANT3D_API_KEY');
  });

  it('shows a cloud adapter as connected from the gateway tool list', async () => {
    mockTools.mockResolvedValue([
      { id: 'meshy.text_to_3d', name: 'Meshy Text to 3D', server: 'meshy', capability: null },
      { id: 'meshy.get_task', name: 'Meshy Task Status', server: 'meshy', capability: null },
      { id: 'web.search', name: 'Web Search', server: 'web', capability: null },
    ]);
    render(<SettingsPage />);
    const card = await screen.findByTestId('cloud-adapter-meshy');
    await waitFor(() => expect(card).toHaveTextContent('Connected · 2 tools'));
    expect(card).not.toHaveTextContent('MESHY_API_KEY');
  });
});
