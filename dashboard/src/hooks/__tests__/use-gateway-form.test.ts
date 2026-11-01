import { act, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '../../test/test-utils';
import { useGatewayForm } from '../use-gateway-form';
import { getGatewayBase, resetGatewayCache, setGatewayBase } from '../../lib/gatewayConfig';

/**
 * The gateway form is now shared between Settings and first-run onboarding,
 * so a break here breaks both — including the screen a brand-new user meets
 * before they have anything else to fall back on.
 *
 * `SettingsPage` had no test of its own when this logic lived inside it, which
 * is part of why extracting it was worth doing.
 */

const probeGateway = vi.hoisted(() => vi.fn());
vi.mock('../../api/endpoints/health', () => ({ probeGateway }));

describe('useGatewayForm', () => {
  beforeEach(() => {
    localStorage.clear();
    resetGatewayCache();
    probeGateway.mockReset();
  });

  afterEach(() => {
    localStorage.clear();
    resetGatewayCache();
  });

  it('starts empty when no gateway is configured', () => {
    const { result } = renderHook(() => useGatewayForm());
    expect(result.current.inUse).toBe('');
    expect(result.current.overridden).toBe(false);
    expect(result.current.dirty).toBe(false);
  });

  it('normalises a bare host into a candidate', () => {
    const { result } = renderHook(() => useGatewayForm());
    act(() => result.current.setAddress('localhost'));
    act(() => result.current.setPort('8000'));
    expect(result.current.candidate).toBe('http://localhost:8000');
    expect(result.current.error).toBeNull();
    expect(result.current.dirty).toBe(true);
  });

  it('reports why an address is invalid instead of silently refusing', () => {
    const { result } = renderHook(() => useGatewayForm());
    act(() => result.current.setAddress('http://localhost'));
    act(() => result.current.setPort('99999'));
    expect(result.current.candidate).toBeNull();
    expect(result.current.error).toMatch(/not a valid port/i);
  });

  it('rejects an API path pasted in place of the gateway root', () => {
    // The likeliest paste mistake. Left in, it silently produces /v1/v1/...
    const { result } = renderHook(() => useGatewayForm());
    act(() => result.current.setAddress('http://localhost:8000/api/v1'));
    expect(result.current.candidate).toBeNull();
    expect(result.current.error).toMatch(/gateway root/i);
  });

  it('persists on save and reports itself as overridden', () => {
    const { result } = renderHook(() => useGatewayForm());
    act(() => result.current.setAddress('http://gw.example.com'));
    let saved: string | null = null;
    act(() => {
      saved = result.current.save();
    });
    expect(saved).toBe('http://gw.example.com');
    expect(getGatewayBase()).toBe('http://gw.example.com');
    expect(result.current.overridden).toBe(true);
    expect(result.current.dirty).toBe(false);
  });

  it('refuses to save an invalid candidate', () => {
    const { result } = renderHook(() => useGatewayForm());
    act(() => result.current.setAddress('http://localhost'));
    act(() => result.current.setPort('nonsense'));
    let saved: string | null = 'unset';
    act(() => {
      saved = result.current.save();
    });
    expect(saved).toBeNull();
    expect(getGatewayBase()).toBe('');
  });

  it('reset clears the override', () => {
    setGatewayBase('http://gw.example.com');
    const { result } = renderHook(() => useGatewayForm());
    expect(result.current.overridden).toBe(true);
    act(() => result.current.reset());
    expect(result.current.overridden).toBe(false);
    expect(getGatewayBase()).toBe('');
  });

  it('surfaces a successful probe with its latency', async () => {
    probeGateway.mockResolvedValue({
      latencyMs: 12,
      status: { status: 'healthy', version: '0.1.0' },
    });
    const { result } = renderHook(() => useGatewayForm());
    act(() => result.current.setAddress('http://localhost:8000'));
    await act(async () => {
      await result.current.runTest();
    });
    await waitFor(() => expect(result.current.test.kind).toBe('ok'));
    if (result.current.test.kind !== 'ok') throw new Error('expected ok');
    expect(result.current.test.latencyMs).toBe(12);
    expect(result.current.test.detail).toContain('healthy');
  });

  it('surfaces a failed probe message rather than swallowing it', async () => {
    probeGateway.mockRejectedValue(new Error('Could not reach http://nope/health'));
    const { result } = renderHook(() => useGatewayForm());
    act(() => result.current.setAddress('http://nope'));
    await act(async () => {
      await result.current.runTest();
    });
    await waitFor(() => expect(result.current.test.kind).toBe('fail'));
    if (result.current.test.kind !== 'fail') throw new Error('expected fail');
    expect(result.current.test.message).toMatch(/could not reach/i);
  });

  it('does not probe an invalid candidate', async () => {
    const { result } = renderHook(() => useGatewayForm());
    act(() => result.current.setAddress('http://localhost'));
    act(() => result.current.setPort('99999'));
    await act(async () => {
      await result.current.runTest();
    });
    expect(probeGateway).not.toHaveBeenCalled();
    expect(result.current.test.kind).toBe('idle');
  });
});
