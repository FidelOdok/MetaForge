import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleAlert, CircleCheck, CircleDashed } from 'lucide-react';
import {
  checkContracts,
  contractKeys,
  getContracts,
  plainState,
  type InterfaceContractView,
} from '../../api/endpoints/interfaceContracts';
import { dfmKeys, evaluateDfm, getDfm, openFindings } from '../../api/endpoints/dfmFindings';
import { useComponentSelection } from '../../store/component-selection-store';
import { StageActions } from './StageActions';

function ContractIcon({ c }: { c: InterfaceContractView }) {
  const state = plainState(c);
  if (state === 'checked, it fits') return <CircleCheck size={13} aria-hidden="true" />;
  if (state === 'does not fit yet' || state.startsWith('needs')) return <CircleAlert size={13} aria-hidden="true" />;
  return <CircleDashed size={13} aria-hidden="true" />;
}

/**
 * Check: the twin's verification state, read only. The interface contracts
 * (the parts that must fit together) and the manufacturing (DFM) results,
 * each in plain words, with one primary action: run the checks again.
 * A result is never claimed before a real check ran on real data.
 */
export function CheckStage({ projectId, onAsk }: { projectId: string; onAsk: (text: string) => void }) {
  const qc = useQueryClient();
  const select = useComponentSelection((s) => s.select);
  const contracts = useQuery({
    queryKey: contractKeys.project(projectId),
    queryFn: ({ signal }) => getContracts(projectId, signal),
  });
  const dfm = useQuery({
    queryKey: dfmKeys.project(projectId),
    queryFn: ({ signal }) => getDfm(projectId, { signal }),
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runChecks = async () => {
    setBusy(true);
    setError(null);
    try {
      await Promise.all([checkContracts(projectId), evaluateDfm(projectId)]);
      await Promise.all([
        qc.invalidateQueries({ queryKey: contractKeys.project(projectId) }),
        qc.invalidateQueries({ queryKey: ['dfm', projectId] }),
      ]);
    } catch {
      setError('The checks could not run. Nothing was changed.');
    } finally {
      setBusy(false);
    }
  };

  const list = contracts.data?.contracts ?? [];
  const findings = openFindings(dfm.data);

  return (
    <div className="cv-stage-col">
      <StageActions
        primary={{ id: 'run_checks', label: busy ? 'Checking…' : 'Run the checks', onClick: () => void runChecks(), disabled: busy }}
        more={[{ id: 'ask', label: 'Ask the assistant about these results', onClick: () => onAsk('About the check results: ') }]}
      />
      {error && (
        <p className="cv-error" role="alert">
          {error}
        </p>
      )}

      <section className="cv-card" aria-labelledby="cv-contracts-title" data-testid="check-contracts">
        <h3 id="cv-contracts-title">Parts that must fit together</h3>
        {list.length ? (
          <ul className="cv-blockers">
            {list.map((c) => (
              <li key={c.key} data-testid={`fit-${c.key}`}>
                <ContractIcon c={c} />
                <span>
                  {c.plain}: {plainState(c)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="cv-muted">{contracts.isLoading ? 'Loading…' : 'No interface contracts yet.'}</p>
        )}
      </section>

      <section className="cv-card" aria-labelledby="cv-dfm-title" data-testid="check-dfm">
        <h3 id="cv-dfm-title">Manufacturing checks</h3>
        {dfm.data && <p className="cv-muted">{dfm.data.status_label}</p>}
        {findings.length ? (
          <ul className="cv-blockers">
            {findings.map(({ component, finding }) => (
              <li key={finding.id}>
                <CircleAlert size={13} aria-hidden="true" />
                <span>
                  <button
                    type="button"
                    className="cv-link"
                    title="Highlight this part in the model"
                    onClick={() => select(projectId, { componentId: component.component_id, name: component.name, from: 'create' })}
                  >
                    {component.name}
                  </button>
                  : {finding.message}
                  {finding.severity === 'blocking' ? ' (blocks making)' : ''}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="cv-muted">{dfm.isLoading ? 'Loading…' : 'No open manufacturing suggestions.'}</p>
        )}
        {dfm.data?.disclaimer && <p className="cv-fineprint">{dfm.data.disclaimer}</p>}
      </section>
    </div>
  );
}
