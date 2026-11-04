import { describe, it, expect } from 'vitest';
import { render, screen } from '../../../test/test-utils';
import { GeometryStatusBadge, geometryBadgeText, geometryBadgeWord } from '../GeometryStatusBadge';
import { engineeringChecksText } from '../../create/ProductStage';
import { exactChecksState, EXACT_BUILD_TITLE } from '../../../lib/exact-checks';
import type { OperationView } from '../../../lib/operations-reducer';

describe('GeometryStatusBadge', () => {
  it('names the four badges and the revision', () => {
    expect(geometryBadgeWord('concept')).toBe('Concept');
    expect(geometryBadgeWord('preview')).toBe('Preview');
    expect(geometryBadgeWord('exact_ready')).toBe('Exact');
    expect(geometryBadgeWord('validated')).toBe('Validated');
    expect(geometryBadgeWord('released')).toBe('Validated');
    expect(geometryBadgeText('validated', 3)).toBe('Validated · revision 3');
    expect(geometryBadgeText('concept')).toBe('Concept (illustrative)');
    expect(geometryBadgeText('failed', 2)).toBe('Exact (check failed) · revision 2');
    expect(geometryBadgeText(null)).toBeNull();
  });

  it('never calls a concept validated or manufacturable', () => {
    render(
      <GeometryStatusBadge
        geometry={{ state: 'concept', badge: 'Concept', authority: 'illustrative', itemKey: null, sourceRevision: null }}
      />,
    );
    const badge = screen.getByText('Concept (illustrative)');
    expect(badge).toHaveAttribute('data-geometry-state', 'concept');
    expect(badge.textContent).not.toMatch(/validated|manufactur/i);
  });

  it('renders the node state with its source revision', () => {
    render(
      <GeometryStatusBadge
        geometry={{ state: 'validated', badge: 'Validated', authority: 'validated', itemKey: 'CAD-A', sourceRevision: 4 }}
      />,
    );
    expect(screen.getByText('Validated · revision 4')).toBeInTheDocument();
  });
});

function op(over: Partial<OperationView>): OperationView {
  return {
    operation_id: 'job:1',
    kind: 'job',
    domain: 'mechanical',
    title: EXACT_BUILD_TITLE,
    state: 'running',
    run_id: null,
    started_at: '2026-11-04T10:00:00Z',
    updated_at: '2026-11-04T10:00:00Z',
    last_sequence: 20,
    base_revision: null,
    target_revision: null,
    target_key: null,
    retry_of: null,
    message: '',
    severity: 'info',
    artifact_refs: [],
    requires_action: false,
    action_refs: [],
    progress: null,
    stages: [],
    milestones: [],
    log: [],
    ...over,
  } as OperationView;
}

describe('Create plain-language engineering checks', () => {
  it('says the preview updated while checks run, with no kernel names', () => {
    const text = engineeringChecksText(exactChecksState([op({})]));
    expect(text).toBe('Preview updated · Engineering checks running');
    expect(text).not.toMatch(/occt|brep|cadquery|freecad|step/i);
  });

  it('reports only the latest build', () => {
    const older = op({ operation_id: 'job:1', state: 'failed', updated_at: '2026-11-04T10:00:00Z' });
    const newer = op({ operation_id: 'job:2', state: 'completed', updated_at: '2026-11-04T10:05:00Z' });
    expect(exactChecksState([older, newer])).toBeNull();
    expect(exactChecksState({ a: older })).toBe('failed');
    expect(engineeringChecksText('failed')).toMatch(/last checked version is kept/);
    expect(exactChecksState([op({ title: 'Generate concept mesh', domain: 'concept' })])).toBeNull();
  });
});
