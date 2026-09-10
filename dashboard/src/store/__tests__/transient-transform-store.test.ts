import { describe, it, expect, beforeEach } from 'vitest';
import { useTransientTransform } from '../transient-transform-store';

const reset = () =>
  useTransientTransform.setState({
    selectedGroup: null,
    mode: 'translate',
    delta: [0, 0, 0],
    rotationDelta: [0, 0, 0],
    scaleDelta: [1, 1, 1],
    isDirty: false,
  });

describe('useTransientTransform', () => {
  beforeEach(reset);

  it('selectGroup sets the group and clears any pending deltas', () => {
    const s = useTransientTransform.getState();
    s.selectGroup('motor_group');
    s.setDelta([5, 0, 0]);
    useTransientTransform.getState().selectGroup('bracket_group');
    const st = useTransientTransform.getState();
    expect(st.selectedGroup).toBe('bracket_group');
    expect(st.delta).toEqual([0, 0, 0]);
    expect(st.rotationDelta).toEqual([0, 0, 0]);
    expect(st.scaleDelta).toEqual([1, 1, 1]);
    expect(st.isDirty).toBe(false);
  });

  it('setDelta is a no-op when nothing is selected', () => {
    useTransientTransform.getState().setDelta([1, 2, 3]);
    expect(useTransientTransform.getState().isDirty).toBe(false);
    expect(useTransientTransform.getState().delta).toEqual([0, 0, 0]);
  });

  it('setDelta marks dirty for a non-zero delta, clean for zero', () => {
    useTransientTransform.getState().selectGroup('motor_group');
    useTransientTransform.getState().setDelta([0, 10, 0]);
    expect(useTransientTransform.getState().isDirty).toBe(true);
    expect(useTransientTransform.getState().delta).toEqual([0, 10, 0]);
    useTransientTransform.getState().setDelta([0, 0, 0]);
    expect(useTransientTransform.getState().isDirty).toBe(false);
  });

  it('revert discards all deltas but keeps the selection', () => {
    const s = useTransientTransform.getState();
    s.selectGroup('motor_group');
    s.setDelta([5, 0, 0]);
    useTransientTransform.getState().revert();
    const st = useTransientTransform.getState();
    expect(st.selectedGroup).toBe('motor_group');
    expect(st.delta).toEqual([0, 0, 0]);
    expect(st.rotationDelta).toEqual([0, 0, 0]);
    expect(st.scaleDelta).toEqual([1, 1, 1]);
    expect(st.isDirty).toBe(false);
  });

  it('clearAfterApply resets selection and all deltas', () => {
    const s = useTransientTransform.getState();
    s.selectGroup('motor_group');
    s.setDelta([5, 0, 0]);
    useTransientTransform.getState().clearAfterApply();
    const st = useTransientTransform.getState();
    expect(st.selectedGroup).toBeNull();
    expect(st.delta).toEqual([0, 0, 0]);
    expect(st.rotationDelta).toEqual([0, 0, 0]);
    expect(st.scaleDelta).toEqual([1, 1, 1]);
    expect(st.isDirty).toBe(false);
  });

  it('setMode switches mode and re-derives isDirty from that mode\'s own delta', () => {
    const s = useTransientTransform.getState();
    s.selectGroup('motor_group');
    s.setDelta([5, 0, 0]); // dirty in translate
    s.setMode('rotate');
    expect(useTransientTransform.getState().isDirty).toBe(false); // rotationDelta still zero
    useTransientTransform.getState().setRotationDelta([0.2, 0, 0]);
    expect(useTransientTransform.getState().isDirty).toBe(true);
    useTransientTransform.getState().setMode('translate');
    expect(useTransientTransform.getState().isDirty).toBe(true); // translate delta [5,0,0] still pending
  });

  it('setRotationDelta and setScaleDelta are no-ops when nothing is selected', () => {
    useTransientTransform.getState().setRotationDelta([0.5, 0, 0]);
    useTransientTransform.getState().setScaleDelta([2, 1, 1]);
    const st = useTransientTransform.getState();
    expect(st.rotationDelta).toEqual([0, 0, 0]);
    expect(st.scaleDelta).toEqual([1, 1, 1]);
    expect(st.isDirty).toBe(false);
  });

  it('setScaleDelta marks dirty away from identity, clean back at identity', () => {
    useTransientTransform.getState().selectGroup('motor_group');
    useTransientTransform.getState().setMode('scale');
    useTransientTransform.getState().setScaleDelta([1.5, 1, 1]);
    expect(useTransientTransform.getState().isDirty).toBe(true);
    useTransientTransform.getState().setScaleDelta([1, 1, 1]);
    expect(useTransientTransform.getState().isDirty).toBe(false);
  });
});
