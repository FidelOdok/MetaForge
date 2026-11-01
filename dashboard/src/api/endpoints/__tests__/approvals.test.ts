import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../client', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

import apiClient from '../../client';
import { decideApproval, getApproval, listApprovals } from '../approvals';
import { makeApproval } from '../../../test/approval-fixtures';

const mockGet = vi.mocked(apiClient.get);
const mockPost = vi.mocked(apiClient.post);
const SURFACE = { headers: { 'X-MetaForge-Surface': 'dashboard' } };

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
});

describe('approvals endpoints', () => {
  it('lists with filters and maps unscoped_count', async () => {
    mockGet.mockResolvedValueOnce({ data: { items: [makeApproval()], unscoped_count: 3 } });
    const list = await listApprovals({ status: 'decided', projectId: 'p1', kind: 'gate' });
    expect(mockGet).toHaveBeenCalledWith('/approvals', {
      params: { status: 'decided', project_id: 'p1', kind: 'gate' },
      ...SURFACE,
    });
    expect(list.unscopedCount).toBe(3);
    expect(list.items).toHaveLength(1);
  });

  it('defaults to pending and omits unset filters', async () => {
    mockGet.mockResolvedValueOnce({ data: { items: [] } });
    const list = await listApprovals();
    expect(mockGet).toHaveBeenCalledWith('/approvals', { params: { status: 'pending' }, ...SURFACE });
    expect(list.unscopedCount).toBe(0);
  });

  it('encodes the colon in ids', async () => {
    mockGet.mockResolvedValueOnce({ data: makeApproval() });
    await getApproval('gate:run_abc');
    expect(mockGet).toHaveBeenCalledWith('/approvals/gate%3Arun_abc', SURFACE);
  });

  it('posts a decision with reason and to_phase', async () => {
    mockPost.mockResolvedValueOnce({ data: makeApproval({ status: 'reworked' }) });
    const out = await decideApproval('gate:run_abc', {
      decision: 'rework',
      reason: 'fix it',
      to_phase: 'requirements',
    });
    expect(mockPost).toHaveBeenCalledWith(
      '/approvals/gate%3Arun_abc/decision',
      { decision: 'rework', reason: 'fix it', to_phase: 'requirements' },
      SURFACE,
    );
    expect(out.status).toBe('reworked');
  });
});
