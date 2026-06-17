import { describe, it, expect, beforeEach } from 'vitest';
import { useTransientTransform } from '../transient-transform-store';

const reset = () =>
  useTransientTransform.setState({ selectedGroup: null, delta: [0, 0, 0], isDirty: false });

describe('useTransientTransform', () => {
  beforeEach(reset);

  it('selectGroup sets the group and clears any pending delta', () => {
    const s = useTransientTransform.getState();
    s.selectGroup('motor_group');
    s.setDelta([5, 0, 0]);
    useTransientTransform.getState().selectGroup('bracket_group');
    const st = useTransientTransform.getState();
    expect(st.selectedGroup).toBe('bracket_group');
    expect(st.delta).toEqual([0, 0, 0]);
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

  it('revert discards the delta but keeps the selection', () => {
    const s = useTransientTransform.getState();
    s.selectGroup('motor_group');
    s.setDelta([5, 0, 0]);
    useTransientTransform.getState().revert();
    const st = useTransientTransform.getState();
    expect(st.selectedGroup).toBe('motor_group');
    expect(st.delta).toEqual([0, 0, 0]);
    expect(st.isDirty).toBe(false);
  });

  it('clearAfterApply resets selection and delta', () => {
    const s = useTransientTransform.getState();
    s.selectGroup('motor_group');
    s.setDelta([5, 0, 0]);
    useTransientTransform.getState().clearAfterApply();
    const st = useTransientTransform.getState();
    expect(st.selectedGroup).toBeNull();
    expect(st.delta).toEqual([0, 0, 0]);
    expect(st.isDirty).toBe(false);
  });
});
