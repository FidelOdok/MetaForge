import apiClient from '../client';
import type { SelectComponentPayload, SelectComponentResult } from '../../types/component-selection';

export async function selectComponent(
  payload: SelectComponentPayload,
): Promise<SelectComponentResult> {
  const { data } = await apiClient.post<SelectComponentResult>(
    '/component-selection/select',
    payload,
  );
  return data;
}
