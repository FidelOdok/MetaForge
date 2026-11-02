import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getApprovalPolicy,
  putApprovalPolicy,
  type ApprovalPolicy,
  type ApprovalPolicyUpdate,
} from '../../api/endpoints/approvals';

/**
 * FORGE-726/730: who decides this project's design-flow gates.
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

  useEffect(() => {
    if (!data) return;
    setMode(data.policy.mode);
    setReviewer(data.policy.reviewer);
    setModel(data.policy.reviewer_model);
  }, [data]);

  if (isLoading) return <p role="status">Loading the approval policy…</p>;
  if (isError || !data) return <p role="alert">The approval policy could not be loaded.</p>;

  const cap = data.modes.indexOf(data.max_mode);
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate({
      mode,
      reviewer,
      reviewer_model: model.trim(),
      ...(mode === 'auto' ? { expires_in_hours: hours } : {}),
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
      </p>
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend style={{ fontSize: 12, marginBottom: 6 }}>Who decides gates</legend>
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
            {i > cap ? ' (above this gateway’s cap)' : ''}
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
      <button type="submit" className="action-primary" disabled={save.isPending}>
        Save approval policy
      </button>
      {save.isError && (
        <p role="alert" style={{ margin: 0 }}>
          Not saved: {(save.error as Error).message}
        </p>
      )}
    </form>
  );
}
