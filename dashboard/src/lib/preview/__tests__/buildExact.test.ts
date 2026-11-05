import { describe, expect, it } from 'vitest';
import { exactBuildPlan, profileExtent } from '../buildExact';
import { buildPreview } from '../buildPreview';
import { definition } from './fixtures';

const missing = (names: string[], over: Parameters<typeof definition>[0] = {}) =>
  definition({
    buildable: false,
    missing: names,
    parameters: {
      depth: { value: 2, status: 'required_input', unit: 'mm', question: 'How thick should this be?', assumed: true },
      material: { value: null, status: 'required_input', unit: null, question: '', assumed: false },
    },
    ...over,
  });

describe('exactBuildPlan', () => {
  it('is ready with no question when every required input is known', () => {
    expect(exactBuildPlan(definition())).toEqual({ kind: 'ready', values: {} });
  });

  it('asks for the depth first, with choices from the sketch and a field for another value', () => {
    const plan = exactBuildPlan(missing(['material', 'depth']));
    expect(plan.kind).toBe('ask');
    if (plan.kind !== 'ask') return;
    expect(plan.question.parameter).toBe('depth');
    expect(plan.question.prompt).toBe('How thick should this be?');
    // The fixture's profile is 40 x 20.
    expect(plan.question.choices.map((c) => c.value)).toEqual([4, 10, 20]);
    expect(plan.question.input).toMatchObject({ kind: 'number', unit: 'mm' });
  });

  it('asks for the material from the manufacturing notes only, never a made-up default', () => {
    const plan = exactBuildPlan(missing(['material', 'depth']), { depth: 6 });
    expect(plan.kind === 'ask' && plan.question.parameter).toBe('material');
    if (plan.kind !== 'ask') return;
    expect(plan.question.prompt).toBe('What material is it made of?');
    expect(plan.question.choices).toEqual([]);
    expect(plan.question.input.kind).toBe('text');
    const withNotes = exactBuildPlan(missing(['material']), {}, [{ name: 'PETG', stated: true }]);
    expect(withNotes.kind === 'ask' && withNotes.question.choices).toEqual([{ label: 'PETG (from your manufacturing notes)', value: 'PETG' }]);
  });

  it('offers turns for a revolve angle', () => {
    const plan = exactBuildPlan(missing(['angle'], { op: 'revolve', angle: 360, depth: undefined }));
    expect(plan.kind === 'ask' && plan.question.choices.map((c) => c.value)).toEqual([360, 180, 90]);
  });

  it('is ready with exactly the answers once each is given', () => {
    expect(exactBuildPlan(missing(['material', 'depth']), { depth: 6, material: 'PLA', extra: 'x' })).toEqual({
      kind: 'ready',
      values: { depth: 6, material: 'PLA' },
    });
  });

  it('says what must be done first: approve, or place on a plane in millimetres', () => {
    expect(exactBuildPlan(definition({ approved: false }))).toMatchObject({ kind: 'blocked', fix: 'editable' });
    expect(exactBuildPlan(definition({ unit: 'unknown' }))).toMatchObject({ kind: 'blocked', fix: 'plane' });
    expect(exactBuildPlan(definition({ plane_assumed: true }))).toMatchObject({ kind: 'blocked', fix: 'plane' });
    expect(exactBuildPlan(definition({ plane_blocked: 'the face moved' }))).toMatchObject({
      kind: 'blocked',
      message: "The sketch's plane needs attention first: the face moved",
    });
  });

  it('measures the profile extent', () => {
    expect(profileExtent(definition().elements)).toEqual({ width: 40, height: 20 });
    expect(profileExtent([])).toBeNull();
  });
});

describe('the revolve angle gizmo', () => {
  const revolve = (angle: number) =>
    definition({ op: 'revolve', depth: undefined, angle, elements: [{ type: 'rectangle', origin: [10, 0], width: 5, height: 20 }] });

  it('is an arc about the axis, just outside the part, ending at the handle', () => {
    const b = buildPreview(revolve(90));
    const h = b.handles.find((x) => x.kind === 'revolve_angle')!;
    expect(h).toMatchObject({ parameter: 'angle', value: 90, step: 1, min: 1, max: 360 });
    expect(h.arc!.length).toBeGreaterThan(2);
    // Every arc point is the same distance from the axis (the plane's v axis, world Y here).
    const radii = h.arc!.map(([x, , z]) => Math.hypot(x, z));
    for (const r of radii) expect(r).toBeCloseTo(15 * 1.25, 5);
    expect(h.arc![h.arc!.length - 1]).toEqual(h.position);
    // One degree is r * pi / 180 millimetres along the arc.
    expect(h.mmPerUnit).toBeCloseTo((18.75 * Math.PI) / 180, 6);
  });

  it('moves with the angle', () => {
    const half = buildPreview(revolve(180)).handles.find((x) => x.kind === 'revolve_angle')!;
    const quarter = buildPreview(revolve(90)).handles.find((x) => x.kind === 'revolve_angle')!;
    expect(half.position).not.toEqual(quarter.position);
    expect(half.arc!.length).toBeGreaterThan(quarter.arc!.length);
  });
});
