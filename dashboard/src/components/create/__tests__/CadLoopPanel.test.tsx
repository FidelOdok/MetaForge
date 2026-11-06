import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '../../../test/test-utils';
import * as api from '../../../api/endpoints/cadLoop';
import type { LoopIteration, LoopRun, LoopRuns } from '../../../api/endpoints/cadLoop';
import { CadLoopPanel, latestRunFor, latestSheet } from '../CadLoopPanel';
import { CadLoopSection, scriptDiff } from '../../viewer/CadLoopSection';

const P = 'p1';
const SPEND = { used_last_hour: 0, cap: 3, policy_mode: 'off', policy_would_approve: false };

function iteration(over: Partial<LoopIteration> = {}): LoopIteration {
  return {
    n: 1,
    script: 'import cadquery as cq\nresult = cq.Workplane().box(20, 10, 2)',
    summary: 'a plate',
    focus: 'first shape',
    build: { ok: true },
    validity: { outcome: 'pass' },
    measurements: { size_mm: { x: 20, y: 10, z: 2 }, volume_mm3: 400, dfm_measure: { min_wall_mm: 0.6 } },
    dfm: {
      status: 'issues_found',
      profile: 'fdm@1.0.0',
      findings: [{ rule_id: 'fdm.min_wall', rule_title: 'Minimum wall thickness', severity: 'advisory' }],
    },
    render: {
      ok: true,
      sheet: 'iter-1-sheet.png',
      views: [
        { view: 'iso', label: 'Iso', file: 'iter-1-iso.png' },
        { view: 'front', label: 'Front', file: 'iter-1-front.png' },
      ],
    },
    critique: {
      satisfied: false,
      verdict: 'not_satisfied',
      issues: [{ severity: 'major', view: 'front', feature: 'left wall', description: 'wall looks thin', suggested_change: 'thicken it' }],
    },
    gate: { critic_satisfied: false, validity_pass: true, no_blocking_dfm: true, dimensions_in_tolerance: true, passed: false },
    models: { author: { provider: 'openrouter', model: 'm1', cost_usd: 0.01 }, critic: { provider: 'openrouter', model: 'v1', cost_usd: null } },
    ...over,
  };
}

function run(over: Partial<LoopRun> = {}): LoopRun {
  return {
    node_id: 'loop1',
    name: 'CAD design loop: Plate',
    component_id: 'CMP-plate',
    component_name: 'Plate',
    created_at: '2026-11-05T10:00:00Z',
    status: 'finished',
    stop_reason: 'iteration_cap',
    stop_detail: '2 iterations ran',
    iterations: [
      iteration(),
      iteration({
        n: 2,
        focus: 'fixing wall thickness',
        script: 'import cadquery as cq\nresult = cq.Workplane().box(20, 10, 3)',
        render: { ok: true, sheet: 'iter-2-sheet.png', views: [{ view: 'iso', file: 'iter-2-iso.png' }] },
      }),
    ],
    result: {
      status: 'proposed',
      iteration: 2,
      cad_key: 'CAD-PLATE',
      base_revision: '1',
      satisfied: false,
      open_issues: ['Review (major): wall looks thin'],
      assumptions: [],
      validated: false,
    },
    budget: { model_calls: 4, spend_usd: 0.02, max_model_calls: 10, max_spend_usd: 1, unpriced_calls: 2 },
    decision: null,
    context: { sketch_ref: 'PSK-PLATE@2', cad_key: 'CAD-PLATE', base_revision: '1' },
    ...over,
  };
}

function runs(over: Partial<LoopRuns> = {}): LoopRuns {
  return { runs: [run()], active_jobs: [], spend: SPEND, note: '', ...over };
}

const spendError = {
  response: { status: 409, data: { detail: { code: 'spend_confirmation_required', message: '3 refinements this hour.' } } },
};
const started = { job_id: 'j1', status: 'running', operation_id: 'job:j1', spend: SPEND };

