import { describe, expect, it } from 'vitest';
import {
  STATUS_ATTENTION,
  STATUS_EXACT,
  STATUS_PENDING,
  STATUS_PREVIEW,
  assumptionNotes,
  parametricDisplay,
} from '../display';
import { definition, validatedCad } from './fixtures';

const idle = { dirty: false, committing: false, commitError: null };

/** SYNC-005, SYNC-006, SYNC-009 and 3D-RC-006, as the stage words them (section 10). */
describe('parametricDisplay', () => {
  it('shows the provisional preview until an exact revision of this definition exists', () => {
    const d = parametricDisplay(definition(), idle);
    expect(d).toMatchObject({ show: 'l2', status: STATUS_PREVIEW, maturity: 'Preview', attention: false });
  });

  it('says the engineering update is pending while the exact build runs', () => {
    const d = parametricDisplay(definition({ build: { job_id: 'j1', status: 'running' } }), idle);
    expect(d).toMatchObject({ show: 'l2', status: STATUS_PREVIEW, secondary: STATUS_PENDING, pending: true });
  });

  it('swaps to the validated L3 model built from this definition', () => {
    const d = parametricDisplay(definition({ cad: validatedCad('hash-5'), build: { job_id: 'j1', status: 'succeeded' } }), idle);
    expect(d).toMatchObject({ show: 'l3', status: STATUS_EXACT, l3NodeId: 'cad-node-2', l3Ref: 'CAD-SLOT@2', maturity: 'Validated' });
    expect(d.notes).toEqual([]);
  });

  it('does not swap to a validated model of another definition, but keeps it underneath', () => {
    const cad = validatedCad('hash-4', { matches_definition: false });
    const d = parametricDisplay(definition({ cad }), idle);
    expect(d).toMatchObject({ show: 'l2', status: STATUS_PREVIEW, l3NodeId: 'cad-node-2' });
  });

  it('notes a discrepancy in plain words after the swap', () => {
    const cad = validatedCad('hash-5', {
      comparison: {
        outcome: 'discrepancy',
        checks: [],
        discrepancies: [
          { check: 'volume', outcome: 'fail', detail: '' },
          { check: 'features', outcome: 'fail', detail: '' },
        ],
        tolerance: { abs_mm: 0.25, rel: 0.01, volume_rel: 0.02, linear_mm: 0.5, basis: '', source: 'default' },
        preview: { op: 'extrude', definition_hash: 'hash-5', volume_mm3: 1, intended_through_holes: 1 },
        compared_at: '',
      },
    });
    const d = parametricDisplay(definition({ cad }), idle);
    expect(d.status).toBe(STATUS_EXACT);
    expect(d.notes).toEqual(['The exact model differs from the preview in volume, holes.']);
  });

  it('on a failed build keeps the preview and the last valid model, offers a retry and never claims exact', () => {
    const cad = validatedCad('hash-4', { matches_definition: false, last_validated: { revision: 1, node_id: 'cad-node-1', item_ref: 'CAD-SLOT@1', is_head: true } });
    const d = parametricDisplay(definition({ cad, build: { job_id: 'j9', status: 'failed', operation_id: 'job:j9' } }), idle);
    expect(d).toMatchObject({
      show: 'l2',
      status: STATUS_ATTENTION,
      attention: true,
      l3NodeId: 'cad-node-1',
      secondary: 'Last valid model kept: CAD-SLOT@1',
      retryOperation: 'job:j9',
      maturity: 'Preview',
    });
    expect(d.status).not.toMatch(/exact/i);
  });

  it('a local edit always shows the provisional preview first', () => {
    const d = parametricDisplay(definition({ cad: validatedCad('hash-5') }), { ...idle, dirty: true });
    expect(d).toMatchObject({ show: 'l2', status: STATUS_PREVIEW });
  });

  it('labels what the preview assumed', () => {
    const notes = assumptionNotes(
      definition({
        unit: 'unknown',
        scale_assumed: true,
        plane_assumed: true,
        parameters: { depth: { value: 4, status: 'required_input', unit: 'unknown', question: '', assumed: true } },
      }),
    );
    expect(notes).toEqual([
      'Depth not set · showing an assumed 4',
      'Scale not confirmed · drawn at an assumed 1 sketch unit = 1 mm',
      'No plane placed · drawn on XY (assumed)',
    ]);
  });
});
