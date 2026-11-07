import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../../test/test-utils';
import type { CreationView } from '../../../api/endpoints/create';
import type { Readiness } from '../../../api/endpoints/dfmFindings';

const getReleaseChecklist = vi.fn();
vi.mock('../../../api/endpoints/dfmFindings', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/dfmFindings')>()),
  getReleaseChecklist: (...a: unknown[]) => getReleaseChecklist(...a),
}));
vi.mock('../MakePanel', () => ({ MakePanel: () => <div data-testid="make-panel" /> }));
vi.mock('../InspectPanel', () => ({ InspectPanel: () => <div data-testid="inspect-panel" /> }));

import { MakeStage, readinessText } from '../MakeStage';

const P = 'proj-make';

function creation(builds: unknown[] = []): CreationView {
  return { readiness: { blockers: [] }, creation: { builds } } as unknown as CreationView;
}

const BUILD = {
  id: 'b1',
  number: 1,
  status: 'completed',
  route: 'home',
  process: 'FDM 3D printing',
  revision: { work_product_id: 'wp', version: 2, label: 'Bracket rev 2' },
  acceptance: null,
};

const readiness = (over: Partial<Readiness>): Readiness => ({ level: 'in_progress', label: '', prototype: false, production: false, next_steps: [], ...over });

beforeEach(() => {
  getReleaseChecklist.mockReset().mockResolvedValue({
    project_id: P,
    applies: true,
    readiness: readiness({ next_steps: ['Validate the exact model'] }),
    items: [
      { id: 'components_exact', label: 'Every component has an exact model', status: 'pass', detail: '', blockers: [], next_steps: [] },
      { id: 'dfm', label: 'Manufacturing checks pass', status: 'fail', detail: '1 blocking', blockers: [], next_steps: [] },
    ],
  });
});

describe('Make', () => {
  it('shows the release checklist and the builds', async () => {
    render(<MakeStage projectId={P} view={creation([BUILD])} model={undefined} modelRevision={null} onAsk={vi.fn()} />);
    expect(await screen.findByText('Not ready to make yet: 1 next step')).toBeInTheDocument();
    expect(screen.getByTestId('release-checklist')).toHaveTextContent('Manufacturing checks pass · 1 blocking');
    expect(screen.getByTestId('make-outputs')).toHaveTextContent('Build 1');
  });

  it('has one primary action that follows the builds, and opens its detail in place', async () => {
    const { unmount } = render(<MakeStage projectId={P} view={creation()} model={undefined} modelRevision={null} onAsk={vi.fn()} />);
    expect(screen.getAllByTestId('stage-primary')).toHaveLength(1);
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Plan the making');
    await userEvent.click(screen.getByTestId('stage-primary'));
    expect(screen.getByTestId('make-panel')).toBeInTheDocument();
    unmount();

    render(<MakeStage projectId={P} view={creation([BUILD])} model={undefined} modelRevision={null} onAsk={vi.fn()} />);
    expect(screen.getByTestId('stage-primary')).toHaveTextContent('Inspect the result');
    await userEvent.click(screen.getByTestId('stage-primary'));
    expect(screen.getByTestId('inspect-panel')).toBeInTheDocument();
  });

  it('claims prototype or production readiness only when the checklist backs it', () => {
    expect(readinessText(readiness({ production: true, prototype: true }))).toBe('Ready for production');
    expect(readinessText(readiness({ prototype: true }))).toBe('Ready for prototype');
    expect(readinessText(readiness({}))).toBe('Not ready to make yet');
  });
});
