import { describe, it, expect, vi } from 'vitest';

vi.mock('../../client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import apiClient from '../../client';
import { getChecklist, getCoverage, getEvidenceForItem, linkEvidence } from '../compliance';

const mockGet = vi.mocked(apiClient.get);
const mockPost = vi.mocked(apiClient.post);

const CHECKLIST = {
  project_id: 'p1',
  target_markets: ['UKCA', 'CE'],
  total_items: 2,
  evidenced_items: 1,
  coverage_percent: 50,
  items: [
    {
      id: 'UKCA-SAF-001',
      regime: 'UKCA',
      category: 'safety',
      requirement: 'Enclosure rated for creepage/clearance',
      standard: 'EN 62368-1:2020',
      evidence_type: 'TEST_REPORT',
      evidence_status: 'MISSING',
      evidence_work_product_id: null,
      notes: '',
    },
  ],
};

describe('getChecklist', () => {
  it('fetches without a markets param when none is given', async () => {
    mockGet.mockResolvedValueOnce({ data: CHECKLIST });
    const result = await getChecklist('p1');
    expect(mockGet).toHaveBeenCalledWith('/compliance/p1/checklist', { params: {} });
    expect(result.total_items).toBe(2);
  });

  it('joins markets into a comma-separated query param', async () => {
    mockGet.mockResolvedValueOnce({ data: CHECKLIST });
    await getChecklist('p1', ['UKCA', 'CE', 'FCC']);
    expect(mockGet).toHaveBeenCalledWith('/compliance/p1/checklist', {
      params: { markets: 'UKCA,CE,FCC' },
    });
  });
});

describe('getCoverage', () => {
  it('hits the coverage endpoint for the project', async () => {
    mockGet.mockResolvedValueOnce({ data: { project_id: 'p1', total_items: 2, evidenced_items: 1, coverage_percent: 50 } });
    const coverage = await getCoverage('p1');
    expect(mockGet).toHaveBeenCalledWith('/compliance/p1/coverage');
    expect(coverage.coverage_percent).toBe(50);
  });
});

describe('linkEvidence', () => {
  it('posts the evidence payload to the project', async () => {
    const evidence = {
      id: 'e1',
      checklist_item_id: 'UKCA-SAF-001',
      evidence_type: 'TEST_REPORT',
      status: 'UPLOADED',
      title: 'Enclosure test report',
      description: '',
      uploaded_at: new Date().toISOString(),
    };
    mockPost.mockResolvedValueOnce({ data: evidence });
    const result = await linkEvidence('p1', {
      checklist_item_id: 'UKCA-SAF-001',
      evidence_type: 'TEST_REPORT',
      title: 'Enclosure test report',
    });
    expect(mockPost).toHaveBeenCalledWith('/compliance/p1/evidence', {
      checklist_item_id: 'UKCA-SAF-001',
      evidence_type: 'TEST_REPORT',
      title: 'Enclosure test report',
    });
    expect(result.id).toBe('e1');
  });
});

describe('getEvidenceForItem', () => {
  it('fetches evidence records for a checklist item', async () => {
    mockGet.mockResolvedValueOnce({ data: [] });
    const result = await getEvidenceForItem('p1', 'UKCA-SAF-001');
    expect(mockGet).toHaveBeenCalledWith('/compliance/p1/evidence/UKCA-SAF-001');
    expect(result).toEqual([]);
  });
});
