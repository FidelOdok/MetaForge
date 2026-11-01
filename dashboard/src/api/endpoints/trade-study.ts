import apiClient from '../client';
import type {
  AddConceptOptionPayload,
  AddConceptOptionResult,
  ConceptOption,
  SelectConceptPayload,
  SelectConceptResult,
} from '../../types/trade-study';

export async function getConceptOptions(projectId?: string): Promise<ConceptOption[]> {
  try {
    const params = projectId ? { project_id: projectId } : {};
    const { data } = await apiClient.get<{ options: ConceptOption[] }>('/trade-study/options', {
      params,
    });
    return data.options;
  } catch {
    return [];
  }
}

export async function addConceptOption(
  payload: AddConceptOptionPayload,
): Promise<AddConceptOptionResult> {
  const { data } = await apiClient.post<AddConceptOptionResult>('/trade-study/options', payload);
  return data;
}

export async function selectConcept(
  payload: SelectConceptPayload,
): Promise<SelectConceptResult> {
  const { data } = await apiClient.post<SelectConceptResult>('/trade-study/select', payload);
  return data;
}
