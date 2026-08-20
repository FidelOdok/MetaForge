import { describe, it, expect, vi } from 'vitest';

vi.mock('../../client', () => ({
  default: {
    get: vi.fn(),
  },
}));

import apiClient from '../../client';
import { getTwinNodes, getTwinNode, getNodeScript } from '../twin';

const mockGet = vi.mocked(apiClient.get);

const NODE = {
  id: 'n1',
  name: 'Clip Panel',
  type: 'work_product',
  domain: 'mechanical',
  status: 'valid',
  properties: {},
  updatedAt: '2026-06-12T00:00:00Z',
};

describe('getTwinNodes (MET-491 project scoping)', () => {
  it('omits project_id params when no project is selected', async () => {
    mockGet.mockResolvedValueOnce({ data: { nodes: [NODE], total: 1 } });

    const result = await getTwinNodes();

    expect(result).toHaveLength(1);
    // No params object => global (all projects) behaviour preserved.
    expect(mockGet).toHaveBeenCalledWith('/twin/nodes', { params: undefined });
  });

  it('passes project_id as a query param when scoped to a project', async () => {
    mockGet.mockResolvedValueOnce({ data: { nodes: [NODE], total: 1 } });

    await getTwinNodes('f8240b2a-9e01-4b16-83eb-b24cfcd4a04f');

    expect(mockGet).toHaveBeenCalledWith('/twin/nodes', {
      params: { project_id: 'f8240b2a-9e01-4b16-83eb-b24cfcd4a04f' },
    });
  });
});

describe('geometryParameters / hasScript pass-through (MET-630)', () => {
  it('carries geometryParameters and hasScript through getTwinNode', async () => {
    mockGet.mockResolvedValueOnce({
      data: {
        ...NODE,
        geometryParameters: { parameters: { pad_length_mm: 15 }, properties: { volume_mm3: 1234.5 } },
        hasScript: true,
      },
    });

    const node = await getTwinNode('n1');

    expect(node?.geometryParameters?.parameters.pad_length_mm).toBe(15);
    expect(node?.hasScript).toBe(true);
  });

  it('leaves geometryParameters undefined when the backend omits it', async () => {
    mockGet.mockResolvedValueOnce({ data: NODE });

    const node = await getTwinNode('n1');

    expect(node?.geometryParameters).toBeUndefined();
    expect(node?.hasScript).toBeUndefined();
  });
});

describe('getNodeScript', () => {
  it('maps the script response to camelCase', async () => {
    mockGet.mockResolvedValueOnce({
      data: {
        node_id: 'n1',
        script_node_id: 's1',
        script_source: 'pad(15)\n',
        git_commit_sha: 'abc123',
        git_path: 'mechanical/cad_src/bracket.py',
      },
    });

    const script = await getNodeScript('n1');

    expect(script).toEqual({
      nodeId: 'n1',
      scriptNodeId: 's1',
      scriptSource: 'pad(15)\n',
      gitCommitSha: 'abc123',
      gitPath: 'mechanical/cad_src/bracket.py',
    });
  });

  it('returns undefined (not throw) when the node has no script', async () => {
    mockGet.mockRejectedValueOnce(new Error('404'));

    const script = await getNodeScript('n1');

    expect(script).toBeUndefined();
  });
});
