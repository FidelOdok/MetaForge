import apiClient from '../client';
import type { ImagineStudio } from '../../types/imagine';

/**
 * The component thread (FORGE-903): every component followed through Imagine,
 * Develop, Check and Make. `GET /v1/twin/projects/{id}/components/thread`, and
 * `POST .../imagine/adopt` to take Develop's part into the studio.
 */

export type ThreadRelation = 'same' | 'refined' | 'replaced' | 'develop_only' | 'imagine_only';

export interface ThreadLibrary {
  id?: string;
  name?: string;
  shared?: boolean;
}

export interface ThreadSelection {
  node_id: string;
  mpn: string;
  manufacturer: string;
  role: string | null;
  selection: string;
  library: ThreadLibrary | null;
}

export interface ThreadLine {
  record: string;
  kind: 'bom' | 'procurement';
  part: string;
  quantity: number;
  refs: string[];
}

export interface ComponentThread {
  key: string;
  name: string;
  role: string;
  relation: ThreadRelation;
  /** `board`: a part Develop put on a board it designed; Develop's alone. */
  level: 'concept' | 'board';
  imagine: {
    status: 'chosen' | 'candidate' | 'absent';
    part_id?: string;
    name?: string;
    mpn?: string;
    manufacturer?: string;
    library?: string | null;
    component_id?: string | null;
    revision?: number;
    handed_over?: boolean;
  };
  develop: {
    status: 'selected' | 'candidate' | 'on_board' | 'designed' | 'absent';
    linked_by: 'stored' | 'mpn' | 'role' | null;
    selections: ThreadSelection[];
    boards: Array<{ board: string; ref: string | null }>;
  };
  check: {
    status: 'pass' | 'violates' | 'unchecked' | 'absent';
    violates: string[];
    margins: number;
    unchecked: number;
  };
  make: {
    status: 'in_bom' | 'ordered' | 'not_in_bom' | 'absent';
    lines: ThreadLine[];
  };
  adopt: {
    kind: 'part';
    node_id: string;
    mpn: string;
    manufacturer: string;
    library: string | null;
    needs_rework: boolean;
  } | null;
}

export interface ComponentThreads {
  project_id: string;
  studio: { status: string; revision: number } | null;
  threads: ComponentThread[];
  summary: Record<ThreadRelation, number>;
  warnings: string[];
}

const enc = encodeURIComponent;

export const componentKeys = {
  threads: (projectId: string) => ['components', projectId, 'thread'] as const,
};

export async function getComponentThreads(projectId: string, signal?: AbortSignal): Promise<ComponentThreads> {
  const { data } = await apiClient.get<ComponentThreads>(`/twin/projects/${enc(projectId)}/components/thread`, {
    signal,
  });
  return data;
}

export async function adoptComponent(
  projectId: string,
  baseRevision: number,
  key: string,
): Promise<ImagineStudio> {
  const { data } = await apiClient.post<ImagineStudio>(`/twin/projects/${enc(projectId)}/imagine/adopt`, {
    base_revision: baseRevision,
    key,
  });
  return data;
}
