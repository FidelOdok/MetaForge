import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '../../../../test/test-utils';
import type { ValidationFinding, ValidationReport as Report, Verdict } from '../../../../types/imagine';

const getValidation = vi.fn();
vi.mock('../../../../api/endpoints/imagine', async (orig) => ({
  ...(await orig<typeof import('../../../../api/endpoints/imagine')>()),
  getValidation: (...a: unknown[]) => getValidation(...a),
}));

import { ValidationReport, ValidationReportView } from '../ValidationReport';

const P = 'proj-validate';

function finding(over: Partial<ValidationFinding> = {}): ValidationFinding {
  return {
    id: 'f_x',
    check_id: 'ledger.reviewed',
    rule_version: 'imagine-v1@1.0.0',
    gate: 'handover',
    severity: 'blocking',
    status: 'fail',
    target: { kind: 'studio', id: 'ledger', revision: 1 },
    message: 'From the service.',
    evidence: [],
    resolver: 'agent',
    fix: null,
    accepted_by: [],
    ...over,
  };
}

function report(over: Partial<Report> = {}): Report {
  return {
    profile_id: 'imagine-v1',
    profile_version: '1.0.0',
    revision_number: 1,
    level: 'V1',
    gates: [],
    findings: [],
    debt: [],
    verdict: 'needs_person',
    limitations: [],
    project_id: P,
    computed_at: '2026-11-09T12:00:00Z',
    stale: false,
    ...over,
  };
}

function view(r: Report) {
  return render(<ValidationReportView report={r} />);
}

beforeEach(() => {
  getValidation.mockReset();
});

describe('the verdict', () => {
  const WORDS: Array<[Verdict, string]> = [
    ['ready_for_handover', 'Nothing blocks the handover'],
    ['needs_person', 'Waiting on you'],
    ['needs_agent', 'Waiting on the assistant'],
    ['blocked_external', 'Blocked outside the project'],
    ['invalid', 'The config has errors'],
  ];

  it.each(WORDS)('says %s in plain words', (verdict, word) => {
    view(report({ verdict }));
    expect(screen.getByTestId('validation-verdict')).toHaveTextContent(word);
  });

  it('shows the service verdict even when its findings look clean, and works none out itself', () => {
    // The page must never decide this. An all-pass report with a blocking verdict is shown as given.
    view(report({ verdict: 'needs_person', findings: [finding({ status: 'pass' })] }));
    expect(screen.getByTestId('validation-verdict')).toHaveTextContent('Waiting on you');
    expect(screen.queryByRole('list', { name: 'Waiting on you' })).not.toBeInTheDocument();
  });

  it('shows the level it was given and never higher than V2', () => {
    view(report({ level: 'V2' }));
    expect(screen.getByTestId('validation-level')).toHaveTextContent('Level V2');
    expect(screen.getByTestId('validation-level')).toHaveAttribute('title', expect.stringContaining('Nothing here is higher'));
  });
});

