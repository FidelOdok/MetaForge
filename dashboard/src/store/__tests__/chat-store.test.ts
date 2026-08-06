import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '../chat-store';

const STORAGE_KEY = 'metaforge.chat-model-selection';

const reset = () => {
  useChatStore.setState({
    streamingContent: {},
    agentSteps: {},
    typingThreadIds: new Set<string>(),
    selectedProvider: null,
    selectedModel: null,
    enabledTools: null,
  });
  localStorage.removeItem(STORAGE_KEY);
};

describe('useChatStore persistence', () => {
  beforeEach(reset);

  it('setModel updates the selection and persists it to localStorage', () => {
    useChatStore.getState().setModel('anthropic', 'claude-fable-5');

    expect(useChatStore.getState().selectedProvider).toBe('anthropic');
    expect(useChatStore.getState().selectedModel).toBe('claude-fable-5');

    const persisted = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
    expect(persisted.state.selectedProvider).toBe('anthropic');
    expect(persisted.state.selectedModel).toBe('claude-fable-5');
  });

  it('setEnabledTools persists the tool selection', () => {
    useChatStore.getState().setEnabledTools(['tool-a', 'tool-b']);

    const persisted = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
    expect(persisted.state.enabledTools).toEqual(['tool-a', 'tool-b']);
  });

  it('never persists transient runtime state (streaming/typing/agent steps)', () => {
    useChatStore.getState().setModel('openai', 'gpt-5.5');
    useChatStore.getState().appendStreamChunk('m1', 'partial text');
    useChatStore.getState().setAgentTyping('t1', true);

    const persisted = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
    expect(persisted.state.streamingContent).toBeUndefined();
    expect(persisted.state.agentSteps).toBeUndefined();
    expect(persisted.state.typingThreadIds).toBeUndefined();
    // The durable preference is still there alongside the omitted transient state.
    expect(persisted.state.selectedProvider).toBe('openai');
  });
});
