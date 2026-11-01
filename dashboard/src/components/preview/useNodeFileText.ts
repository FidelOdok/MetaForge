import { useEffect, useState } from 'react';
import { fetchNodeFileText } from '../../api/endpoints/twin';

export interface NodeFileText {
  text: string | null;
  loading: boolean;
  error: string | null;
}

// One fetch per node file per page load, shared by every preview surface
// (inspector panel, full-screen modal, project row) open on the same node.
// Plain state rather than react-query so a preview renders under any
// provider tree, including page tests that mock the twin hooks.
const cache = new Map<string, Promise<string>>();

/** Test hook: forget cached file bodies. */
export function clearNodeFileTextCache(): void {
  cache.clear();
}

/**
 * The work product's file as text, fetched only when `enabled`. Callers
 * pass `enabled` from the registry (`TEXT_ENGINES`), so a binary file is
 * never requested as text. `version` (the node's updatedAt) keys the cache
 * so an edited work product is re-read.
 */
export function useNodeFileText(nodeId: string, enabled: boolean, version = ''): NodeFileText {
  const [state, setState] = useState<NodeFileText>({ text: null, loading: enabled, error: null });

  useEffect(() => {
    if (!enabled) {
      setState({ text: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState({ text: null, loading: true, error: null });
    const key = `${nodeId}@${version}`;
    let pending = cache.get(key);
    if (!pending) {
      pending = fetchNodeFileText(nodeId);
      cache.set(key, pending);
      // A failure must not be cached forever: the file may be stored later.
      pending.catch(() => cache.delete(key));
    }
    pending
      .then((text) => {
        if (!cancelled) setState({ text, loading: false, error: null });
      })
      .catch(() => {
        if (!cancelled) setState({ text: null, loading: false, error: 'No file stored for this work product yet.' });
      });
    return () => {
      cancelled = true;
    };
  }, [nodeId, enabled, version]);

  return state;
}
