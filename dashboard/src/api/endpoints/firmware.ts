import apiClient from '../client';

export interface FirmwareJointEntry {
  jointName: string;
  jointType: string;
  canId: number;
  limits: { lower?: number; upper?: number } | null;
}

interface FirmwareJointEntryApi {
  joint_name: string;
  joint_type: string;
  can_id: number;
  limits: { lower?: number; upper?: number } | null;
}

function fromApiJoint(j: FirmwareJointEntryApi): FirmwareJointEntry {
  return { jointName: j.joint_name, jointType: j.joint_type, canId: j.can_id, limits: j.limits };
}

export interface CreateFirmwareScaffoldResult {
  pinmapNodeId: string;
  firmwareSourceNodeId: string;
  joints: FirmwareJointEntry[];
}

interface CreateFirmwareScaffoldApiResponse {
  pinmap_node_id: string;
  firmware_source_node_id: string;
  joints: FirmwareJointEntryApi[];
}

export interface CreateFirmwareScaffoldPayload {
  workProductId: string;
  projectId?: string;
}

/** FORGE-276: derive a per-joint CAN node table + a minimal C header
 * scaffold from a work product's real committed assembly.joints, via the
 * same topological sort twin.create_bringup_checklist already derives. */
export async function createFirmwareScaffold(
  payload: CreateFirmwareScaffoldPayload,
): Promise<CreateFirmwareScaffoldResult> {
  const { data } = await apiClient.post<CreateFirmwareScaffoldApiResponse>('/firmware/scaffold', {
    work_product_id: payload.workProductId,
    project_id: payload.projectId,
  });
  return {
    pinmapNodeId: data.pinmap_node_id,
    firmwareSourceNodeId: data.firmware_source_node_id,
    joints: data.joints.map(fromApiJoint),
  };
}
