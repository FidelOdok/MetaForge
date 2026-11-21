import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getApprovalPolicy,
  putApprovalPolicy,
  type ApprovalPolicy,
  type ApprovalPolicyUpdate,
  type Delegation,
} from '../../api/endpoints/approvals';
import { DeploymentLimitsNote } from './DeploymentLimitsNote';

/**
 * FORGE-726/730: who decides this project's design-flow gates and its held
 * tool writes. FORGE-904: the mode is capped by the account and the deployment,
 * and agents deciding for a person can be turned off for this project; the panel
 * says which layer holds each one.
 *
 * Only a person sets this, from here: the gateway refuses an agent, and no
 * MCP tool sets it. Modes above the gateway's cap are shown but cannot be
 * chosen, so the person can see what exists and why it is unavailable.
 */

const MODE_TEXT: Record<ApprovalPolicy['mode'], string> = {
  off: 'Off: a person decides every gate',
  advise: 'Advise: the reviewer recommends, a person decides',
  auto_clean: 'Auto (clean gates): the reviewer approves gates whose checks all passed',
  auto: 'Auto: the reviewer decides until it expires (test runs)',
};

const REVIEWER_TEXT: Record<ApprovalPolicy['reviewer'], string> = {
  server: 'Server: the gateway runs the reviewer',
  client: "Client: a connected client's reviewer subagent",
  client_then_server: 'Client, then server if no client takes it',
};

const keyFor = (projectId: string) => ['approval-policy', projectId] as const;

const LIMITED_BY: Record<string, string> = {
  expired: 'auto has expired, so advise is in force',
  account: 'held there by your account’s limit (Settings, Approvals)',
  deployment: 'held there by this MetaForge’s limit',
};

const LAYER: Record<Delegation['decided_by'], string> = {
  deployment: 'this MetaForge',
  account: 'your account',
  project: 'this project',
};

type Switch = 'follow' | 'off';

export function ApprovalPolicyPanel({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: keyFor(projectId),
    queryFn: () => getApprovalPolicy(projectId),
  });
  const save = useMutation({
    mutationFn: (body: ApprovalPolicyUpdate) => putApprovalPolicy(projectId, body),
    onSuccess: (view) => qc.setQueryData(keyFor(projectId), view),
  });
  const [mode, setMode] = useState<ApprovalPolicy['mode']>('off');
  const [reviewer, setReviewer] = useState<ApprovalPolicy['reviewer']>('server');
  const [model, setModel] = useState('');
  const [hours, setHours] = useState(24);
  const [human, setHuman] = useState<Switch>('follow');
  const [release, setRelease] = useState<Switch>('follow');

  useEffect(() => {
    if (!data) return;
    setMode(data.policy.mode);
    setReviewer(data.policy.reviewer);
    setModel(data.policy.reviewer_model);
    setHuman(data.policy.allow_human_authority === false ? 'off' : 'follow');
    setRelease(data.policy.allow_release === false ? 'off' : 'follow');
  }, [data]);

  if (isLoading) return <p role="status">Loading the approval policy…</p>;
  if (isError || !data) return <p role="alert">The approval policy could not be loaded.</p>;

  const cap = data.modes.indexOf(data.max_mode);
  const deploymentCap = data.deployment ? data.modes.indexOf(data.deployment.max_mode) : cap;
  const above = (i: number) =>
    i > deploymentCap ? ' (above this MetaForge’s limit)' : i > cap ? ' (above your account’s limit)' : '';
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate({
      mode,
      reviewer,
      reviewer_model: model.trim(),
      ...(mode === 'auto' ? { expires_in_hours: hours } : {}),
      allow_human_authority: human === 'off' ? false : null,
      allow_release: release === 'off' ? false : null,
    });
  };

  return (
    <form className="glass rounded p-4 space-y-3" onSubmit={onSubmit} aria-label="Approval policy">
      <p style={{ margin: 0, fontSize: 13 }} data-testid="policy-in-force">
        In force now: <strong>{data.effective_mode}</strong>
        {data.policy.expires_at && data.policy.mode === 'auto'
          ? ` (auto until ${new Date(data.policy.expires_at).toLocaleString()})`
          : ''}
        {data.policy.updated_by ? `, set by ${data.policy.updated_by}` : ''}
        {data.limited_by && data.effective_mode !== data.policy.mode
          ? `. This project chose ${data.policy.mode}, ${LIMITED_BY[data.limited_by] ?? data.limited_by}.`
          : ''}
      </p>
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend style={{ fontSize: 12, marginBottom: 6 }}>Who decides gates and held tool writes</legend>
        {data.modes.map((m, i) => (
          <label key={m} style={{ display: 'block', fontSize: 13, opacity: i > cap ? 0.5 : 1 }}>
            <input
              type="radio"
              name="mode"
              value={m}
              checked={mode === m}
              disabled={i > cap}
              onChange={() => setMode(m)}
            />{' '}
            {MODE_TEXT[m]}
            {above(i)}
          </label>
        ))}
      </fieldset>
      {mode !== 'off' && (
        <>
          <label style={{ display: 'block', fontSize: 13 }}>
            Reviewer{' '}
            <select value={reviewer} onChange={(e) => setReviewer(e.target.value as ApprovalPolicy['reviewer'])}>
              {Object.entries(REVIEWER_TEXT).map(([v, t]) => (
                <option key={v} value={v}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label style={{ display: 'block', fontSize: 13 }}>
            Reviewer model{' '}
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="provider:model, or empty for the default"
            />
          </label>
        </>
      )}
      {mode === 'auto' && (
        <label style={{ display: 'block', fontSize: 13 }}>
          Auto for{' '}
          <input
            type="number"
            min={1}
            max={168}
            value={hours}
            onChange={(e) => setHours(Number(e.target.value) || 24)}
            style={{ width: 70 }}
          />{' '}
          hours, then advise again
        </label>
      )}
      <fieldset style={{ border: 0, padding: 0, margin: 0 }} data-testid="project-delegation">
        <legend style={{ fontSize: 12, marginBottom: 6 }}>Agents acting for a person on this project</legend>
        {(
          [
            ['human_authority', 'Accept, hand over, rework and decide uploads', human, setHuman],
            ['release', 'Fix the release baseline and approve a release', release, setRelease],
          ] as const
        ).map(([kind, label, value, set]) => {
          const d = data.delegation?.[kind];
          return (
            <label key={kind} style={{ display: 'block', fontSize: 13 }}>
              {label}{' '}
              <select aria-label={label} value={value} onChange={(e) => set(e.target.value as Switch)}>
                <option value="follow">Follow your account</option>
                <option value="off">Off for this project</option>
              </select>
              {d && (
                <span style={{ marginLeft: 6, opacity: 0.8 }} data-testid={`delegation-${kind}`}>
                  {d.allowed ? 'allowed' : 'not allowed'}, by {LAYER[d.decided_by]}
                </span>
              )}
            </label>
          );
        })}
      </fieldset>
      <button type="submit" className="action-primary" disabled={save.isPending}>
        Save approval policy
      </button>
      {save.isError && (
        <p role="alert" style={{ margin: 0 }}>
          Not saved: {(save.error as Error).message}
        </p>
      )}
      {data.deployment && <DeploymentLimitsNote limits={data.deployment} />}
    </form>
  );
}