describe('who clears what', () => {
  it('groups open findings by who can clear them', () => {
    view(
      report({
        findings: [
          finding({ id: 'a', check_id: 'assumptions.settled', resolver: 'person', message: '3 assumptions to confirm.' }),
          finding({
            id: 'b',
            check_id: 'parts.interfaces',
            resolver: 'agent',
            message: 'usbc-a lacks interfaces.',
            fix: { op: 'set', pointer: '/parts/0/interface_count', needs: 'the interfaces the datasheet lists' },
          }),
          finding({ id: 'c', check_id: 'concept.mesh', resolver: 'external', message: 'No concept mesh.' }),
        ],
      }),
    );
    const person = screen.getByRole('list', { name: 'Waiting on you' });
    expect(within(person).getByText(/3 assumptions to confirm/)).toBeInTheDocument();
    expect(within(person).getByText(/Confirm or accept each assumption in the studio/)).toBeInTheDocument();
    const agent = screen.getByRole('list', { name: 'Waiting on the assistant' });
    expect(within(agent).getByText(/usbc-a lacks interfaces/)).toBeInTheDocument();
    expect(within(agent).getByText(/Needs: the interfaces the datasheet lists/)).toBeInTheDocument();
    const outside = screen.getByRole('list', { name: 'Outside the project' });
    expect(within(outside).getByText(/No concept mesh/)).toBeInTheDocument();
  });

  it('leaves out a group with nothing in it', () => {
    view(report({ findings: [finding({ resolver: 'agent' })] }));
    expect(screen.queryByRole('list', { name: 'Waiting on you' })).not.toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Outside the project' })).not.toBeInTheDocument();
  });

  it('does not list a pass, an assumption or an advisory finding as waiting', () => {
    view(
      report({
        findings: [
          finding({ id: 'p', status: 'pass', message: 'A pass.' }),
          finding({ id: 'a', status: 'assumed', message: 'An assumption.' }),
          finding({ id: 'v', severity: 'advisory', message: 'Advisory only.' }),
        ],
      }),
    );
    expect(screen.queryByText(/A pass\./)).not.toBeInTheDocument();
    expect(screen.queryByText(/An assumption\./)).not.toBeInTheDocument();
    expect(screen.queryByText(/Advisory only\./)).not.toBeInTheDocument();
  });

  it('names which items a finding is about, and how many more', () => {
    const evidence = ['a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7', 'a8'].map((id) => ({ kind: 'assumption', id }));
    view(report({ findings: [finding({ resolver: 'person', evidence })] }));
    expect(screen.getByText(/a1, a2, a3, a4, a5, a6 and 2 more/)).toBeInTheDocument();
  });

  it('shows a requirement by the name the person knows, not the studio\'s internal one', () => {
    const evidence = ['img_req_interval', 'img_custom_battery', 'img_studio_extra'].map((id) => ({
      kind: 'requirement',
      id,
    }));
    view(report({ debt: [finding({ status: 'assumed', severity: 'advisory', evidence })] }));
    expect(screen.getByText(/interval, battery, extra/)).toBeInTheDocument();
    expect(screen.queryByText(/img_req_/)).not.toBeInTheDocument();
  });

  it('says a default verification method is assumed, since nobody accepted it', () => {
    view(
      report({
        debt: [
          finding({
            check_id: 'requirements.sourced',
            status: 'assumed',
            severity: 'advisory',
            message: '11 sourced; 10 use the default verification method.',
          }),
        ],
      }),
    );
    const debt = screen.getByRole('list', { name: 'Assumed, not confirmed' });
    expect(within(debt).getByText(/use the default verification method/)).toBeInTheDocument();
    expect(within(debt).queryByText(/accepted by/)).not.toBeInTheDocument();
  });

  it('shows a finding that could not run, or is inconclusive, as waiting', () => {
    view(
      report({
        findings: [
          finding({ id: 'n', status: 'not_assessed', resolver: 'external', message: 'Not assessed.' }),
          finding({ id: 'i', status: 'inconclusive', resolver: 'agent', message: 'Inconclusive.' }),
        ],
      }),
    );
    expect(screen.getByText(/Not assessed\./)).toBeInTheDocument();
    expect(screen.getByText(/Inconclusive\./)).toBeInTheDocument();
  });
});

describe('what was accepted and not confirmed', () => {
  it('lists the debt and who accepted it, and says it is not a person confirming', () => {
    view(
      report({
        verdict: 'ready_for_handover',
        debt: [
          finding({
            check_id: 'assumptions.settled',
            status: 'assumed',
            severity: 'advisory',
            resolver: 'person',
            message: '2 of 3 accepted as assumptions, not confirmed.',
            accepted_by: ['agent claude on behalf of fidel'],
          }),
        ],
      }),
    );
    const debt = screen.getByRole('list', { name: 'Assumed, not confirmed' });
    expect(within(debt).getByText(/2 of 3 accepted as assumptions/)).toBeInTheDocument();
    expect(within(debt).getByText(/accepted by agent claude on behalf of fidel; not confirmed by a person/)).toBeInTheDocument();
  });

  it('has no debt section when nothing was accepted', () => {
    view(report({ verdict: 'ready_for_handover' }));
    expect(screen.queryByText('Assumed, not confirmed')).not.toBeInTheDocument();
  });
});

