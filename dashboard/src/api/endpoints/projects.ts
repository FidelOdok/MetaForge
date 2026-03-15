import apiClient from '../client';
import type { Project, ProjectWorkProduct } from '../../types/project';

interface ProjectWorkProductRaw {
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
  work_products: ProjectWorkProductRaw[];
  agent_count: number;
  last_updated: string;
  created_at: string;
}

interface ProjectListResponseRaw {
  projects: ProjectResponseRaw[];
  total: number;
}

function mapWorkProduct(raw: ProjectWorkProductRaw): ProjectWorkProduct {
  return {
    id: raw.id,
    name: raw.name,
    type: raw.type as ProjectWorkProduct['type'],
    status: raw.status as ProjectWorkProduct['status'],
    updatedAt: raw.updated_at,
  };
}

function mapProject(raw: ProjectResponseRaw): Project {
  return {
    id: raw.id,
    name: raw.name,
    description: raw.description,
    status: raw.status as Project['status'],
    work_products: raw.work_products.map(mapWorkProduct),
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

export interface CreateProjectPayload {
  name: string;
  description?: string;
  status?: string;
}

export async function createProject(payload: CreateProjectPayload): Promise<Project> {
  const { data } = await apiClient.post<ProjectResponseRaw>('/projects', payload);
  return mapProject(data);
}
