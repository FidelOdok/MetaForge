import apiClient from '../client';

export interface ReleaseSnapshot {
  hierarchyNodeIds: string[];
  bomItemIds: string[];
  evidenceIds: string[];
  decisionIds: string[];
  drawingIds: string[];
}

export interface ReleaseDiff {
  comparedTo: string | null;
  hierarchyDelta: number;
  bomDelta: number;
  evidenceDelta: number;
  decisionDelta: number;
}

export interface ReleasePackage {
  nodeId: string;
  createdAt: string | null;
  title: string | null;
  statement: string | null;
  snapshot: ReleaseSnapshot;
  diffFromPrevious: ReleaseDiff;
  gateStatus: string;
}

interface ReleaseSnapshotApi {
  hierarchy_node_ids: string[];
  bom_item_ids: string[];
  evidence_ids: string[];
  decision_ids: string[];
  drawing_ids: string[];
}

interface ReleaseDiffApi {
  compared_to: string | null;
  hierarchy_delta: number;
  bom_delta: number;
  evidence_delta: number;
  decision_delta: number;
}

interface ReleasePackageApiResponse {
  node_id: string;
  created_at: string | null;
  title: string | null;
  statement: string | null;
  snapshot: ReleaseSnapshotApi;
  diff_from_previous: ReleaseDiffApi;
  gate_status: string;
}

interface ReleasePackageListApiResponse {
  releases: ReleasePackageApiResponse[];
}

function fromApi(r: ReleasePackageApiResponse): ReleasePackage {
  return {
    nodeId: r.node_id,
    createdAt: r.created_at,
    title: r.title,
    statement: r.statement,
    snapshot: {
      hierarchyNodeIds: r.snapshot.hierarchy_node_ids,
      bomItemIds: r.snapshot.bom_item_ids,
      evidenceIds: r.snapshot.evidence_ids,
      decisionIds: r.snapshot.decision_ids,
      drawingIds: r.snapshot.drawing_ids,
    },
    diffFromPrevious: {
      comparedTo: r.diff_from_previous.compared_to,
      hierarchyDelta: r.diff_from_previous.hierarchy_delta,
      bomDelta: r.diff_from_previous.bom_delta,
      evidenceDelta: r.diff_from_previous.evidence_delta,
      decisionDelta: r.diff_from_previous.decision_delta,
    },
    gateStatus: r.gate_status,
  };
}

/** FORGE-299: list a project's release packages (oldest first), each
 * carrying its own already-computed diff against the immediately-prior
 * package. Empty (never an error) for a project with no releases yet. */
export async function listReleasePackages(projectId?: string): Promise<ReleasePackage[]> {
  if (!projectId) return [];
  try {
    const { data } = await apiClient.get<ReleasePackageListApiResponse>('/releases', {
      params: { project_id: projectId },
    });
    return data.releases.map(fromApi);
  } catch {
    return [];
  }
}

/** FORGE-299: create a new release package -- gated server-side on the G8
 * release gate returning PASSED; a 409 means the project isn't
 * release-ready yet (the gate's own failing checks are in the error
 * detail, surfaced by the caller). */
export async function createReleasePackage(
  projectId: string,
  notes?: string,
): Promise<ReleasePackage> {
  const { data } = await apiClient.post<ReleasePackageApiResponse>('/releases', {
    project_id: projectId,
    notes,
  });
  return fromApi(data);
}
