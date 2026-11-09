import { CircleAlert, CircleCheck, CircleDashed } from 'lucide-react';
import { refusalOf } from '../../../api/endpoints/create';
import { useValidation } from '../../../hooks/use-imagine';
import type {
  Resolver,
  ValidationFinding,
  ValidationReport as Report,
  Verdict,
} from '../../../types/imagine';

/**
 * What is left before Imagine can be handed over, and who clears each thing.
 *
 * The verification service decides all of it; this only shows what it said. The
 * verdict, the level, the findings and who can clear them are never worked out
 * here, so the page cannot show a pass the service did not compute. The words
 * below are presentation: they say the same thing in plain language.
 */

const VERDICT_WORD: Record<Verdict, string> = {
  ready_for_handover: 'Nothing blocks the handover',
  needs_person: 'Waiting on you',
  needs_agent: 'Waiting on the assistant',
  blocked_external: 'Blocked outside the project',
  invalid: 'The config has errors',
};

const VERDICT_HELP: Record<Verdict, string> = {
  ready_for_handover:
    'Nothing is blocking. Anything accepted as an assumption is listed below and is not confirmed. The handover button is yours.',
  needs_person: 'Only you can clear these. The handover button is yours.',
  needs_agent: 'The assistant can fix these. Ask it to carry on.',
  blocked_external: 'Something outside the project has to happen first.',
  invalid: 'The studio config does not pass its checks. Ask the assistant to fix it.',
};

const GROUPS: Array<{ resolver: Resolver; title: string }> = [
  { resolver: 'person', title: 'Waiting on you' },
  { resolver: 'agent', title: 'Waiting on the assistant' },
  { resolver: 'external', title: 'Outside the project' },
];

// What the person does for the findings only they can clear. Presentation
// only: the finding itself, and whether it is open, come from the service.
const WHAT_TO_DO: Record<string, string> = {
  'assumptions.settled': 'Confirm or accept each assumption in the studio.',
  'scenarios.confirmed': 'Confirm each scenario test in the studio.',
  'screening.stored': 'Open the studio so its checks run.',
};

const LEVEL_TITLE = 'Screening level: V0 captured, V1 consistent, V2 screened. Nothing here is higher.';
const MAX_REFS = 6;

function isOpen(f: ValidationFinding): boolean {
  return f.severity === 'blocking' && (f.status === 'fail' || f.status === 'inconclusive' || f.status === 'not_assessed');
}

function tone(f: ValidationFinding): 'done' | 'assumed' | 'missing' {
  if (f.status === 'pass') return 'done';
  if (f.status === 'assumed') return 'assumed';
  return 'missing';
}

function FindingIcon({ finding }: { finding: ValidationFinding }) {
  const t = tone(finding);
  if (t === 'done') return <CircleCheck size={14} aria-hidden="true" />;
  if (t === 'assumed') return <CircleDashed size={14} aria-hidden="true" />;
  return <CircleAlert size={14} aria-hidden="true" />;
}

/** A requirement's id is the studio's internal name; show the name the person knows. */
const plainId = (id: string) => id.replace(/^img_(?:req|custom|studio)_/, '');

/** The ids a finding is about, so the person can see which assumption or part. */
function refs(f: ValidationFinding): string {
  const ids = f.evidence.map((e) => plainId(e.id));
  if (ids.length === 0) return '';
  const shown = ids.slice(0, MAX_REFS).join(', ');
  return ids.length > MAX_REFS ? `${shown} and ${ids.length - MAX_REFS} more` : shown;
}

function Row({ finding }: { finding: ValidationFinding }) {
  const about = refs(finding);
  const hint =
    finding.resolver === 'person'
      ? WHAT_TO_DO[finding.check_id]
      : finding.resolver === 'agent' && finding.fix
        ? `Needs: ${finding.fix.needs}.`
        : undefined;
  return (
    <li className={`is-${tone(finding)}`} data-testid={`finding-${finding.check_id}`}>
      <FindingIcon finding={finding} />
      <span className="cv-checklist-label">
        {finding.message}
        {about && <span className="cv-muted"> · {about}</span>}
        {hint && <span className="cv-muted"> · {hint}</span>}
        {finding.accepted_by.length > 0 && (
          <span className="cv-muted">
            {' '}
            · accepted by {finding.accepted_by.join(', ')}; not confirmed by a person
          </span>
        )}
      </span>
      <span className="cv-checklist-state cv-mono">{finding.check_id}</span>
    </li>
  );
}

function Group({ title, findings, label }: { title: string; findings: ValidationFinding[]; label?: string }) {
  if (findings.length === 0) return null;
  return (
    <div className="cv-group">
      <h5>{title}</h5>
      <ul className="cv-checklist" aria-label={label ?? title}>
        {findings.map((f) => (
          <Row key={f.id} finding={f} />
        ))}
      </ul>
    </div>
  );
}

function DefineExit({ report }: { report: Report }) {
  const gate = report.gates.find((g) => g.gate === 'define_exit');
  if (!gate) return null;
  const open = report.findings.filter((f) => f.gate === 'define_exit' && f.status !== 'pass');
  const word = gate.decision === 'pass' ? 'Ready' : gate.decision === 'fail' ? 'Not ready' : 'Incomplete';
  return (
    <details className="cv-define" data-testid="define-exit">
      <summary>
        Leaving the Define phase: <strong>{word}</strong>
        {open.length > 0 ? ` (${open.length})` : ''}
      </summary>
      <p className="cv-muted">
        Stricter than the handover on purpose: an assumption that is only accepted passes the handover
        and does not pass this.
      </p>
      {open.length > 0 && (
        <ul className="cv-checklist" aria-label="Define phase checks">
          {open.map((f) => (
            <Row key={f.id} finding={f} />
          ))}
        </ul>
      )}
    </details>
  );
}

export function ValidationReportView({ report }: { report: Report }) {
  const open = report.findings.filter(isOpen);
  return (
    <>
      <header className="cv-validation-head">
        <h4>What is left, and who clears it</h4>
        <span className={`cv-verdict is-${report.verdict}`} role="status" data-testid="validation-verdict">
          {VERDICT_WORD[report.verdict]}
        </span>
        <span className="cv-muted cv-level" title={LEVEL_TITLE} data-testid="validation-level">
          Level {report.level}
        </span>
      </header>
      <p className="cv-muted">{VERDICT_HELP[report.verdict]}</p>
      {GROUPS.map((g) => (
        <Group key={g.resolver} title={g.title} findings={open.filter((f) => f.resolver === g.resolver)} />
      ))}
      <Group title="Assumed, not confirmed" findings={report.debt} />
      <DefineExit report={report} />
    </>
  );
}

/** The report for the head revision, from the gateway. A failure to load is shown, never hidden. */
export function ValidationReport({ projectId, enabled = true }: { projectId: string; enabled?: boolean }) {
  const validation = useValidation(projectId, enabled);
  return (
    <section className="cv-validation" aria-label="Validation report" data-testid="validation-report">
      {validation.data ? (
        <ValidationReportView report={validation.data} />
      ) : validation.isError ? (
        <p className="cv-muted" role="alert">
          The validation report could not be loaded: {refusalOf(validation.error)}
        </p>
      ) : (
        <p className="cv-muted">Checking what is left…</p>
      )}
    </section>
  );
}
