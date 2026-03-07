import apiClient from '../client';
import type { Project, ProjectArtifact } from '../../types/project';

interface ProjectArtifactRaw {
  id: string;
  name: string;
  type: string;
  status: string;
  updated_at: string;
}

interface ProjectResponseRaw {
  id: string;
  name: string;
  description: string;
  status: string;
  artifacts: ProjectArtifactRaw[];
  agent_count: number;
  last_updated: string;
  created_at: string;
}

interface ProjectListResponseRaw {
  projects: ProjectResponseRaw[];
  total: number;
}

function mapArtifact(raw: ProjectArtifactRaw): ProjectArtifact {
  return {
    id: raw.id,
    name: raw.name,
    type: raw.type as ProjectArtifact['type'],
    status: raw.status as ProjectArtifact['status'],
    updatedAt: raw.updated_at,
  };
}

function mapProject(raw: ProjectResponseRaw): Project {
  return {
    id: raw.id,
    name: raw.name,
    description: raw.description,
    status: raw.status as Project['status'],
    artifacts: raw.artifacts.map(mapArtifact),
    agentCount: raw.agent_count,
    lastUpdated: raw.last_updated,
    createdAt: raw.created_at,
  };
}

export async function getProjects(): Promise<Project[]> {
  const { data } = await apiClient.get<ProjectListResponseRaw>('/projects');
  return data.projects.map(mapProject);
}

export async function getProject(id: string): Promise<Project | undefined> {
  try {
    const { data } = await apiClient.get<ProjectResponseRaw>(`/projects/${id}`);
    return mapProject(data);
  } catch {
    return undefined;
  }
}
