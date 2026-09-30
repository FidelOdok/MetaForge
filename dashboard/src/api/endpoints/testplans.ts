import apiClient from '../client';

export interface TestPlanEntry {
  nodeId: string;
  requirementId: string;
  step: string;
  acceptanceValue: string;
  createdAt?: string;
}

interface TestPlanEntryApiResponse {
  node_id: string;
  requirement_id: string;
  step: string;
  acceptance_value: string;
  created_at?: string;
}

interface TestPlanListApiResponse {
  entries: TestPlanEntryApiResponse[];
}

interface GenerateTestPlanApiResponse {
  project_id: string;
  entries: TestPlanEntryApiResponse[];
}

function fromApi(e: TestPlanEntryApiResponse): TestPlanEntry {
  return {
    nodeId: e.node_id,
    requirementId: e.requirement_id,
    step: e.step,
    acceptanceValue: e.acceptance_value,
    createdAt: e.created_at,
  };
}

/** FORGE-298: list a project's generated test-plan entries (oldest first).
 * Empty (never an error) for a project with no entries yet. */
export async function listTestPlan(projectId?: string): Promise<TestPlanEntry[]> {
  if (!projectId) return [];
  try {
    const { data } = await apiClient.get<TestPlanListApiResponse>('/testplans', {
      params: { project_id: projectId },
    });
    return data.entries.map(fromApi);
  } catch {
    return [];
  }
}

/** FORGE-298: generate one verification_case per real requirement on the
 * project with verification_method == "test", mechanically derived from
 * the requirement's own metric/operator/limit/unit/target_node_type
 * fields. */
export async function generateTestPlan(projectId: string): Promise<TestPlanEntry[]> {
  const { data } = await apiClient.post<GenerateTestPlanApiResponse>('/testplans', {
    project_id: projectId,
  });
  return data.entries.map(fromApi);
}
