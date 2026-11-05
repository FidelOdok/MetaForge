import { describe, it, expect, vi, beforeEach } from 'vitest';
import { waitFor } from '@testing-library/react';
import * as chat from '../../api/endpoints/twin-chat';
import * as uploads from '../../api/endpoints/uploads';
import * as create from '../../api/endpoints/create';
import { displayText, intentOf, useCreateStore } from '../create-store';
import { useModelChoice } from '../model-choice';

const TOOLS = { ask: [], change: ['twin.propose_change'], design: ['cadquery.create'] };
const P = 'p1';

function thread(id = 't1') {
  return { id, title: '', scope_kind: 'project', scope_entity_id: P, last_message_at: '', messages: [] };
}

describe('create store', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useCreateStore.setState({ sessions: {} });
  });

  it('does not send an empty draft, and clears the draft once queued', async () => {
    const send = vi.spyOn(chat, 'sendTwinChatMessage').mockResolvedValue({} as never);
    vi.spyOn(chat, 'createTwinChatThread').mockResolvedValue(thread());
    const s = useCreateStore.getState();
    expect(s.submit(P, TOOLS)).toBe(false);
    s.setDraft(P, 'A desk hook');
    expect(s.submit(P, TOOLS)).toBe(true);
    expect(useCreateStore.getState().session(P).draft).toBe('');
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(send.mock.calls[0]![1].content).toContain('A desk hook');
    await waitFor(() => expect(useCreateStore.getState().session(P).queue).toHaveLength(0));
  });

  it('sends the model the person chose, and nothing when they use the default', async () => {
    const send = vi.spyOn(chat, 'sendTwinChatMessage').mockResolvedValue({} as never);
    vi.spyOn(chat, 'createTwinChatThread').mockResolvedValue(thread());
    const s = useCreateStore.getState();
    useModelChoice.getState().choose({ provider: 'openrouter', model: 'anthropic/claude-sonnet-5.5' });
    s.setDraft(P, 'first');
    s.submit(P, TOOLS);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(send.mock.calls[0]![1]).toMatchObject({ provider: 'openrouter', model: 'anthropic/claude-sonnet-5.5' });
    useModelChoice.getState().reset();
    s.setDraft(P, 'second');
    s.submit(P, TOOLS);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(send.mock.calls[1]![1]).not.toHaveProperty('provider');
    expect(send.mock.calls[1]![1]).not.toHaveProperty('model');
  });

  it('sends an answer from a reply block as a question, leaving the draft alone', async () => {
    const send = vi.spyOn(chat, 'sendTwinChatMessage').mockResolvedValue({} as never);
    vi.spyOn(chat, 'createTwinChatThread').mockResolvedValue(thread());
    const s = useCreateStore.getState();
    s.setIntent(P, 'design');
    s.setDraft(P, 'half-typed idea');
    expect(s.reply(P, '  ', TOOLS)).toBe(false);
    expect(s.reply(P, 'Desk thickness: 25 mm (measured by me)', TOOLS)).toBe(true);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const body = send.mock.calls[0]![1];
    expect(body.content).toContain('Desk thickness: 25 mm');
    // An answer never runs design tools, whatever mode the composer is in.
    expect(body.tools).toEqual([]);
    expect(useCreateStore.getState().session(P).draft).toBe('half-typed idea');
  });

  it('queues behind a running pass and creates the thread only once', async () => {
    let release: () => void = () => undefined;
    const send = vi
      .spyOn(chat, 'sendTwinChatMessage')
      .mockImplementationOnce(() => new Promise((r) => (release = () => r({} as never))))
      .mockResolvedValue({} as never);
    const make = vi.spyOn(chat, 'createTwinChatThread').mockResolvedValue(thread());
    const s = useCreateStore.getState();
    s.setDraft(P, 'first');
    s.submit(P, TOOLS);
    s.setIntent(P, 'change');
    s.setDraft(P, 'second');
    s.submit(P, TOOLS);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const queue = useCreateStore.getState().session(P).queue;
    expect(queue.map((i) => i.state)).toEqual(['sending', 'queued']);
    release();
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(make).toHaveBeenCalledTimes(1);
    // The change request carries its own tool and instruction.
    expect(send.mock.calls[1]![1].tools).toEqual(['twin.propose_change']);
    expect(intentOf(send.mock.calls[1]![1].content)).toBe('change');
  });

  it('names the turn operation before sending and keeps the operation id the gateway returns', async () => {
    let release: () => void = () => undefined;
    const send = vi
      .spyOn(chat, 'sendTwinChatMessage')
      .mockImplementation(() => new Promise((r) => (release = () => r({ operation_id: 'chat:from-gateway' } as never))));
    vi.spyOn(chat, 'createTwinChatThread').mockResolvedValue(thread());
    const s = useCreateStore.getState();
    s.setDraft(P, 'A desk hook');
    s.submit(P, TOOLS);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const sending = useCreateStore.getState().session(P).queue[0]!;
    // The acknowledgement can be matched to the stream before the reply returns.
    expect(send.mock.calls[0]![1].client_turn_id).toBe(sending.id);
    expect(sending.operationId).toBe(`chat:${sending.id}`);
    release();
    await waitFor(() => expect(useCreateStore.getState().session(P).queue).toHaveLength(0));
    expect(useCreateStore.getState().session(P).lastOperationId).toBe('chat:from-gateway');
  });

  it('sends a retried instruction as a new turn', async () => {
    const send = vi
      .spyOn(chat, 'sendTwinChatMessage')
      .mockRejectedValueOnce(new Error('gateway down'))
      .mockResolvedValue({} as never);
    vi.spyOn(chat, 'createTwinChatThread').mockResolvedValue(thread());
    const s = useCreateStore.getState();
    s.setDraft(P, 'again');
    s.submit(P, TOOLS);
    await waitFor(() => expect(useCreateStore.getState().session(P).queue[0]?.state).toBe('failed'));
    const failed = useCreateStore.getState().session(P).queue[0]!;
    s.retry(P, failed.id, TOOLS);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    const first = send.mock.calls[0]![1].client_turn_id;
    const second = send.mock.calls[1]![1].client_turn_id;
    expect(first).toBe(failed.id);
    expect(second).toBeTruthy();
    expect(second).not.toBe(first);
  });

  it('keeps a failed instruction to retry or edit', async () => {
    vi.spyOn(chat, 'createTwinChatThread').mockResolvedValue(thread());
    vi.spyOn(chat, 'sendTwinChatMessage').mockRejectedValue(new Error('gateway down'));
    const s = useCreateStore.getState();
    s.setDraft(P, 'keep me');
    s.submit(P, TOOLS);
    await waitFor(() => expect(useCreateStore.getState().session(P).queue[0]?.state).toBe('failed'));
    const failed = useCreateStore.getState().session(P).queue[0]!;
    expect(failed.error).toBe('gateway down');
    s.restore(P, failed.id);
    expect(useCreateStore.getState().session(P).draft).toBe('keep me');
    expect(useCreateStore.getState().session(P).queue).toHaveLength(0);
  });

  it('uploads attachments at once, reports a rejected one and records the rest as references', async () => {
    vi.spyOn(uploads, 'createUpload').mockResolvedValue({
      id: 'u1',
      status: 'approved',
      source: 'dashboard',
      project_id: P,
      description: '',
      requested_by: null,
      agent: null,
      created_at: '',
      expires_at: null,
      files: [
        { name: 'sketch.png', size: 1, sha256: '', format: 'png', wp_type: 'image', domain: '', error: null, work_product_id: 'wp-1' },
        { name: 'x.exe', size: 1, sha256: '', format: '', wp_type: '', domain: '', error: 'Unsupported file type', work_product_id: null },
      ],
    });
    const view = { creation: { version: 3, brief: { items: {}, measurements: [], assumptions: [], references: [] } } };
    vi.spyOn(create, 'getCreation').mockResolvedValue(view as never);
    const put = vi.spyOn(create, 'putBrief').mockResolvedValue(view as never);
    useCreateStore.getState().attach(P, [new File(['a'], 'sketch.png'), new File(['b'], 'x.exe')]);
    expect(useCreateStore.getState().session(P).attachments.map((a) => a.state)).toEqual(['uploading', 'uploading']);
    await waitFor(() =>
      expect(useCreateStore.getState().session(P).attachments.map((a) => a.state)).toEqual(['added', 'rejected']),
    );
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(put.mock.calls[0]![1].references).toEqual(['wp-1']);
    expect(put.mock.calls[0]![2]).toMatchObject({ expected_version: 3 });
  });

  it('strips the instruction it appends when showing a message', () => {
    expect(displayText('Make it blue\nPropose a reviewable change using twin.propose_change; do not apply it.')).toBe(
      'Make it blue',
    );
  });

  it('keeps what each sent message was classified as, and sends the selected part', async () => {
    const classification = {
      change_class: 'appearance',
      confidence: 0.92,
      stage: 'rules',
      also: [],
      target_component: null,
      target_component_id: null,
      target_feature: null,
      handling: 'update_appearance',
      message: 'Colour change only. Your engineering model stays the same.',
      signals: [],
      appearance: null,
      proposed_context_update: null,
      choices: [],
      stated_dimensions: [],
    } as const;
    const send = vi
      .spyOn(chat, 'sendTwinChatMessage')
      .mockResolvedValue({ id: 'm-red', classification } as never);
    vi.spyOn(chat, 'createTwinChatThread').mockResolvedValue(thread());
    const s = useCreateStore.getState();
    s.selectNode(P, 'node-lid');
    s.setDraft(P, 'make it red');
    s.submit(P, TOOLS);
    await waitFor(() => expect(useCreateStore.getState().session(P).classifications?.['m-red']).toBeTruthy());
    expect(send.mock.calls[0]![1].graph_ref_node).toBe('node-lid');
    expect(useCreateStore.getState().session(P).classifications?.['m-red']?.message).toMatch(/^Colour change only/);
  });
});
