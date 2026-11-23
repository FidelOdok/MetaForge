import type { DeploymentLimits } from '../../api/endpoints/approvals';

/**
 * FORGE-904: what the people who run this MetaForge allow. Read only: these are
 * set in the deployment's environment, so no account can raise its own limits.
 */

export const MODE_NAME: Record<DeploymentLimits['max_mode'], string> = {
  off: 'Off',
  advise: 'Advise',
  auto_clean: 'Auto (clean gates)',
  auto: 'Auto (test runs)',
};

const TOOL_NAME: Record<string, string> = {
  'twin.render_concept_image': 'concept images',
  'twin.generate_sketch': 'concept sketches',
  'twin.generate_concept_mesh': 'concept meshes',
  'twin.segment_concept_mesh': 'concept part splits',
  'twin.regenerate_concept_part': 'concept part redraws',
  'twin.complete_concept_part': 'concept part completions',
  'twin.design_cad_loop': 'CAD design loop runs',
  'cadquery.execute_script': 'CadQuery scripts',
};

/** Said wherever approval settings are shown: with approvals off, no setting below holds a call. */
export function ApprovalsOffNotice({ limits }: { limits?: DeploymentLimits | null }) {
  if (!limits?.approvals_off) return null;
  return (
    <p role="status" data-testid="approvals-off" className="mf-approvals-off">
      Approvals are off on this MetaForge (METAFORGE_APPROVALS=off): nothing waits for anyone. Each approval is
      approved automatically and recorded as approved by the switch. Placing an order, and a gate whose own checks
      fail, still wait.
    </p>
  );
}

export function DeploymentLimitsNote({ limits }: { limits: DeploymentLimits }) {
  const yes = (v: boolean) => (v ? 'allowed' : 'not allowed');
  return (
    <details data-testid="deployment-limits" style={{ fontSize: 12 }}>
      <summary style={{ cursor: 'pointer' }}>
        This MetaForge's limits: highest mode <strong>{MODE_NAME[limits.max_mode]}</strong>; agents deciding
        for a person {yes(limits.allow_human_authority)}; agents releasing {yes(limits.allow_release)}
      </summary>
      <p style={{ margin: '6px 0' }}>
        Set by the people who run this MetaForge, in its environment. An account or a project can stay
        below these limits but never go above them.
      </p>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        <li>Approvals: {limits.approvals_off ? 'off (METAFORGE_APPROVALS=off)' : 'on'}</li>
        <li>Highest approval mode any project may use: {MODE_NAME[limits.max_mode]}</li>
        <li>
          Agents may decide accept, hand over, rework and uploads for a person:{' '}
          {yes(limits.allow_human_authority)}
        </li>
        <li>Agents may fix the release baseline and approve a release: {yes(limits.allow_release)}</li>
        <li>An approval for a held call stays usable for {Math.round(limits.approval_ttl_seconds / 60)} minutes</li>
        <li>
          Approved automatically at most, per project per hour:{' '}
          {limits.hourly_caps
            .filter((c) => c.per_hour !== null)
            .map((c) => `${c.per_hour} ${TOOL_NAME[c.tool] ?? c.tool}`)
            .join(', ')}
        </li>
      </ul>
      <p style={{ margin: '6px 0 0' }}>
        Never approved automatically, whatever the settings: destructive tools, spending, and project, flow,
        library and vendor tools.
      </p>
    </details>
  );
}
