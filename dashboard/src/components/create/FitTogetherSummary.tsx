import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleCheck, CircleDashed, CircleAlert, RefreshCw } from 'lucide-react';
import {
  checkContracts,
  contractKeys,
  getContracts,
  plainState,
  type InterfaceContractView,
} from '../../api/endpoints/interfaceContracts';

function StateIcon({ c }: { c: InterfaceContractView }) {
  const state = plainState(c);
  if (state === 'checked, it fits') return <CircleCheck size={13} aria-hidden="true" />;
  if (state === 'does not fit yet' || state.startsWith('needs')) return <CircleAlert size={13} aria-hidden="true" />;
  return <CircleDashed size={13} aria-hidden="true" />;
}

/**
 * Check, in plain words: the parts of the product that must fit together
 * (the interface contracts), each with where it stands. Shown only when the
 * project has contracts. "Not checked yet" stays that until a real check
 * ran on real data; nothing here claims a fit it has not checked. The
 * technical detail is in Engineering's Interfaces tab.
 */
export function FitTogetherSummary({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const view = useQuery({
    queryKey: contractKeys.project(projectId),
    queryFn: ({ signal }) => getContracts(projectId, signal),
  });
  const contracts = view.data?.contracts ?? [];
  if (!contracts.length) return null;

  const check = async () => {
    setBusy(true);
    setError(null);
    try {
      await checkContracts(projectId);
      await qc.invalidateQueries({ queryKey: contractKeys.project(projectId) });
    } catch {
      setError('The check could not run. Nothing was changed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="cv-card cv-fit" aria-labelledby="cv-fit-title" data-testid="fit-together">
      <p className="cv-eyebrow">Check</p>
      <h3 id="cv-fit-title">Parts that must fit together</h3>
      <ul className="cv-blockers">
        {contracts.map((c) => (
          <li key={c.key} data-testid={`fit-${c.key}`}>
            <StateIcon c={c} />
            <span>
              {c.plain}: {plainState(c)}
            </span>
          </li>
        ))}
      </ul>
      <button type="button" className="cv-link" onClick={() => void check()} disabled={busy}>
        <RefreshCw size={12} aria-hidden="true" /> {busy ? 'Checking…' : 'Check now'}
      </button>
      {error && (
        <p className="cv-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
