import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { probeGateway } from '../api/endpoints/health';
import {
  GatewayUrlError,
  describeMixedContent,
  getGatewayBase,
  isGatewayUserConfigured,
  joinAddressPort,
  setGatewayBase,
  splitGatewayBase,
  subscribeGatewayBase,
} from '../lib/gatewayConfig';

/**
 * Everything needed to edit, probe and save the gateway address.
 *
 * Extracted from `SettingsPage` so the first-run onboarding screen can offer
 * the same form without a second copy of the validation, the mixed-content
 * warning and the cache-invalidation rule. Two implementations of "what counts
 * as a valid gateway address" would drift, and the one on the onboarding path
 * is the one a new user meets first.
 *
 * Deliberately does not raise toasts or navigate. Both callers want different
 * wording and different follow-on behaviour, so they own that; this owns the
 * mechanics.
 */

export type GatewayTestState =
  | { kind: 'idle' }
  | { kind: 'testing' }
  | { kind: 'ok'; latencyMs: number; detail: string }
  | { kind: 'fail'; message: string };

export interface GatewayForm {
  address: string;
  setAddress: (v: string) => void;
  port: string;
  setPort: (v: string) => void;

  /** The base currently in effect. `""` means same-origin relative paths. */
  inUse: string;
  /** True when the value came from the user rather than the build default. */
  overridden: boolean;

  /** The normalised base the fields currently describe, or null if invalid. */
  candidate: string | null;
  /** Why the fields are invalid, when they are. */
  error: string | null;
  /** True when the candidate differs from what is in effect. */
  dirty: boolean;
  /** Mixed-content explanation for the candidate, when it applies. */
  mixedContentWarning: string | null;

  test: GatewayTestState;
  runTest: () => Promise<void>;
  /** Persist the candidate. Returns the saved base, or null if it could not. */
  save: () => string | null;
  /** Drop the override and fall back to the build default. */
  reset: () => void;
}

export function useGatewayForm(): GatewayForm {
  const queryClient = useQueryClient();

  const [inUse, setInUse] = useState(getGatewayBase);
  const [address, setAddress] = useState(() => splitGatewayBase(getGatewayBase()).address);
  const [port, setPort] = useState(() => splitGatewayBase(getGatewayBase()).port);
  const [test, setTest] = useState<GatewayTestState>({ kind: 'idle' });
  const [overridden, setOverridden] = useState(isGatewayUserConfigured);

  // Also fires for a change made in another tab, so two open dashboards do not
  // disagree about where the gateway is.
  useEffect(
    () =>
      subscribeGatewayBase(() => {
        const base = getGatewayBase();
        setInUse(base);
        setOverridden(isGatewayUserConfigured());
        const parts = splitGatewayBase(base);
        setAddress(parts.address);
        setPort(parts.port);
      }),
    [],
  );

  const parsed = useMemo<{ base: string } | { error: string }>(() => {
    try {
      return { base: joinAddressPort(address, port) };
    } catch (err) {
      return { error: err instanceof GatewayUrlError ? err.message : String(err) };
    }
  }, [address, port]);

  const candidate = 'base' in parsed ? parsed.base : null;
  const error = 'error' in parsed ? parsed.error : null;

  const runTest = useCallback(async () => {
    if (candidate === null) return;
    setTest({ kind: 'testing' });
    try {
      const { latencyMs, status } = await probeGateway(candidate);
      const detail = [status?.status, status?.version].filter(Boolean).join(' · ') || 'healthy';
      setTest({ kind: 'ok', latencyMs, detail });
    } catch (err) {
      setTest({ kind: 'fail', message: err instanceof Error ? err.message : String(err) });
    }
  }, [candidate]);

  const save = useCallback((): string | null => {
    if (candidate === null) return null;
    setGatewayBase(candidate);
    const base = getGatewayBase();
    setInUse(base);
    setOverridden(isGatewayUserConfigured());
    // Everything already fetched came from the *old* gateway, so it is not
    // merely stale — it belongs to a different machine.
    queryClient.clear();
    return base;
  }, [candidate, queryClient]);

  const reset = useCallback(() => {
    setGatewayBase(null);
    const base = getGatewayBase();
    setInUse(base);
    setOverridden(false);
    const parts = splitGatewayBase(base);
    setAddress(parts.address);
    setPort(parts.port);
    setTest({ kind: 'idle' });
    queryClient.clear();
  }, [queryClient]);

  return {
    address,
    setAddress,
    port,
    setPort,
    inUse,
    overridden,
    candidate,
    error,
    dirty: candidate !== null && candidate !== inUse,
    mixedContentWarning: candidate ? describeMixedContent(candidate) : null,
    test,
    runTest,
    save,
    reset,
  };
}