describe('leaving the Define phase', () => {
  const gate = (decision: 'pass' | 'fail' | 'incomplete') => ({
    gate: 'define_exit',
    rule_version: 'imagine-v1@1.0.0',
    decision,
    blocking: 0,
    assumed: 0,
    advisory: 0,
  });

  it('says it is stricter, and lists what stops it', () => {
    view(
      report({
        gates: [gate('fail')],
        findings: [
          finding({
            id: 'd',
            gate: 'define_exit',
            severity: 'advisory',
            check_id: 'define.assumptions_confirmed',
            message: '0 of 3 confirmed; accepting an assumption does not confirm it.',
            resolver: 'person',
          }),
          finding({ id: 'ok', gate: 'define_exit', severity: 'advisory', status: 'pass', check_id: 'define.markets_chosen', message: 'UK chosen.' }),
        ],
      }),
    );
    const define = screen.getByTestId('define-exit');
    expect(define).toHaveTextContent('Not ready (1)');
    expect(define).toHaveTextContent(/Stricter than the handover/);
    expect(within(define).getByText(/accepting an assumption does not confirm it/)).toBeInTheDocument();
    expect(within(define).queryByText(/UK chosen/)).not.toBeInTheDocument();
  });

  it.each([
    ['pass', 'Ready'],
    ['incomplete', 'Incomplete'],
  ] as const)('reads %s as %s', (decision, word) => {
    view(report({ gates: [gate(decision)] }));
    expect(screen.getByTestId('define-exit')).toHaveTextContent(word);
  });

  it('is not shown when the service did not evaluate it', () => {
    view(report({ gates: [] }));
    expect(screen.queryByTestId('define-exit')).not.toBeInTheDocument();
  });
});

describe('when the verification service could not answer', () => {
  it('shows the one finding outside the project, in plain words, and the blocked verdict', () => {
    view(
      report({
        verdict: 'blocked_external',
        level: 'V0',
        findings: [
          finding({
            check_id: 'verification.service',
            gate: 'verification',
            status: 'not_assessed',
            resolver: 'external',
            message: 'could not reach the verification service (ConnectError)',
          }),
        ],
      }),
    );
    expect(screen.getByTestId('validation-verdict')).toHaveTextContent('Blocked outside the project');
    const outside = screen.getByRole('list', { name: 'Outside the project' });
    expect(within(outside).getByText(/could not reach the verification service/)).toBeInTheDocument();
    expect(screen.getByTestId('validation-level')).toHaveTextContent('Level V0');
  });
});

describe('loading it', () => {
  it('shows the report from the gateway', async () => {
    getValidation.mockResolvedValue(report({ verdict: 'needs_agent' }));
    render(<ValidationReport projectId={P} />);
    expect(await screen.findByTestId('validation-verdict')).toHaveTextContent('Waiting on the assistant');
    expect(getValidation).toHaveBeenCalledWith(P, expect.anything());
  });

  it('says it is checking while it loads', () => {
    getValidation.mockReturnValue(new Promise(() => {}));
    render(<ValidationReport projectId={P} />);
    expect(screen.getByText(/Checking what is left/)).toBeInTheDocument();
  });

  it('shows a failure to load instead of hiding it', async () => {
    getValidation.mockRejectedValue({ response: { status: 502, data: { detail: 'The Imagine studio is unavailable' } } });
    render(<ValidationReport projectId={P} />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be loaded/);
  });

  it('does not ask when it is not enabled', () => {
    render(<ValidationReport projectId={P} enabled={false} />);
    expect(getValidation).not.toHaveBeenCalled();
  });
});
