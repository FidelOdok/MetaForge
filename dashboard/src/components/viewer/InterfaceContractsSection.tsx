import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  agreeContract,
  checkContracts,
  contractKeys,
  getContracts,
  parameterText,
  type ContractCheck,
  type InterfaceContractView,
} from '../../api/endpoints/interfaceContracts';

const OUTCOME_LABEL: Record<string, string> = {
  satisfied: 'Satisfied',
  violated: 'Violated',
  not_evaluated: 'Not evaluated',
  stale: 'Stale',
};

function valuesText(values: Record<string, unknown>): string {
  return Object.entries(values)
    .filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && v.length === 0))
    .map(([k, v]) => `${k} ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(' · ');
}

function CheckRow({ check }: { check: ContractCheck }) {
  return (
    <li className={`is-${check.outcome === 'violated' ? 'blocking' : 'advisory'}`} data-testid={`contract-check-${check.id}`}>
      <div className="tw-dfm-finding-head">
        <span className="tw-dfm-severity">{OUTCOME_LABEL[check.outcome] ?? check.outcome}</span>
        <span>
          {check.label} <code>{check.id}</code>
        </span>
      </div>
      <p>{check.message}</p>
      {Object.keys(check.values).length > 0 && <p className="tw-dfm-numbers">Values: {valuesText(check.values)}</p>}
      {check.missing.length > 0 && (
        <ul className="tw-dfm-numbers">
          {check.missing.map((m) => (
            <li key={m}>Missing: {m}</li>
          ))}
        </ul>
      )}
      {check.inputs.length > 0 && <p className="tw-dfm-numbers">Read: {check.inputs.join(', ')}</p>}
    </li>
  );
}

function ContractBlock({ projectId, c }: { projectId: string; c: InterfaceContractView }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latest = c.latest_check;

  const agree = async (domain: string) => {
    setBusy(domain);
    setError(null);
    try {
      await agreeContract(projectId, c.key, domain);
      await qc.invalidateQueries({ queryKey: contractKeys.project(projectId) });
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'The agreement could not be recorded.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="tw-dfm-component" data-testid={`contract-${c.key}`}>
      <header>
        <strong>{c.title}</strong>
        <span className="tw-dfm-kind">
          {c.ref} · interface v{c.interface_version}
        </span>
        <span className={`tw-dfm-status is-${c.status}`} data-testid={`contract-status-${c.key}`}>
          {c.status}
        </span>
      </header>
      <dl className="tw-dfm-meta">
        <dt>Owners</dt>
        <dd>{c.owning_domains.join(', ')}</dd>
        <dt>Agreed</dt>
        <dd>
          {c.agreements.length
            ? c.agreements.map((a) => `${a.domain} (${a.decided_by})`).join(', ')
            : 'nobody yet'}
        </dd>
        <dt>Sides</dt>
        <dd>
          {c.links.length
            ? c.links
                .map((l) => `${l.role}: ${l.head_ref ?? `not linked (produced by ${l.produced_by || 'a later step'})`}`)
                .join('; ')
            : 'none'}
        </dd>
        <dt>Evidence</dt>
        <dd className={latest?.stale ? 'is-stale' : ''}>
          {latest
            ? `check ${latest.record_id} · ${latest.validation.status} · ${latest.staleness}${
                latest.staleness_reason ? `: ${latest.staleness_reason}` : ''
              }`
            : 'not checked yet'}
        </dd>
      </dl>
      {c.awaiting_agreement.length > 0 && (
        <div className="tw-dfm-actions" role="group" aria-label={`Agree to ${c.key}`}>
          {c.awaiting_agreement.map((d) => (
            <button key={d} type="button" disabled={busy !== null} onClick={() => void agree(d)}>
              {busy === d ? 'Recording…' : `Agree for ${d}`}
            </button>
          ))}
        </div>
      )}
      {error && (
        <p className="tw-dfm-none" role="alert">
          {error}
        </p>
      )}
      {c.parameters.length > 0 && (
        <table className="tw-contract-params" aria-label={`Coupled parameters of ${c.key}`}>
          <thead>
            <tr>
              <th>Parameter</th>
              <th>Value</th>
              <th>Owner</th>
              <th>Basis</th>
            </tr>
          </thead>
          <tbody>
            {c.parameters.map((p) => (
              <tr key={p.name}>
                <td>{p.label}</td>
                <td>{parameterText(p)}</td>
                <td>{p.owner}</td>
                <td>{p.basis}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {c.artifacts.length > 0 && (
        <ul className="tw-dfm-numbers" aria-label={`Expected artifacts of ${c.key}`}>
          {c.artifacts.map((a) => (
            <li key={a.kind}>
              {a.label}
              {a.parts.length ? ` (${a.parts.join(', ')})` : ''}: {a.rule}
            </li>
          ))}
        </ul>
      )}
      {latest ? (
        <>
          <p className="tw-dfm-numbers">{latest.summary}</p>
          <ul className="tw-dfm-findings">
            {latest.checks.map((check) => (
              <CheckRow key={check.id} check={check} />
            ))}
          </ul>
        </>
      ) : (
        <p className="tw-dfm-none">Not checked yet.</p>
      )}
    </section>
  );
}

/**
 * Engineering's Interfaces tab: the interface contracts between domains,
 * each with its coupled parameters (value, unit, tolerance, owner, basis),
 * the items on each side, who agreed, the status and the evidence of the
 * latest recorded check (each check's values, or what it was missing and
 * which step would produce it). Agreement is a person's recorded decision.
 */
export function InterfaceContractsSection({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const [running, setRunning] = useState(false);
  const view = useQuery({
    queryKey: contractKeys.project(projectId),
    queryFn: ({ signal }) => getContracts(projectId, signal),
  });

  const run = async () => {
    setRunning(true);
    try {
      await checkContracts(projectId);
      await qc.invalidateQueries({ queryKey: contractKeys.project(projectId) });
    } finally {
      setRunning(false);
    }
  };

  if (view.isLoading) return <p className="tw-dfm-none">Loading interface contracts…</p>;
  if (view.isError || !view.data) return <p className="tw-dfm-none">The interface contracts could not be read.</p>;
  const contracts = view.data.contracts;

  return (
    <div className="tw-dfm" data-testid="interface-contracts">
      <div className="tw-dfm-top">
        <span>Interfaces</span>
        <button type="button" onClick={() => void run()} disabled={running || !contracts.length}>
          {running ? 'Checking…' : 'Check now'}
        </button>
      </div>
      {contracts.length ? (
        contracts.map((c) => <ContractBlock key={c.key} projectId={projectId} c={c} />)
      ) : (
        <p className="tw-dfm-none">No interface contracts in this project.</p>
      )}
    </div>
  );
}
