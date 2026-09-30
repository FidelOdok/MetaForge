import apiClient from '../client';

export interface BringupStep {
  stepNumber: number;
  jointName: string;
  jointType: string;
  base: string;
  follower: string;
  instruction: string;
}

interface BringupStepApi {
  step_number: number;
  joint_name: string;
  joint_type: string;
  base: string;
  follower: string;
  instruction: string;
}

function fromApiStep(s: BringupStepApi): BringupStep {
  return {
    stepNumber: s.step_number,
    jointName: s.joint_name,
    jointType: s.joint_type,
    base: s.base,
    follower: s.follower,
    instruction: s.instruction,
  };
}

export interface CreateBringupChecklistResult {
  nodeId: string;
  statement: string;
  steps: BringupStep[];
}

interface CreateBringupChecklistApiResponse {
  node_id: string;
  statement: string;
  steps: BringupStepApi[];
}

export interface CreateBringupChecklistPayload {
  workProductId: string;
  projectId?: string;
}

/** FORGE-295: derive and record a step-by-step assembly checklist from a
 * work product's real committed assembly.joints, via a topological sort of
 * the base->follower dependency graph. */
export async function createBringupChecklist(
  payload: CreateBringupChecklistPayload,
): Promise<CreateBringupChecklistResult> {
  const { data } = await apiClient.post<CreateBringupChecklistApiResponse>('/bringup', {
    work_product_id: payload.workProductId,
    project_id: payload.projectId,
  });
  return { nodeId: data.node_id, statement: data.statement, steps: data.steps.map(fromApiStep) };
}

export interface BringupChecklistListEntry {
  nodeId: string;
  createdAt: string;
  title: string | null;
  statement: string;
  steps: BringupStep[];
}

interface BringupChecklistListEntryApi {
  node_id: string;
  created_at: string;
  title: string | null;
  statement: string;
  steps: BringupStepApi[];
}

interface BringupChecklistListApiResponse {
  entries: BringupChecklistListEntryApi[];
}

/** FORGE-295: list a work product's previously-generated bring-up
 * checklists, oldest first. */
export async function listBringupChecklists(
  workProductId: string,
): Promise<BringupChecklistListEntry[]> {
  const { data } = await apiClient.get<BringupChecklistListApiResponse>('/bringup', {
    params: { work_product_id: workProductId },
  });
  return data.entries.map((e) => ({
    nodeId: e.node_id,
    createdAt: e.created_at,
    title: e.title,
    statement: e.statement,
    steps: e.steps.map(fromApiStep),
  }));
}
