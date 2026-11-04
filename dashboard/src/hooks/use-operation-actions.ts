import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';

import { cancelOperation, retryOperation } from '../api/endpoints/operations';
import type { OperationView } from '../lib/operations-reducer';
import type { ActionRef } from '../types/operations';

/**
 * What the activity overlay's actions do on a page (FORGE-788, RT-028).
 *
 * `cancel` and `retry` call the gateway's operation routes; the overlay shows
 * only the actions an operation lists, so a button exists only when the
 * gateway supports it for that operation. The new operation a retry starts
 * arrives on the project stream like any other. `open` goes to the path the
 * action names, else to the run. `approval` is the host's: Create opens its
 * Decisions panel, Engineering keeps the overlay's inline approval card.
 *
 * The returned handler resolves when the gateway answers and rejects with its
 * refusal (a 409 says why), which the overlay shows next to the actions.
 */
export function useOperationActions(projectId: string) {
  const navigate = useNavigate();
  return useCallback(
    async (op: OperationView, action: ActionRef): Promise<unknown> => {
      if (action.kind === 'cancel') return cancelOperation(projectId, op.operation_id);
      if (action.kind === 'retry') return retryOperation(projectId, op.operation_id);
      if (action.kind === 'open') {
        const href = action.id.startsWith('/') ? action.id : op.run_id ? `/runs/${encodeURIComponent(op.run_id)}` : null;
        if (href) navigate(href);
        return undefined;
      }
      return undefined;
    },
    [navigate, projectId],
  );
}