describe('CadLoopPanel (Create)', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('starts a refinement, asking before it spends past the cap', async () => {
    vi.spyOn(api, 'getCadLoops').mockResolvedValue(runs({ runs: [] }));
    const start = vi.spyOn(api, 'startCadLoop').mockRejectedValueOnce(spendError).mockResolvedValueOnce(started);
    render(<CadLoopPanel projectId={P} sketchKey="PSK-PLATE" componentName="Plate" />);
    await userEvent.click(await screen.findByRole('button', { name: 'Refine the model automatically' }));
    expect(await screen.findByTestId('cad-loop-spend')).toHaveTextContent('3 refinements this hour.');
    await userEvent.click(screen.getByRole('button', { name: 'Run it anyway' }));
    await waitFor(() => expect(start).toHaveBeenLastCalledWith(P, { sketch_key: 'PSK-PLATE', confirm_spend: true }));
  });

  it('shows the latest rendered views while it works', async () => {
    vi.spyOn(api, 'getCadLoops').mockResolvedValue(
      runs({ runs: [run({ status: 'running', result: null, stop_reason: null })], active_jobs: [{ job_id: 'j1', status: 'running' }] }),
    );
    render(<CadLoopPanel projectId={P} sketchKey="PSK-PLATE" componentName="Plate" />);
    expect(await screen.findByTestId('cad-loop-running')).toHaveTextContent('iteration 2');
    expect(screen.getByTestId('cad-loop-sheet').getAttribute('src')).toContain('/twin/nodes/loop1/files/iter-2-sheet.png');
    expect(screen.queryByRole('button', { name: 'Refine the model automatically' })).not.toBeInTheDocument();
  });

  it('ends with the open issues in plain words and the person choosing', async () => {
    vi.spyOn(api, 'getCadLoops').mockResolvedValue(runs());
    const decide = vi.spyOn(api, 'decideCadLoop').mockResolvedValue(run());
    render(<CadLoopPanel projectId={P} sketchKey="PSK-PLATE" />);
    expect(await screen.findByTestId('cad-loop-stop')).toHaveTextContent('Stopped at the iteration limit with issues still open');
    expect(screen.getByLabelText('Open issues')).toHaveTextContent('wall looks thin');
    await userEvent.click(screen.getByRole('button', { name: 'Keep the previous one' }));
    await waitFor(() => expect(decide).toHaveBeenCalledWith(P, 'loop1', 'reject'));
    await userEvent.click(screen.getByRole('button', { name: 'Use this version' }));
    await waitFor(() => expect(decide).toHaveBeenLastCalledWith(P, 'loop1', 'accept'));
  });

  it('asks the question instead of guessing, and runs again with the answer', async () => {
    const asked = run({
      stop_reason: 'needs_input',
      result: null,
      questions: [{ name: 'feat-1.depth', question: 'How thick should the plate be?' }],
    });
    vi.spyOn(api, 'getCadLoops').mockResolvedValue(runs({ runs: [asked] }));
    const start = vi.spyOn(api, 'startCadLoop').mockResolvedValue(started);
    render(<CadLoopPanel projectId={P} sketchKey="PSK-PLATE" />);
    expect(await screen.findByText(/nothing was guessed/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('How thick should the plate be?'), '3');
    await userEvent.click(screen.getByRole('button', { name: 'Answer and continue' }));
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(P, { sketch_key: 'PSK-PLATE', answers: { 'feat-1.depth': 3 }, confirm_spend: false }),
    );
  });

  it('picks the run of this plan and its newest sheet', () => {
    const other = run({ node_id: 'x', context: { sketch_ref: 'PSK-OTHER@1' } });
    expect(latestRunFor([other, run()], 'PSK-PLATE')?.node_id).toBe('loop1');
    expect(latestSheet(run())).toEqual({ iteration: 2, file: 'iter-2-sheet.png' });
    expect(latestSheet(null)).toBeNull();
  });
});

describe('CadLoopSection (Engineering)', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('shows each iteration with views, review, measurements, DFM and the script diff', async () => {
    vi.spyOn(api, 'getCadLoops').mockResolvedValue(runs());
    render(<CadLoopSection projectId={P} />);
    const first = await screen.findByTestId('cad-loop-iteration-1');
    expect(first).toHaveTextContent('first shape');
    expect(first).toHaveTextContent('20 x 10 x 2');
    expect(first).toHaveTextContent('thinnest wall 0.6 mm');
    expect(first).toHaveTextContent('Minimum wall thickness');
    expect(first).toHaveTextContent('wall looks thin');
    expect(first).toHaveTextContent('(advisory)');
    expect(screen.getAllByAltText(/iteration 1/).length).toBe(2);
    expect(screen.getByTestId('cad-loop-diff-2')).toHaveTextContent('- result = cq.Workplane().box(20, 10, 2)');
    expect(screen.getByTestId('cad-loop-diff-2')).toHaveTextContent('+ result = cq.Workplane().box(20, 10, 3)');
    expect(screen.getByTestId('cad-loop-run-loop1')).toHaveTextContent('a proposal, not validated');
  });

  it('diffs scripts line by line', () => {
    expect(scriptDiff('a\nb\nc', 'a\nx\nc')).toEqual([
      { kind: 'same', text: 'a' },
      { kind: 'removed', text: 'b' },
      { kind: 'added', text: 'x' },
      { kind: 'same', text: 'c' },
    ]);
  });
});
