import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '../../test/test-utils';
import { opSnapshot } from '../../test/operation-fixtures';
import type { OperationView } from '../../lib/operations-reducer';

const cancelOperation = vi.fn();
const retryOperation = vi.fn();
vi.mock('../../api/endpoints/operations', () => ({
  cancelOperation: (...args: unknown[]) => cancelOperation(...args),
  retryOperation: (...args: unknown[]) => retryOperation(...args),
}));

const navigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigate };
});

import { useOperationActions } from '../use-operation-actions';

function view(over: Partial<OperationView> = {}): OperationView {
  return { ...opSnapshot(), milestones: [], log: [], ...over } as OperationView;
}

beforeEach(() => {
  cancelOperation.mockReset();
  retryOperation.mockReset();
  navigate.mockReset();
});

describe('useOperationActions', () => {
  it('sends cancel and retry to the gateway for the operation', async () => {
    cancelOperation.mockResolvedValue({ operation_id: 'run:a', state: 'cancelled' });
    retryOperation.mockResolvedValue({ operation_id: 'run:b', retry_of: 'run:a', state: 'queued' });
    const { result } = renderHook(() => useOperationActions('p1'));
    const op = view({ operation_id: 'run:a' });
    await result.current(op, { kind: 'cancel', id: 'run:a', label: 'Cancel' });
    expect(cancelOperation).toHaveBeenCalledWith('p1', 'run:a');
    await expect(result.current(op, { kind: 'retry', id: 'run:a', label: 'Retry' })).resolves.toMatchObject({
      operation_id: 'run:b',
      retry_of: 'run:a',
    });
    expect(retryOperation).toHaveBeenCalledWith('p1', 'run:a');
  });

  it('opens a named path, else the run', async () => {
    const { result } = renderHook(() => useOperationActions('p1'));
    await result.current(view({ run_id: 'r1' }), { kind: 'open', id: '/projects/p1', label: 'Open' });
    expect(navigate).toHaveBeenCalledWith('/projects/p1');
    await result.current(view({ run_id: 'r1' }), { kind: 'open', id: 'x', label: 'Open' });
    expect(navigate).toHaveBeenLastCalledWith('/runs/r1');
  });

  it('passes a refusal back to the caller', async () => {
    retryOperation.mockRejectedValue(new Error('409'));
    const { result } = renderHook(() => useOperationActions('p1'));
    await expect(result.current(view(), { kind: 'retry', id: 'run:a', label: 'Retry' })).rejects.toThrow('409');
  });
});
