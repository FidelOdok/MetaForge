import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '../../../../test/test-utils';
import { useComponentSelection } from '../../../../store/component-selection-store';

const postImagineEvent = vi.fn();
vi.mock('../../../../api/endpoints/imagine', async (orig) => ({
  ...(await orig<typeof import('../../../../api/endpoints/imagine')>()),
  postImagineEvent: (...a: unknown[]) => postImagineEvent(...a),
}));

import { StudioFrame } from '../StudioFrame';

const P = 'proj-studio';
const SNAPSHOT = { choices: { c1: { option: 'b' } } };

function frame(readOnly = false) {
  const view = render(<StudioFrame projectId={P} src="about:blank" collections={SNAPSHOT} readOnly={readOnly} />);
  const el = screen.getByTestId('studio-frame') as HTMLIFrameElement;
  const win = el.contentWindow!;
  const posted = vi.spyOn(win, 'postMessage').mockImplementation(() => undefined);
  return { view, el, win, posted };
}

function fromPage(data: unknown, source: unknown, origin = 'null') {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data, origin, source: source as Window }));
  });
}

beforeEach(() => {
  postImagineEvent.mockReset().mockResolvedValue({ accepted: true, mapped_to: [] });
  useComponentSelection.setState({ selected: {} });
});

describe('StudioFrame', () => {
  it('hosts the page in a scripts-only sandbox, with no same-origin access', () => {
    const { el } = frame();
    expect(el).toHaveAttribute('sandbox', 'allow-scripts');
    expect(el.getAttribute('sandbox')).not.toContain('allow-same-origin');
    expect(el).toHaveAttribute('title', 'Imagine studio');
  });

  it('answers fg:ready with the snapshot', () => {
    const { win, posted } = frame();
    fromPage({ fg: 1, type: 'fg:ready' }, win);
    expect(posted).toHaveBeenCalledWith({ fg: 1, type: 'fg:snapshot', collections: SNAPSHOT }, '*');
    expect(posted).toHaveBeenCalledTimes(1);
  });

  it('forwards a write to the gateway as an event with a fresh id', async () => {
    const { win } = frame();
    fromPage({ fg: 1, type: 'fg:write', collection: 'requirements', id: 'r1', data: { text: 'Holds 400 g' } }, win);
    fromPage({ fg: 1, type: 'fg:write', collection: 'requirements', id: 'r1', data: { text: 'Holds 400 g' } }, win);
    await waitFor(() => expect(postImagineEvent).toHaveBeenCalledTimes(2));
    const [project, event] = postImagineEvent.mock.calls[0]!;
    expect(project).toBe(P);
    expect(event).toMatchObject({ kind: 'requirement', data: { collection: 'requirements', id: 'r1', data: { text: 'Holds 400 g' } } });
    expect(event.event_id).toMatch(/^evt-/);
    expect(postImagineEvent.mock.calls[1]![1].event_id).not.toBe(event.event_id);
  });

  it('says so when a write could not be saved', async () => {
    postImagineEvent.mockRejectedValue(new Error('down'));
    const { win } = frame();
    fromPage({ fg: 1, type: 'fg:write', collection: 'ledger', id: 'l1', data: {} }, win);
    expect(await screen.findByRole('alert')).toHaveTextContent('could not be saved');
  });

  it('resizes on fg:height, within bounds', () => {
    const { el, win } = frame();
    fromPage({ fg: 1, type: 'fg:height', px: 1234 }, win);
    expect(el.style.height).toBe('1234px');
    fromPage({ fg: 1, type: 'fg:height', px: 3 }, win);
    expect(el.style.height).toBe('240px');
  });

  it('highlights a selected component through the shared selection', () => {
    const { win } = frame();
    fromPage({ fg: 1, type: 'fg:select', component_id: 'CMP-arm' }, win);
    expect(useComponentSelection.getState().selected[P]).toEqual({ componentId: 'CMP-arm', from: 'create' });
  });

  it('turns fg:download into a download, never a navigation', () => {
    const create = vi.fn(() => 'blob:x');
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const { win } = frame();
    fromPage({ fg: 1, type: 'fg:download', name: '../brief.md', mime: 'text/markdown', base64: 'aGk=' }, win);
    expect(create).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    const anchor = click.mock.instances[0] as unknown as HTMLAnchorElement;
    expect(anchor.download).toBe('brief.md');
    click.mockRestore();
  });

  it('ignores a message from any other window, origin or shape', async () => {
    const { win, posted } = frame();
    fromPage({ fg: 1, type: 'fg:ready' }, window);
    fromPage({ fg: 1, type: 'fg:ready' }, null);
    fromPage({ fg: 1, type: 'fg:ready' }, win, 'https://evil.example');
    fromPage({ fg: 1, type: 'fg:write', collection: 'secrets', id: 'x', data: {} }, win);
    fromPage({ type: 'fg:write', collection: 'choices', id: 'x', data: {} }, win);
    fromPage('fg:ready', win);
    fromPage({ fg: 1, type: 'fg:eval', code: 'alert(1)' }, win);
    await new Promise((r) => setTimeout(r, 0));
    expect(posted).not.toHaveBeenCalled();
    expect(postImagineEvent).not.toHaveBeenCalled();
  });

  it('is read only after handover: the page is told, and writes are not forwarded', async () => {
    const { win, posted, view } = frame(false);
    fromPage({ fg: 1, type: 'fg:ready' }, win);
    posted.mockClear();
    view.rerender(<StudioFrame projectId={P} src="about:blank" collections={SNAPSHOT} readOnly />);
    expect(posted).toHaveBeenCalledWith({ fg: 1, type: 'fg:readonly', value: true }, '*');
    fromPage({ fg: 1, type: 'fg:write', collection: 'choices', id: 'c2', data: {} }, win);
    await new Promise((r) => setTimeout(r, 0));
    expect(postImagineEvent).not.toHaveBeenCalled();
  });

  it('tells a page that loads after handover that it is read only', () => {
    const { win, posted } = frame(true);
    fromPage({ fg: 1, type: 'fg:ready' }, win);
    expect(posted).toHaveBeenNthCalledWith(1, { fg: 1, type: 'fg:snapshot', collections: SNAPSHOT }, '*');
    expect(posted).toHaveBeenNthCalledWith(2, { fg: 1, type: 'fg:readonly', value: true }, '*');
  });
});
