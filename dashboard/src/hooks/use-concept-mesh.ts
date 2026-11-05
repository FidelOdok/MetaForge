import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getConceptMesh, startConceptMesh } from '../api/endpoints/conceptMesh';
import { refusalOf } from '../api/endpoints/create';
import { spendConfirmationOf } from '../api/endpoints/sketchGeneration';
import { useProjectOperations } from '../store/operations-store';
import { twinKeys } from './use-twin';

export const conceptMeshKeys = {
  project: (projectId: string) => ['concept-mesh', projectId] as const,
};

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'superseded', 'stale']);

/**
 * *Make a 3D concept* (3D-RC-001): starts the gateway's concept mesh job and
 * follows its operation on the project stream. Past the hourly cap the
 * gateway asks first; `spendPrompt` carries its words and `start(true)`
 * confirms. When the job ends the twin nodes are fetched again, so the new
 * 3D concept takes the stage.
 */
export function useConceptMesh(projectId: string | undefined) {
  const qc = useQueryClient();
  const [pendingOp, setPendingOp] = useState<string | null>(null);
  const [spendPrompt, setSpendPrompt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ops = useProjectOperations(projectId).ops;
  const op = pendingOp ? ops.byId[pendingOp] : undefined;
  const following = !!pendingOp && (!op || !TERMINAL.has(op.state));

  const state = useQuery({
    queryKey: conceptMeshKeys.project(projectId ?? ''),
    queryFn: ({ signal }) => getConceptMesh(projectId!, signal),
    enabled: !!projectId,
    // While a job runs, look again until its result arrives.
    refetchInterval: (q) => (following || q.state.data?.active_job ? 4_000 : false),
  });

  const mutation = useMutation({
    mutationFn: (confirm: boolean) => startConceptMesh(projectId!, { confirm_spend: confirm }),
    onSuccess: (started) => {
      setPendingOp(started.operation_id);
      setSpendPrompt(null);
      setError(null);
      void qc.invalidateQueries({ queryKey: conceptMeshKeys.project(projectId ?? '') });
    },
    onError: (err) => {
      const spend = spendConfirmationOf(err);
      if (spend) {
        setSpendPrompt(spend);
        setError(null);
      } else {
        setError(refusalOf(err));
      }
    },
  });

  const opState = op?.state;
  const opMessage = op?.message;
  useEffect(() => {
    if (!pendingOp || !opState || !TERMINAL.has(opState)) return;
    void qc.invalidateQueries({ queryKey: twinKeys.all });
    void qc.invalidateQueries({ queryKey: conceptMeshKeys.project(projectId ?? '') });
    if (opState === 'failed') setError(opMessage || 'The 3D concept could not be made. The stage is unchanged.');
    setPendingOp(null);
  }, [pendingOp, opState, opMessage, projectId, qc]);

  return {
    state: state.data,
    running: following || !!state.data?.active_job,
    starting: mutation.isPending,
    spendPrompt,
    error,
    start: (confirm = false) => {
      if (projectId) mutation.mutate(confirm);
    },
    dismissSpend: () => setSpendPrompt(null),
  };
}
