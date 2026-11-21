import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getAccountApprovalPolicy,
  putAccountApprovalPolicy,
  type AccountPolicyUpdate,
  type ApprovalMode,
} from '../../api/endpoints/approvals';
import { DeploymentLimitsNote, MODE_NAME } from './DeploymentLimitsNote';

/**
 * FORGE-904: your account's approval settings, for every project of the account.
 *
 * They can only narrow what the deployment allows: the highest mode any of your
 * projects may use, and whether agents may decide steps or releases for you. A
 * project can narrow them further on its own page.
 */

export const ACCOUNT_POLICY_KEY = ['account-approval-policy'] as const;

type Switch = 'follow' | 'off';

export function AccountApprovalSettings() {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ACCOUNT_POLICY_KEY,
    queryFn: getAccountApprovalPolicy,
    retry: false,
  });
  const save = useMutation({
    mutationFn: (body: AccountPolicyUpdate) => putAccountApprovalPolicy(body),
    onSuccess: (view) => {
      qc.setQueryData(ACCOUNT_POLICY_KEY, view);
      void qc.invalidateQueries({ queryKey: ['approval-policy'] });
    },
  });
  const [maxMode, setMaxMode] = useState<ApprovalMode | 'follow'>('follow');
  const [human, setHuman] = useState<Switch>('follow');
  const [release, setRelease] = useState<Switch>('follow');

  useEffect(() => {
    if (!data) return;
    setMaxMode(data.policy.max_mode ?? 'follow');
    setHuman(data.policy.allow_human_authority === false ? 'off' : 'follow');
    setRelease(data.policy.allow_release === false ? 'off' : 'follow');
  }, [data]);

  if (isLoading) return <p role="status">Loading your approval settings…</p>;
  if (isError || !data) return <p role="status">Your approval settings could not be loaded.</p>;

  const cap = data.modes.indexOf(data.deployment.max_mode);
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate({
      max_mode: maxMode === 'follow' ? null : maxMode,
      allow_human_authority: human === 'off' ? false : null,
      allow_release: release === 'off' ? false : null,
    });
  };
  const followText = (allowed: boolean) => `Follow this MetaForge (${allowed ? 'allowed' : 'not allowed'})`;

  return (
    <form className="space-y-3" onSubmit={onSubmit} aria-label="Account approval settings" data-testid="account-approvals">
      <p style={{ margin: 0, fontSize: 13 }} data-testid="account-in-force">
        Your projects may use at most <strong>{MODE_NAME[data.max_mode]}</strong>. Agents deciding for you:{' '}
        <strong>{data.allow_human_authority ? 'allowed' : 'not allowed'}</strong>. Agents releasing:{' '}
        <strong>{data.allow_release ? 'allowed' : 'not allowed'}</strong>.
        {data.policy.updated_by ? ` Set by ${data.policy.updated_by}.` : ''}
      </p>
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend style={{ fontSize: 12, marginBottom: 6 }}>Highest approval mode for all your projects</legend>
        <label style={{ display: 'block', fontSize: 13 }}>
          <input type="radio" name="account-max" checked={maxMode === 'follow'} onChange={() => setMaxMode('follow')} />{' '}
          Follow this MetaForge ({MODE_NAME[data.deployment.max_mode]})
        </label>
        {data.modes.map((m, i) => (
          <label key={m} style={{ display: 'block', fontSize: 13, opacity: i > cap ? 0.5 : 1 }}>
            <input
              type="radio"
              name="account-max"
              value={m}
              checked={maxMode === m}
              disabled={i > cap}
              onChange={() => setMaxMode(m)}
            />{' '}
            At most {MODE_NAME[m]}
            {i > cap ? ' (above this MetaForge’s limit)' : ''}
          </label>
        ))}
      </fieldset>
      <label style={{ display: 'block', fontSize: 13 }}>
        Agents may accept, hand over, rework and decide uploads for you{' '}
        <select
          aria-label="Agents may decide for you"
          value={human}
          onChange={(e) => setHuman(e.target.value as Switch)}
        >
          <option value="follow">{followText(data.deployment.allow_human_authority)}</option>
          <option value="off">Off for all my projects</option>
        </select>
      </label>
      <label style={{ display: 'block', fontSize: 13 }}>
        Agents may fix the release baseline and approve a release for you{' '}
        <select aria-label="Agents may release for you" value={release} onChange={(e) => setRelease(e.target.value as Switch)}>
          <option value="follow">{followText(data.deployment.allow_release)}</option>
          <option value="off">Off for all my projects</option>
        </select>
      </label>
      <button type="submit" className="action-primary" disabled={save.isPending}>
        Save approval settings
      </button>
      {save.isError && (
        <p role="alert" style={{ margin: 0 }}>
          Not saved: {(save.error as Error).message}
        </p>
      )}
      <DeploymentLimitsNote limits={data.deployment} />
    </form>
  );
}
