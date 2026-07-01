import { describe, it, expect, vi } from 'vitest';

vi.mock('../../client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import apiClient from '../../client';
import { createRun, getRun, listRuns, submitRunApproval } from '../runs';

const mockGet = vi.mocked(apiClient.get);
const mockPost = vi.mocked(apiClient.post);

const RAW = {
  id: 'run_1',
  status: 'running',
  request: { goal: 'x' },
  created_at: 1,
  updated_at: 2,
  error: null,
  approval_reason: null,
  result: null,
  history: ['queued', 'running'],
};

describe('listRuns', () => {
  it('maps snake_case to camelCase', async () => {
    mockGet.mockResolvedValueOnce({ data: { runs: [RAW] } });
    const runs = await listRuns();
    expect(runs[0]?.id).toBe('run_1');
    expect(runs[0]?.createdAt).toBe(1);
    expect(runs[0]?.approvalReason).toBeUndefined();
    expect(runs[0]?.history).toEqual(['queued', 'running']);
  });
});

describe('getRun', () => {
  it('returns undefined on error', async () => {
    mockGet.mockRejectedValueOnce(new Error('404'));
    expect(await getRun('nope')).toBeUndefined();
  });
});

describe('createRun', () => {
  it('posts request + start and maps the result', async () => {
    mockPost.mockResolvedValueOnce({ data: RAW });
    const run = await createRun({ goal: 'x' }, true);
    expect(mockPost).toHaveBeenCalledWith('/runs', { request: { goal: 'x' }, start: true });
    expect(run.status).toBe('running');
  });
});

describe('submitRunApproval', () => {
  it('posts the decision to the approval endpoint', async () => {
    mockPost.mockResolvedValueOnce({ data: { ...RAW, status: 'rejected' } });
    const run = await submitRunApproval('run_1', 'reject');
    expect(mockPost).toHaveBeenCalledWith('/runs/run_1/approval', { decision: 'reject' });
    expect(run.status).toBe('rejected');
  });
});
